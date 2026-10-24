"""Load local configuration explicitly, without import-time side effects."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    """Application settings; database credentials are excluded from repr."""

    database_url: str | None = field(default=None, repr=False)
    downloads_dir: Path = PROJECT_ROOT / "downloads"
    log_level: str = "INFO"
    extracted_dir: Path = PROJECT_ROOT / "extracted"
    moex_ca_bundle: str | None = None


def load_settings(env_file: Path | None = None) -> Settings:
    """Read .env; environment variables take precedence over its values."""
    load_dotenv(env_file if env_file is not None else PROJECT_ROOT / ".env")
    downloads_dir = Path(os.getenv("DOWNLOADS_DIR", "downloads"))
    if not downloads_dir.is_absolute():
        downloads_dir = PROJECT_ROOT / downloads_dir
    extracted_dir = Path(os.getenv("EXTRACTED_DIR", "extracted"))
    if not extracted_dir.is_absolute():
        extracted_dir = PROJECT_ROOT / extracted_dir
    ca_bundle = os.getenv("MOEX_CA_BUNDLE") or None
    if ca_bundle is not None:
        ca_path = Path(ca_bundle)
        ca_bundle = str(ca_path if ca_path.is_absolute() else PROJECT_ROOT / ca_path)

    return Settings(
        database_url=os.getenv("DATABASE_URL") or None,
        downloads_dir=downloads_dir,
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        extracted_dir=extracted_dir,
        moex_ca_bundle=ca_bundle,
    )
