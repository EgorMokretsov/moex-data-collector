"""Validate archives and extract regular files into a fresh staging directory."""

import hashlib
import json
import logging
import re
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

import py7zr

from app.errors import CollectorError

LOGGER = logging.getLogger(__name__)
MANIFEST = ".moex-extraction.json"
WINDOWS_RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.I)


@dataclass(frozen=True)
class Member:
    """A regular file or directory that is safe to write on Windows."""

    name: str
    is_dir: bool
    size: int


def safe_relative_path(name: str) -> Path:
    """Reject traversal, absolute paths, NTFS streams and Windows aliases."""
    normalized = name.replace("\\", "/").rstrip("/")
    parts = normalized.split("/")
    if (
        not normalized or PureWindowsPath(normalized).drive
        or normalized.startswith("/")
        or any(
            part in ("", ".", "..") or part.endswith((" ", "."))
            or any(ord(char) < 32 or char in '<>:"|?*' for char in part)
            or WINDOWS_RESERVED.match(part)
            for part in parts
        )
    ):
        raise CollectorError(f"Unsafe archive member path: {name!r}")
    return Path(*parts)


def _check_members(members: list[Member]) -> list[Member]:
    seen: dict[str, bool] = {}
    for member in members:
        path = safe_relative_path(member.name)
        key = path.as_posix().casefold()
        if key == MANIFEST:
            raise CollectorError(f"Reserved archive member name: {member.name!r}")
        if key in seen:
            raise CollectorError(f"Duplicate archive path: {member.name!r}")
        seen[key] = member.is_dir
    for key in seen:
        parent = key.rpartition("/")[0]
        while parent:
            if parent in seen and not seen[parent]:
                raise CollectorError(f"Archive file is also a parent directory: {parent!r}")
            parent = parent.rpartition("/")[0]
    if not any(not member.is_dir for member in members):
        raise CollectorError("Archive contains no regular files.")
    return members


def _zip_members(archive: zipfile.ZipFile) -> list[Member]:
    members = []
    for item in archive.infolist():
        mode = stat.S_IFMT(item.external_attr >> 16)
        if mode not in (0, stat.S_IFREG, stat.S_IFDIR) or item.flag_bits & 1:
            raise CollectorError(f"Links, special or encrypted files are unsupported: {item.filename!r}")
        members.append(Member(item.filename, item.is_dir(), item.file_size))
    return _check_members(members)


def _seven_members(archive: py7zr.SevenZipFile) -> list[Member]:
    if archive.needs_password():
        raise CollectorError("Encrypted 7z archives are unsupported.")
    members = []
    for item in archive.list():
        if item.is_symlink or not (item.is_file or item.is_directory):
            raise CollectorError(f"Links or special files are unsupported: {item.filename!r}")
        members.append(Member(item.filename, item.is_directory, item.uncompressed))
    return _check_members(members)


def _format(path: Path, archive_format: str | None) -> str:
    result = (archive_format or path.suffix.lstrip(".")).lower()
    if result not in ("zip", "7z"):
        raise CollectorError(f"Unsupported archive format: {result}")
    return result


def validate_archive(path: Path, archive_format: str | None = None) -> None:
    """Read all records and verify CRCs without loading the archive into RAM."""
    kind = _format(path, archive_format)
    try:
        if kind == "zip":
            with zipfile.ZipFile(path) as archive:
                _zip_members(archive)
                bad_member = archive.testzip()
        else:
            with py7zr.SevenZipFile(path) as archive:
                _seven_members(archive)
                bad_member = archive.testzip()
        if bad_member is not None:
            raise CollectorError(f"Archive CRC check failed: {bad_member}")
    except CollectorError:
        raise
    except Exception as exc:
        # Compression libraries expose several unrelated exception classes.
        raise CollectorError(f"Cannot validate archive {path.name}: {exc}") from exc


def extract_archive(path: Path, destination: Path) -> Path:
    """Publish a directory only after every member is extracted successfully.

    Matching output is reused after SHA-256 verification. Existing unrelated
    or modified directories are never overwritten. TemporaryDirectory cleans
    up only its own staging directory on errors and interruptions.
    """
    kind = _format(path, None)
    archive_digest = _sha256(path)
    if destination.exists() or destination.is_symlink():
        if _matches_existing(destination, archive_digest):
            LOGGER.info("Using verified extracted files: %s", destination)
            return destination
        raise CollectorError(
            f"Extraction directory already exists and differs from this archive: {destination}. "
            "Choose another --extracted-dir; existing files were preserved."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(
            dir=destination.parent, prefix=f".{destination.name}.", suffix=".part",
        ) as temporary:
            staging = Path(temporary) / "contents"
            staging.mkdir()
            if kind == "zip":
                with zipfile.ZipFile(path) as archive:
                    members = _zip_members(archive)
                    _check_disk_space(staging, members)
                    for item, member in zip(archive.infolist(), members, strict=True):
                        target = staging / safe_relative_path(member.name)
                        if member.is_dir:
                            target.mkdir(parents=True, exist_ok=True)
                        else:
                            target.parent.mkdir(parents=True, exist_ok=True)
                            with archive.open(item) as source, target.open("xb") as output:
                                shutil.copyfileobj(source, output, length=1024 * 1024)
            else:
                with py7zr.SevenZipFile(path) as archive:
                    members = _seven_members(archive)
                    _check_disk_space(staging, members)
                    archive.extractall(path=staging)
            # Verify the output before making it visible under its final name.
            checksums = {}
            for member in members:
                target = staging / safe_relative_path(member.name)
                if target.is_symlink() or not target.resolve().is_relative_to(staging.resolve()):
                    raise CollectorError(f"Unsafe extracted path: {member.name!r}")
                if not member.is_dir and (
                    not target.is_file() or target.stat().st_size != member.size
                ):
                    raise CollectorError(f"Incomplete extracted file: {member.name!r}")
                if not member.is_dir:
                    checksums[safe_relative_path(member.name).as_posix()] = _sha256(target)
            (staging / MANIFEST).write_text(
                json.dumps({"archive_sha256": archive_digest, "files": checksums}),
                encoding="utf-8",
            )
            staging.rename(destination)
        LOGGER.info("Extracted to: %s", destination)
        return destination
    except CollectorError:
        raise
    except Exception as exc:
        raise CollectorError(f"Cannot extract {path.name}: {exc}") from exc


def _check_disk_space(directory: Path, members: list[Member]) -> None:
    required = sum(member.size for member in members)
    free = shutil.disk_usage(directory).free
    if required > free:
        raise CollectorError(f"Not enough disk space: need {required}, available {free} bytes.")


def _sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _matches_existing(directory: Path, archive_digest: str) -> bool:
    """A completion marker alone is insufficient: verify all recorded files."""
    manifest = directory / MANIFEST
    if directory.is_symlink() or not directory.is_dir() or manifest.is_symlink():
        return False
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if data.get("archive_sha256") != archive_digest or not data.get("files"):
            return False
        for name, digest in data["files"].items():
            target = directory / safe_relative_path(name)
            if (
                target.is_symlink() or not target.is_file()
                or not target.resolve().is_relative_to(directory.resolve())
                or _sha256(target) != digest
            ):
                return False
        return True
    except (OSError, ValueError, TypeError, AttributeError, CollectorError):
        return False
