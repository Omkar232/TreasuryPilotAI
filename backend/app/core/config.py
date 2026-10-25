from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from urllib.parse import urlsplit


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")
    app_env: str = "development"
    app_api_token: str = ""
    app_admin_token: str = ""
    database_url: str = "sqlite:///./treasurypilot.db"
    frontend_origins: str = "http://localhost:5173"
    airwallex_base_url: str = "https://api.sandbox.airwallex.com"
    airwallex_client_id: str = ""
    airwallex_api_key: str = ""
    airwallex_login_as: str = ""
    ai_provider: str = ""
    ai_api_key: str = ""
    ai_model: str = "gpt-4o-mini"

    @model_validator(mode="after")
    def validate_production_configuration(self):
        # Render Postgres exposes a generic postgresql:// URL. Pin SQLAlchemy to
        # the installed psycopg v3 driver instead of its psycopg2 default.
        if self.database_url.startswith("postgresql://"):
            self.database_url = "postgresql+psycopg://" + \
                self.database_url[len("postgresql://"):]
        if self.app_env != "production":
            return self

        if len(self.app_api_token) < 32:
            raise ValueError(
                "APP_API_TOKEN must contain at least 32 characters in production")
        if self.app_api_token.lower().startswith(("replace-", "change-me", "your-")):
            raise ValueError(
                "APP_API_TOKEN must not be a placeholder in production")
        if len(self.app_admin_token) < 32:
            raise ValueError(
                "APP_ADMIN_TOKEN must contain at least 32 characters in production")
        if self.app_admin_token.lower().startswith(("replace-", "change-me", "your-")):
            raise ValueError(
                "APP_ADMIN_TOKEN must not be a placeholder in production")
        if self.app_api_token == self.app_admin_token:
            raise ValueError(
                "APP_API_TOKEN and APP_ADMIN_TOKEN must be distinct in production")
        if not self.database_url.startswith("postgresql+psycopg://"):
            raise ValueError(
                "DATABASE_URL must use PostgreSQL with the installed psycopg driver in production")

        origins = self.cors_origins
        for origin in origins:
            try:
                parsed = urlsplit(origin)
            except ValueError as exc:
                raise ValueError(
                    "FRONTEND_ORIGINS contains an invalid origin") from exc
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                    or parsed.password is not None or parsed.path not in ("", "/")
                    or parsed.query or parsed.fragment):
                raise ValueError(
                    "Every FRONTEND_ORIGINS entry must be a bare HTTPS origin in production")
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [x.strip() for x in self.frontend_origins.split(",") if x.strip()]

    @property
    def airwallex_configured(self) -> bool:
        return bool(self.airwallex_client_id and self.airwallex_api_key and self.airwallex_sandbox_url_valid)

    @property
    def ai_configured(self) -> bool:
        return self.ai_provider.lower() == "openai" and bool(self.ai_api_key and self.ai_model)

    @property
    def airwallex_sandbox_url_valid(self) -> bool:
        try:
            parsed = urlsplit(self.airwallex_base_url)
            return (parsed.scheme == "https" and parsed.hostname == "api.sandbox.airwallex.com"
                    and parsed.username is None and parsed.password is None
                    and parsed.path in ("", "/") and not parsed.query and not parsed.fragment)
        except ValueError:
            return False


settings = Settings()
