"""Stream downloads to temporary files and publish only verified archives."""

import logging
import tempfile
import time
from pathlib import Path

import requests

from app.discovery import ARCHIVE_NAME, TIMEOUT, Archive
from app.errors import CollectorError
from app.extractor import validate_archive

LOGGER = logging.getLogger(__name__)
CHUNK_SIZE = 1024 * 1024


def download_archive(
    archive: Archive, downloads_dir: Path, session: requests.Session,
) -> Path:
    """Reuse valid local archives; replace damaged ones after a full download."""
    if not ARCHIVE_NAME.fullmatch(archive.filename):
        raise CollectorError(f"Invalid archive filename: {archive.filename!r}")
    folder = downloads_dir / archive.market.value
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / archive.filename
    if destination.is_symlink():
        raise CollectorError(f"Archive destination is a symbolic link: {destination}")
    if destination.exists():
        try:
            validate_archive(destination, archive.archive_format)
        except CollectorError:
            LOGGER.warning("Local archive is damaged; downloading again: %s", destination)
        else:
            LOGGER.info("Using verified archive: %s", destination)
            return destination

    temporary: Path | None = None
    try:
        with session.get(
            archive.source_url, stream=True, timeout=TIMEOUT,
            headers={"Accept-Encoding": "identity"},
        ) as response:
            response.raise_for_status()
            if response.status_code != 200:
                raise CollectorError(
                    f"Expected a full download, got HTTP {response.status_code}."
                )
            length_header = response.headers.get("Content-Length")
            expected = int(length_header) if length_header is not None else None
            LOGGER.info(
                "Downloading %s (%s bytes)", archive.filename, expected or "unknown",
            )
            with tempfile.NamedTemporaryFile(
                dir=folder, prefix=f".{archive.filename}.", suffix=".part", delete=False,
            ) as output:
                temporary = Path(output.name)
                received = 0
                last_progress = time.monotonic()
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    if chunk:
                        output.write(chunk)
                        received += len(chunk)
                        if time.monotonic() - last_progress >= 5:
                            LOGGER.info("Downloaded %.1f MiB", received / 1024**2)
                            last_progress = time.monotonic()
            if expected is not None and received != expected:
                raise CollectorError(
                    f"Incomplete download: expected {expected}, got {received} bytes."
                )
        validate_archive(temporary, archive.archive_format)
        temporary.replace(destination)
        LOGGER.info("Downloaded and verified: %s", destination)
        return destination
    finally:
        # This path was created by this invocation, never a shared .part file.
        if temporary is not None:
            temporary.unlink(missing_ok=True)
