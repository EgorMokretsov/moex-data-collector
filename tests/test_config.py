"""Configuration boundaries that must hold before database ingestion exists."""

import pytest

from app.config import PROJECT_ROOT, load_settings
from app.database import build_engine


def test_environment_overrides_dotenv_and_hides_credentials(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=from-file\n", encoding="utf-8")
    monkeypatch.setenv("DATABASE_URL", "from-environment-secret")
    monkeypatch.setenv("DOWNLOADS_DIR", "downloads")

    settings = load_settings(env_file)

    assert settings.database_url == "from-environment-secret"
    assert "from-environment-secret" not in repr(settings)
    assert settings.downloads_dir == PROJECT_ROOT / "downloads"


def test_missing_database_url_fails_before_connecting(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")

    settings = load_settings(env_file)

    with pytest.raises(ValueError, match="DATABASE_URL"):
        build_engine(settings)
