"""Unit tests for configuration."""

from __future__ import annotations

import pytest

from omega.config import Settings


class TestSettings:
    """Test application settings validation."""

    def test_defaults(self) -> None:
        """Settings should have sensible defaults."""
        settings = Settings()
        assert settings.app_name == "omega"
        assert settings.app_version == "0.1.0"
        assert settings.environment in ("development", "test")
        assert settings.log_level == "INFO"

    def test_cors_origins_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CORS origins should be loaded from environment."""
        monkeypatch.setenv("CORS_ORIGINS", '["http://localhost:3000","http://example.com"]')
        settings = Settings()
        assert "http://localhost:3000" in settings.cors_origins
        assert "http://example.com" in settings.cors_origins

    def test_required_database_url(self) -> None:
        """DATABASE_URL should be set."""
        settings = Settings()
        assert "postgresql" in settings.database_url

    def test_required_redis_url(self) -> None:
        """REDIS_URL should be set."""
        settings = Settings()
        assert "redis" in settings.redis_url

    def test_worker_health_timeout_default(self) -> None:
        """Worker health timeout should default to 3.0."""
        settings = Settings()
        assert settings.worker_health_timeout == 3.0

    def test_auto_migrate_default(self) -> None:
        """Auto migrate should default to True."""
        settings = Settings()
        assert settings.auto_migrate is True

    def test_synthetic_dev_placeholder_used_in_development(self) -> None:
        """Development default must use explicitly labeled non-production synthetic placeholder."""
        settings = Settings()
        assert settings.synthetic_dev_db_password == "nonprod_synthetic_dev_placeholder_password"
        assert "nonprod_synthetic_dev_placeholder_password" in settings.database_url
        assert "nonprod_synthetic_dev_placeholder_password" in settings.database_url_sync

    def test_production_fails_closed_with_dev_placeholder(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Production environment must reject synthetic placeholder database credentials."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("DATABASE_URL_SYNC", raising=False)
        with pytest.raises(ValueError, match="Production DATABASE_URL must be explicitly configured"):
            Settings()

    def test_production_fails_closed_with_empty_database_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Production environment must reject empty database URL."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("DATABASE_URL", "")
        with pytest.raises(ValueError, match="Production DATABASE_URL must be explicitly configured"):
            Settings()

    def test_production_succeeds_with_explicit_operational_credential(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Production environment succeeds when explicit valid database URLs are configured."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://omega:real_secure_prod_pw_123@prod-db:5432/omega")
        monkeypatch.setenv("DATABASE_URL_SYNC", "postgresql+psycopg2://omega:real_secure_prod_pw_123@prod-db:5432/omega")
        settings = Settings()
        assert "real_secure_prod_pw_123" in settings.database_url
        assert "real_secure_prod_pw_123" in settings.database_url_sync
