"""Synthetic technical fixtures exercise ZIP/7z handling, not market parsing."""

import io
import logging
import stat
import threading
import zipfile
from collections import namedtuple
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import py7zr
import pytest
import requests

from app import cli
from app.discovery import Archive, discover_archives, parse_listing
from app.downloader import download_archive
from app.errors import CollectorError
from app.extractor import MANIFEST, extract_archive, safe_relative_path, validate_archive
from app.http import create_session
from app.markets import MARKET_URLS, Market


def make_archive(path: Path, kind: str, content: bytes = b"test fixture\n") -> Path:
    if kind == "zip":
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("nested/example.txt", content)
    else:
        with py7zr.SevenZipFile(path, "w") as archive:
            archive.writestr(content, "nested/example.txt")
    return path


def test_listing_recognizes_real_iis_links_and_cyrillic_types():
    html = '''
    <a href="/pub/info/data/">Parent</a>
    <a href="OrderLog20241001_B.zip">B</a>
    <a href="OrderLog20241001_B.zip">duplicate</a>
    <a href="OrderLog20241001_%D0%90.zip">Cyrillic A</a>
    <a href="OrderLog20241002_В.7z">Cyrillic B</a>
    <a href="OrderLog20240230_A.zip">Invalid date</a>
    <a href="../OrderLog20241001_A.zip">Outside directory</a>
    <a href="https://example.org/OrderLog20241001_A.zip">External</a>
    <a href="http://ftp.moex.com/pub/info/data/Currencies/OrderLog20241001_A.zip">Downgrade</a>
    <a href="sub/OrderLog20241001_A.zip">Subdirectory</a>
    '''
    items = parse_listing(html, Market.CURRENCIES, MARKET_URLS[Market.CURRENCIES])
    assert [(item.trade_date, item.archive_type, item.archive_format) for item in items] == [
        (date(2024, 10, 1), "A", "zip"),
        (date(2024, 10, 1), "B", "zip"),
        (date(2024, 10, 2), "B", "7z"),
    ]
    assert items[0].filename == "OrderLog20241001_А.zip"
    assert "%D0%90" in items[0].source_url


@pytest.mark.parametrize("kind", ["zip", "7z"])
def test_extract_valid_archive_and_reuse_only_verified_output(tmp_path, kind):
    path = make_archive(tmp_path / f"source.{kind}", kind)
    output = tmp_path / "output"
    validate_archive(path)
    assert extract_archive(path, output) == output
    assert (output / "nested/example.txt").read_bytes() == b"test fixture\n"
    marker_time = (output / MANIFEST).stat().st_mtime_ns
    extract_archive(path, output)
    assert (output / MANIFEST).stat().st_mtime_ns == marker_time

    (output / "nested/example.txt").write_bytes(b"user changes\n")
    with pytest.raises(CollectorError, match="existing files were preserved"):
        extract_archive(path, output)
    assert (output / "nested/example.txt").read_bytes() == b"user changes\n"


@pytest.mark.parametrize("kind", ["zip", "7z"])
def test_corrupt_archive_leaves_no_published_or_partial_output(tmp_path, kind):
    path = make_archive(tmp_path / f"source.{kind}", kind)
    content = bytearray(path.read_bytes())
    if kind == "zip":
        content[content.index(b"test fixture")] ^= 0xFF
    else:
        content[32] ^= 0xFF  # Corrupt the packed stream, keeping the 7z signature.
    path.write_bytes(content)
    with pytest.raises(CollectorError):
        validate_archive(path)
    with pytest.raises(CollectorError):
        extract_archive(path, tmp_path / "output")
    assert not (tmp_path / "output").exists()
    assert not list(tmp_path.glob("*.part"))


@pytest.mark.parametrize("name", [
    "../outside.txt", "..\\outside.txt", "/absolute.txt", "C:/absolute.txt",
    "C:relative.txt", "//server/share.txt", "file:stream", "NUL.txt",
    "nested/../outside.txt", "nested/file. ", "nested//file.txt",
])
def test_reject_unsafe_windows_member_paths(name):
    with pytest.raises(CollectorError):
        safe_relative_path(name)


@pytest.mark.parametrize("name", ["../outside.txt", "nested/../../outside.txt"])
def test_traversal_is_rejected_before_zip_extraction(tmp_path, name):
    path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("valid.txt", b"fixture")
        archive.writestr(name, b"unsafe")
    with pytest.raises(CollectorError, match="Unsafe"):
        extract_archive(path, tmp_path / "output")
    assert not (tmp_path / "output").exists()
    assert not (tmp_path / "outside.txt").exists()


def test_zip_symlinks_are_rejected(tmp_path):
    path = tmp_path / "link.zip"
    member = zipfile.ZipInfo("link")
    member.create_system = 3
    member.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(member, "../outside")
    with pytest.raises(CollectorError, match="Links"):
        extract_archive(path, tmp_path / "output")


def test_zip_case_collisions_are_rejected(tmp_path):
    path = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("same.txt", "first")
        archive.writestr("SAME.txt", "second")
    with pytest.raises(CollectorError, match="Duplicate"):
        extract_archive(path, tmp_path / "output")


@pytest.fixture
def archive_server():
    """A real local HTTP endpoint for streaming, statuses and disconnects."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("example.txt", b"fixture\n" * 100)
    payload = buffer.getvalue()
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            if self.path == "/market/":
                body = b'<a href="OrderLog20241001_B.zip">archive</a>'
            elif self.path.endswith(".zip"):
                body = payload
            elif self.path == "/missing":
                self.send_error(404)
                return
            elif self.path == "/corrupt":
                body = b"this is not a zip archive"
            else:
                body = payload[:20]
            self.send_response(200)
            expected = len(payload) if self.path == "/truncated" else len(body)
            self.send_header("Content-Length", str(expected))
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", payload, hits
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def archive_at(url):
    return Archive(
        Market.CURRENCIES, date(2024, 10, 1), "B", "OrderLog20241001_B.zip", url, "zip",
    )


def test_stream_download_verifies_cache_and_replaces_corruption(archive_server, tmp_path):
    base, payload, hits = archive_server
    archive = archive_at(base + "/archive.zip")
    with create_session() as session:
        destination = download_archive(archive, tmp_path, session)
        assert destination.read_bytes() == payload
        download_archive(archive, tmp_path, session)
        assert hits == ["/archive.zip"]
        destination.write_bytes(b"damaged local cache")
        download_archive(archive, tmp_path, session)
    assert destination.read_bytes() == payload
    assert hits == ["/archive.zip", "/archive.zip"]
    assert not list(tmp_path.rglob("*.part"))


@pytest.mark.parametrize("endpoint", ["/truncated", "/corrupt", "/missing"])
def test_failed_download_is_not_published(archive_server, tmp_path, endpoint):
    base, _, _ = archive_server
    with create_session() as session:
        with pytest.raises((requests.RequestException, CollectorError)):
            download_archive(archive_at(base + endpoint), tmp_path, session)
    assert not (tmp_path / "currencies/OrderLog20241001_B.zip").exists()
    assert not list(tmp_path.rglob("*.part"))


def test_cli_download_end_to_end(archive_server, tmp_path, monkeypatch, capsys):
    base, payload, _ = archive_server
    monkeypatch.setitem(MARKET_URLS, Market.CURRENCIES, base + "/market/")
    command = [
        "download", "--market", "currencies", "--date", "2024-10-01", "--type", "B",
        "--downloads-dir", str(tmp_path / "downloads"),
        "--extracted-dir", str(tmp_path / "extracted"),
    ]
    assert cli.main(command) == 0
    assert cli.main(command) == 0
    destination = tmp_path / "downloads/currencies/OrderLog20241001_B.zip"
    assert destination.read_bytes() == payload
    output = tmp_path / "extracted/currencies/OrderLog20241001_B/example.txt"
    assert output.read_bytes() == b"fixture\n" * 100
    assert "Extracted:" in capsys.readouterr().out


def test_unavailable_date_does_not_download(archive_server, tmp_path, monkeypatch, caplog):
    base, _, hits = archive_server
    monkeypatch.setitem(MARKET_URLS, Market.BONDS, base + "/market/")
    with caplog.at_level(logging.ERROR):
        result = cli.main([
            "download", "--market", "bonds", "--date", "2024-10-02", "--type", "A",
            "--downloads-dir", str(tmp_path),
        ])
    assert result == 1
    assert "Available dates: 2024-10-01" in caplog.text
    assert hits == ["/market/"]


def test_discovery_http_errors_are_not_treated_as_empty_listing(archive_server, monkeypatch):
    base, _, _ = archive_server
    monkeypatch.setitem(MARKET_URLS, Market.BONDS, base + "/missing")
    with create_session() as session, pytest.raises(requests.HTTPError):
        discover_archives(Market.BONDS, session)


def test_disk_space_error_does_not_publish_output(tmp_path, monkeypatch):
    path = make_archive(tmp_path / "source.zip", "zip")
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr("app.extractor.shutil.disk_usage", lambda _: usage(10, 10, 0))
    with pytest.raises(CollectorError, match="disk space"):
        extract_archive(path, tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_download_only_does_not_extract(archive_server, tmp_path, monkeypatch):
    base, _, _ = archive_server
    monkeypatch.setitem(MARKET_URLS, Market.CURRENCIES, base + "/market/")
    output = tmp_path / "extracted"
    assert cli.main([
        "download", "--market", "currencies", "--date", "2024-10-01", "--type", "B",
        "--downloads-dir", str(tmp_path / "downloads"),
        "--extracted-dir", str(output), "--download-only",
    ]) == 0
    assert not output.exists()


def test_interruption_cleans_partial_download(archive_server, tmp_path, monkeypatch):
    base, _, _ = archive_server

    def interrupted_stream(self, *args, **kwargs):
        yield b"partial response"
        raise KeyboardInterrupt

    monkeypatch.setattr(requests.Response, "iter_content", interrupted_stream)
    with create_session() as session, pytest.raises(KeyboardInterrupt):
        download_archive(archive_at(base + "/archive.zip"), tmp_path, session)
    assert not list(tmp_path.rglob("*.zip"))
    assert not list(tmp_path.rglob("*.part"))
