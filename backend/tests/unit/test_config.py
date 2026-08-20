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
