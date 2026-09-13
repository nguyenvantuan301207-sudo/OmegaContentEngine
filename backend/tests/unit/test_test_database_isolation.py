"""Fail-closed tests for pytest database isolation helpers."""

from __future__ import annotations

import os

import pytest

from tests.conftest import _database_identity, _validate_test_database_url

RUNTIME_URL = "postgresql+asyncpg://runtime:secret@omega-postgres:5432/omega"


def test_test_database_url_is_mandatory() -> None:
    with pytest.raises(pytest.UsageError, match="TEST_DATABASE_URL is required"):
        _validate_test_database_url(RUNTIME_URL, None)


def test_runtime_database_identity_is_rejected_even_with_different_credentials() -> None:
    same_database = "postgresql+asyncpg://other:credentials@omega-postgres:5432/omega"
    with pytest.raises(pytest.UsageError, match="runtime database"):
        _validate_test_database_url(RUNTIME_URL, same_database)


def test_runtime_database_name_is_rejected_on_another_host() -> None:
    with pytest.raises(pytest.UsageError, match="name 'omega' is forbidden"):
        _validate_test_database_url(
            RUNTIME_URL,
            "postgresql+asyncpg://test:secret@isolated-postgres:5432/omega",
        )


def test_database_name_must_be_explicitly_test_only() -> None:
    with pytest.raises(pytest.UsageError, match="test-only"):
        _validate_test_database_url(
            RUNTIME_URL,
            "postgresql+asyncpg://test:secret@isolated-postgres:5432/staging",
        )


def test_isolated_test_database_is_accepted_without_mutating_environment() -> None:
    test_url = "postgresql+asyncpg://test:secret@omega-postgres:5432/omega_test"
    previous = os.environ.get("OMEGA_TEST_MODE")

    assert _validate_test_database_url(RUNTIME_URL, test_url) == test_url
    assert _database_identity(RUNTIME_URL) != _database_identity(test_url)
    assert os.environ.get("OMEGA_TEST_MODE") == previous


def test_scoped_test_mode_environment_is_restored() -> None:
    previous = os.environ.get("OMEGA_TEST_MODE")
    with pytest.MonkeyPatch.context() as scoped:
        scoped.setenv("OMEGA_TEST_MODE", "1")
        assert os.environ["OMEGA_TEST_MODE"] == "1"
    assert os.environ.get("OMEGA_TEST_MODE") == previous
