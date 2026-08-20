"""Unit tests for job service — sanitize_error."""

from __future__ import annotations

from omega.application.job_service import _sanitize_error


class TestSanitizeError:
    """Test error sanitization logic."""

    def test_simple_error(self) -> None:
        """Simple errors should be passed through with type prefix."""
        error = ValueError("Something went wrong")
        result = _sanitize_error(error)
        assert result == "ValueError: Something went wrong"

    def test_connection_string_redacted(self) -> None:
        """Errors containing connection strings should be redacted."""
        error = ConnectionError("Failed to connect to postgresql://user:pass@host/db")
        result = _sanitize_error(error)
        assert "://" not in result
        assert "details in logs" in result

    def test_password_redacted(self) -> None:
        """Errors containing 'password' should be redacted."""
        error = Exception("Invalid password for user admin")
        result = _sanitize_error(error)
        assert "password" not in result.lower() or "details in logs" in result

    def test_token_redacted(self) -> None:
        """Errors containing 'token' should be redacted."""
        error = Exception("OAuth token expired: abc123")
        result = _sanitize_error(error)
        assert "details in logs" in result

    def test_long_error_truncated(self) -> None:
        """Long error messages should be truncated."""
        error = ValueError("x" * 1000)
        result = _sanitize_error(error)
        assert len(result) < 600
        assert result.endswith("...")

    def test_multiline_error_first_line_only(self) -> None:
        """Only the first line of multiline errors should be used."""
        error = RuntimeError("First line\nSecond line\nThird line")
        result = _sanitize_error(error)
        assert "Second line" not in result
        assert "First line" in result
