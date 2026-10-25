import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_production_settings_reject_short_operator_token():
    with pytest.raises(ValidationError, match="APP_API_TOKEN"):
        Settings(_env_file=None, app_env="production", app_api_token="short",
            app_admin_token="b" * 40,
            database_url="postgresql+psycopg://db.example/app",
            frontend_origins="https://treasurypilot.example")


def test_production_settings_reject_placeholder_operator_token():
    with pytest.raises(ValidationError, match="placeholder"):
        Settings(_env_file=None, app_env="production",
            app_api_token="replace-this-with-a-real-random-secret-123456789",
            app_admin_token="b" * 40,
            database_url="postgresql+psycopg://db.example/app",
            frontend_origins="https://treasurypilot.example")


def test_production_settings_require_a_distinct_admin_token():
    base = {"_env_file": None, "app_env": "production",
        "app_api_token": "a" * 40,
        "database_url": "postgresql+psycopg://db.example/app",
        "frontend_origins": "https://treasurypilot.example"}
    with pytest.raises(ValidationError, match="APP_ADMIN_TOKEN"):
        Settings(**base, app_admin_token="short")
    with pytest.raises(ValidationError, match="must be distinct"):
        Settings(**base, app_admin_token="a" * 40)


def test_production_settings_reject_sqlite_and_non_https_origins():
    common = {"_env_file": None, "app_env": "production",
        "app_api_token": "a" * 40,
        "app_admin_token": "b" * 40,
        "frontend_origins": "https://treasurypilot.example"}
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        Settings(**common, database_url="sqlite:///./treasurypilot.db")
    with pytest.raises(ValidationError, match="HTTPS origin"):
        Settings(**{**common, "frontend_origins": "http://treasurypilot.example"},
            database_url="postgresql+psycopg://db.example/app")


def test_production_settings_accept_postgres_https_and_non_placeholder_token():
    settings = Settings(_env_file=None, app_env="production",
        app_api_token="a" * 40,
        app_admin_token="b" * 40,
        database_url="postgresql+psycopg://db.example/app",
        frontend_origins="https://treasurypilot.example")
    assert settings.app_env == "production"
    assert settings.cors_origins == ["https://treasurypilot.example"]


def test_production_settings_normalize_render_postgres_connection_string():
    settings = Settings(_env_file=None, app_env="production",
        app_api_token="a" * 40,
        app_admin_token="b" * 40,
        database_url="postgresql://db.example/app",
        frontend_origins="")
    assert settings.database_url == "postgresql+psycopg://db.example/app"
    assert settings.cors_origins == []
