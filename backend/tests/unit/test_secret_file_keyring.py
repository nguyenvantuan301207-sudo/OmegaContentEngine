"""Unit tests for secret-file keyring loading authority, precedence, and fail-closed semantics.

Implements all 25 test cases required by P20-D1S-PRE1:
1. explicit valid keyring file
2. implicit default secret file
3. env fallback when no file exists
4. legacy single-key fallback
5. explicit missing file -> failure
6. explicit unreadable file -> failure
7. invalid JSON -> failure
8. non-object JSON -> failure
9. invalid version key -> failure
10. duplicate normalized version -> failure
11. invalid Fernet key -> failure
12. active version absent -> failure
13. empty file -> failure
14. oversized file -> failure
15. unsafe/world-writable file -> failure where platform semantics permit
16. file takes precedence over environment
17. invalid existing default file does NOT fall back
18. vault v1/v2/v3 multi-key read
19. active v3 write
20. v3-only operation
21. rotation dry-run using only file source
22. rotation execution using only file source
23. verify using only file source
24. preflight using only file source
25. secret contents absent from logs/exceptions/test reports

Synthetic Fernet keys only. Never real production keys.
"""

from __future__ import annotations

import base64
import json
import os
import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from scripts.p20_release_tool import check_rotation_preflight

from omega.infrastructure.vault import (
    CredentialVaultService,
    VaultConfigurationError,
    VaultDecryptionError,
    load_keyring,
)
from omega.maintenance.rotate_vault_keys import format_report, main_async, parse_args


@pytest.fixture(autouse=True)
def isolated_vault_environment(monkeypatch):
    for name in (
        "OMEGA_KEYRING_FILE",
        "OMEGA_KEYRING",
        "OMEGA_SECRET_ENCRYPTION_KEY",
        "OMEGA_CURRENT_KEY_VERSION",
    ):
        monkeypatch.delenv(name, raising=False)


def _synth_key() -> str:
    """Generate a clean synthetic Fernet key."""
    return Fernet.generate_key().decode("utf-8")


# ── 1. Explicit valid keyring file ──
def test_explicit_valid_keyring_file(tmp_path: Path):
    k1, k2, k3 = _synth_key(), _synth_key(), _synth_key()
    keyring_data = {"1": k1, "2": k2, "3": k3}
    secret_file = tmp_path / "omega_keyring.json"
    secret_file.write_text(json.dumps(keyring_data), encoding="utf-8")

    res = load_keyring(keyring_file=str(secret_file), active_version=2)
    assert res.source == "EXPLICIT_FILE"
    assert res.source_path == str(secret_file)
    assert res.active_version == 2
    assert res.versions == [1, 2, 3]
    assert len(res.keyring) == 3


# ── 2. Implicit default secret file ──
def test_implicit_default_secret_file(tmp_path: Path):
    k1 = _synth_key()
    secret_file = tmp_path / "default_keyring.json"
    secret_file.write_text(json.dumps({"1": k1}), encoding="utf-8")

    with patch.dict(os.environ, {}, clear=True):
        res = load_keyring(
            keyring_file=None,
            active_version=1,
            default_secret_file=str(secret_file),
        )
        assert res.source == "DEFAULT_FILE"
        assert res.source_path == str(secret_file)
        assert res.versions == [1]


# ── 3. Env fallback when no file exists ──
def test_env_fallback_when_no_file_exists(tmp_path: Path):
    k1, k2 = _synth_key(), _synth_key()
    env_json = json.dumps({"1": k1, "2": k2})

    with patch.dict(os.environ, {"OMEGA_KEYRING": env_json}, clear=True):
        res = load_keyring(
            keyring_file=None,
            default_secret_file=str(tmp_path / "absent.json"),
            active_version=1,
        )
        assert res.source == "ENV_KEYRING"
        assert res.source_path is None
        assert res.versions == [1, 2]


# ── 4. Legacy single-key fallback ──
def test_legacy_single_key_fallback(tmp_path: Path):
    k1 = _synth_key()
    with patch.dict(os.environ, {"OMEGA_SECRET_ENCRYPTION_KEY": k1}, clear=True):
        res = load_keyring(
            keyring_file=None,
            default_secret_file=str(tmp_path / "absent.json"),
            active_version=1,
        )
        assert res.source == "LEGACY_KEY"
        assert res.source_path is None
        assert res.versions == [1]


# ── 5. Explicit missing file -> failure ──
def test_explicit_missing_file_failure(tmp_path: Path):
    missing_file = tmp_path / "does_not_exist.json"
    k1 = _synth_key()
    # Even if environment has a valid fallback, explicit file MUST FAIL CLOSED
    with (
        patch.dict(os.environ, {"OMEGA_KEYRING": json.dumps({"1": k1})}, clear=True),
        pytest.raises(VaultConfigurationError, match="does not exist"),
    ):
        load_keyring(keyring_file=str(missing_file))


# ── 6. Explicit unreadable file -> failure ──
def test_explicit_unreadable_file_failure(tmp_path: Path):
    secret_file = tmp_path / "unreadable.json"
    secret_file.write_text("{}", encoding="utf-8")

    with (
        patch("os.open", side_effect=PermissionError("Permission denied")),
        pytest.raises(VaultConfigurationError, match="Failed to read keyring secret file"),
    ):
        load_keyring(keyring_file=str(secret_file))


# ── 7. Invalid JSON -> failure ──
def test_invalid_json_failure(tmp_path: Path):
    secret_file = tmp_path / "bad_json.json"
    secret_file.write_text('{"1": "bad json without close quote}', encoding="utf-8")

    with pytest.raises(VaultConfigurationError, match="Failed to parse keyring JSON"):
        load_keyring(keyring_file=str(secret_file))


# ── 8. Non-object JSON -> failure ──
def test_non_object_json_failure(tmp_path: Path):
    secret_file = tmp_path / "array.json"
    secret_file.write_text('["key1", "key2"]', encoding="utf-8")

    with pytest.raises(VaultConfigurationError, match="must be a JSON object"):
        load_keyring(keyring_file=str(secret_file))


# ── 9. Invalid version key -> failure ──
def test_invalid_version_key_failure(tmp_path: Path):
    secret_file = tmp_path / "bad_ver.json"
    secret_file.write_text(json.dumps({"v1": _synth_key()}), encoding="utf-8")

    with pytest.raises(VaultConfigurationError, match="must be an integer"):
        load_keyring(keyring_file=str(secret_file))

    # Also test negative or zero version
    secret_file2 = tmp_path / "bad_ver2.json"
    secret_file2.write_text(json.dumps({"0": _synth_key()}), encoding="utf-8")
    with pytest.raises(VaultConfigurationError, match="must be a positive integer"):
        load_keyring(keyring_file=str(secret_file2))


# ── 10. Duplicate normalized version -> failure ──
def test_duplicate_normalized_version_failure(tmp_path: Path):
    secret_file = tmp_path / "dup_ver.json"
    # "1" and "01" parse to the same integer 1
    raw_content = f'{{"1": "{_synth_key()}", "01": "{_synth_key()}"}}'
    secret_file.write_text(raw_content, encoding="utf-8")

    with pytest.raises(VaultConfigurationError, match="Duplicate normalized key version"):
        load_keyring(keyring_file=str(secret_file))


# ── 11. Invalid Fernet key -> failure ──
def test_invalid_fernet_key_failure(tmp_path: Path):
    secret_file = tmp_path / "bad_fernet.json"
    secret_file.write_text(json.dumps({"1": "not-a-fernet-key"}), encoding="utf-8")

    with pytest.raises(VaultConfigurationError, match="Invalid encryption key format"):
        load_keyring(keyring_file=str(secret_file))


# ── 12. Active version absent -> failure ──
def test_active_version_absent_failure(tmp_path: Path):
    secret_file = tmp_path / "missing_active.json"
    secret_file.write_text(json.dumps({"1": _synth_key(), "2": _synth_key()}), encoding="utf-8")

    with pytest.raises(VaultConfigurationError, match="Active key version 3 not found in keyring"):
        load_keyring(keyring_file=str(secret_file), active_version=3)


# ── 13. Empty file -> failure ──
def test_empty_file_failure(tmp_path: Path):
    secret_file = tmp_path / "empty.json"
    secret_file.write_text("", encoding="utf-8")

    with pytest.raises(VaultConfigurationError, match="empty"):
        load_keyring(keyring_file=str(secret_file))

    # Also test empty object {}
    secret_file_obj = tmp_path / "empty_obj.json"
    secret_file_obj.write_text("{}", encoding="utf-8")
    with pytest.raises(VaultConfigurationError, match="empty"):
        load_keyring(keyring_file=str(secret_file_obj))


# ── 14. Oversized file -> failure ──
def test_oversized_file_failure(tmp_path: Path):
    secret_file = tmp_path / "huge.json"
    # Create file > 64KB
    huge_data = {"1": _synth_key(), "padding": "x" * 70000}
    secret_file.write_text(json.dumps(huge_data), encoding="utf-8")

    with pytest.raises(VaultConfigurationError, match="exceeds maximum allowed size"):
        load_keyring(keyring_file=str(secret_file))


# ── 15. Unsafe/world-writable file -> failure where platform permits ──
@pytest.mark.skipif(os.name != "posix", reason="POSIX permission semantics")
def test_unsafe_world_writable_file_failure(tmp_path: Path):
    secret_file = tmp_path / "unsafe.json"
    secret_file.write_text(json.dumps({"1": _synth_key()}), encoding="utf-8")

    # Mock the opened descriptor permissions without altering pathlib checks.
    real_stat = os.stat(secret_file)
    mock_stat = MagicMock(
        st_mode=real_stat.st_mode | stat.S_IWOTH,
        st_size=real_stat.st_size,
    )
    with (
        patch("os.fstat", return_value=mock_stat),
        pytest.raises(VaultConfigurationError, match="world-writable"),
    ):
        load_keyring(keyring_file=str(secret_file))


# ── 16. File takes precedence over environment ──
def test_file_takes_precedence_over_environment(tmp_path: Path):
    k_file = _synth_key()
    k_env = _synth_key()

    secret_file = tmp_path / "prec.json"
    secret_file.write_text(json.dumps({"1": k_file}), encoding="utf-8")

    with patch.dict(
        os.environ,
        {"OMEGA_KEYRING": json.dumps({"1": k_env}), "OMEGA_SECRET_ENCRYPTION_KEY": k_env},
        clear=True,
    ):
        res = load_keyring(keyring_file=str(secret_file), active_version=1)
        assert res.source == "EXPLICIT_FILE"
        assert res.source_path == str(secret_file)
        # Verify the key in res matches k_file by encrypting and decrypting
        f_file = Fernet(k_file.encode("utf-8"))
        tok = f_file.encrypt(b"test-payload")
        assert res.keyring[1].decrypt(tok) == b"test-payload"


# ── 17. Invalid existing default file does NOT fall back ──
def test_invalid_existing_default_file_does_not_fall_back(tmp_path: Path):
    bad_default = tmp_path / "default_bad.json"
    bad_default.write_text("NOT_VALID_JSON", encoding="utf-8")
    k_env = _synth_key()

    with (
        patch.dict(os.environ, {"OMEGA_KEYRING": json.dumps({"1": k_env})}, clear=True),
        pytest.raises(VaultConfigurationError, match="Failed to parse keyring JSON"),
    ):
        load_keyring(
            keyring_file=None,
            default_secret_file=str(bad_default),
        )


# ── 18. Vault v1/v2/v3 multi-key read ──
def test_vault_multi_key_read(tmp_path: Path):
    k1, k2, k3 = _synth_key(), _synth_key(), _synth_key()
    secret_file = tmp_path / "multi_keyring.json"
    secret_file.write_text(json.dumps({"1": k1, "2": k2, "3": k3}), encoding="utf-8")

    vault = CredentialVaultService(keyring_file=str(secret_file), active_version=3)

    # Encrypt directly with individual keys
    c1 = Fernet(k1.encode("utf-8")).encrypt(b"token-v1").decode("utf-8")
    c2 = Fernet(k2.encode("utf-8")).encrypt(b"token-v2").decode("utf-8")
    c3 = Fernet(k3.encode("utf-8")).encrypt(b"token-v3").decode("utf-8")

    # Vault must decrypt all three
    assert vault.decrypt(c1, key_version=1) == "token-v1"
    assert vault.decrypt(c2, key_version=2) == "token-v2"
    assert vault.decrypt(c3, key_version=3) == "token-v3"

    # MultiFernet fallback decrypt (without explicit key_version)
    assert vault.decrypt(c1) == "token-v1"
    assert vault.decrypt(c2) == "token-v2"
    assert vault.decrypt(c3) == "token-v3"


# ── 19. Active v3 write ──
def test_active_v3_write(tmp_path: Path):
    k1, k2, k3 = _synth_key(), _synth_key(), _synth_key()
    secret_file = tmp_path / "keyring.json"
    secret_file.write_text(json.dumps({"1": k1, "2": k2, "3": k3}), encoding="utf-8")

    vault = CredentialVaultService(keyring_file=str(secret_file), active_version=3)
    cipher, v = vault.encrypt("new-secret-value")

    assert v == 3
    assert cipher.startswith("gAAAAA")
    assert vault.decrypt(cipher, 3) == "new-secret-value"


# ── 20. v3-only operation ──
def test_v3_only_operation(tmp_path: Path):
    k3 = _synth_key()
    secret_file = tmp_path / "v3_only.json"
    secret_file.write_text(json.dumps({"3": k3}), encoding="utf-8")

    vault = CredentialVaultService(keyring_file=str(secret_file), active_version=3)
    cipher, v = vault.encrypt("isolated-token")
    assert v == 3
    assert vault.decrypt(cipher, 3) == "isolated-token"

    # Reading v1 or v2 ciphertext must fail with VaultDecryptionError
    k1 = _synth_key()
    c1 = Fernet(k1.encode("utf-8")).encrypt(b"old-token").decode("utf-8")
    with pytest.raises(VaultDecryptionError, match="could not authenticate ciphertext"):
        vault.decrypt(c1, 1)


# ── 21. Rotation dry-run using only file source ──
@pytest.mark.asyncio
async def test_rotation_dry_run_using_only_file_source(tmp_path: Path):
    k1, k2, k3 = _synth_key(), _synth_key(), _synth_key()
    secret_file = tmp_path / "rot_keyring.json"
    secret_file.write_text(json.dumps({"1": k1, "2": k2, "3": k3}), encoding="utf-8")

    # Clear raw keyring from env
    with patch.dict(os.environ, {"OMEGA_CURRENT_KEY_VERSION": "3"}, clear=True):
        args = parse_args(
            [
                "--target-version",
                "3",
                "--dry-run",
                "--keyring-file",
                str(secret_file),
            ]
        )
        assert args.keyring_file == str(secret_file)

        # Mock VaultKeyRotationService inside main_async
        with patch("omega.maintenance.rotate_vault_keys.VaultKeyRotationService") as mock_srv_cls:
            mock_srv = MagicMock()
            mock_report = MagicMock(
                mode="DRY_RUN",
                failed_count=0,
                total_evaluated=10,
                eligible_count=10,
                rotated_count=0,
                already_current_count=0,
                entries=[],
            )
            mock_srv.rotate_keys = pytest.importorskip("unittest.mock").AsyncMock(
                return_value=mock_report
            )
            mock_srv_cls.return_value = mock_srv

            exit_code = await main_async(args)
            assert exit_code == 0
            # Ensure vault was initialized with keyring_file
            assert mock_srv_cls.call_args[1]["vault"].keyring_source == "EXPLICIT_FILE"


# ── 22. Rotation execution using only file source ──
@pytest.mark.asyncio
async def test_rotation_execution_using_only_file_source(tmp_path: Path):
    k1, k2, k3 = _synth_key(), _synth_key(), _synth_key()
    secret_file = tmp_path / "rot_exec_keyring.json"
    secret_file.write_text(json.dumps({"1": k1, "2": k2, "3": k3}), encoding="utf-8")

    with patch.dict(os.environ, {"OMEGA_CURRENT_KEY_VERSION": "3"}, clear=True):
        args = parse_args(
            [
                "--target-version",
                "3",
                "--execute",
                "--keyring-file",
                str(secret_file),
            ]
        )

        with patch("omega.maintenance.rotate_vault_keys.VaultKeyRotationService") as mock_srv_cls:
            mock_srv = MagicMock()
            mock_report = MagicMock(
                mode="EXECUTE",
                failed_count=0,
                total_evaluated=10,
                eligible_count=10,
                rotated_count=10,
                already_current_count=0,
                entries=[],
            )
            mock_srv.rotate_keys = pytest.importorskip("unittest.mock").AsyncMock(
                return_value=mock_report
            )
            mock_srv_cls.return_value = mock_srv

            exit_code = await main_async(args)
            assert exit_code == 0
            vault_inst = mock_srv_cls.call_args[1]["vault"]
            assert vault_inst.keyring_source == "EXPLICIT_FILE"
            assert vault_inst.active_version == 3


# ── 23. Verify using only file source ──
@pytest.mark.asyncio
async def test_verify_using_only_file_source(tmp_path: Path):
    k3 = _synth_key()
    secret_file = tmp_path / "verify_keyring.json"
    secret_file.write_text(json.dumps({"3": k3}), encoding="utf-8")

    with patch.dict(os.environ, {"OMEGA_CURRENT_KEY_VERSION": "3"}, clear=True):
        args = parse_args(
            [
                "--target-version",
                "3",
                "--verify",
                "--keyring-file",
                str(secret_file),
            ]
        )

        with patch("omega.maintenance.rotate_vault_keys.VaultKeyRotationService") as mock_srv_cls:
            mock_srv = MagicMock()
            mock_report = MagicMock(
                mode="VERIFY",
                failed_count=0,
                total_evaluated=10,
                already_current_count=10,
                entries=[],
            )
            mock_srv.verify_keys = pytest.importorskip("unittest.mock").AsyncMock(
                return_value=mock_report
            )
            mock_srv_cls.return_value = mock_srv

            exit_code = await main_async(args)
            assert exit_code == 0


# ── 24. Preflight using only file source ──
def test_preflight_using_only_file_source(tmp_path: Path):
    k1, k2, k3 = _synth_key(), _synth_key(), _synth_key()
    secret_file = tmp_path / "preflight_keyring.json"
    secret_file.write_text(json.dumps({"1": k1, "2": k2, "3": k3}), encoding="utf-8")

    # Dummy backup
    backup_file = tmp_path / "test_backup.dump"
    backup_file.write_bytes(b"PGDMP_DUMMY")

    with (
        patch.dict(os.environ, {"OMEGA_CURRENT_KEY_VERSION": "3"}, clear=True),
        patch(
            "scripts.p20_release_tool.check_publisher_absence",
            return_value={"passed": True, "violations": []},
        ),
        patch(
            "scripts.p20_release_tool.verify_deployment_safety",
            return_value={"passed": True, "violations": []},
        ),
    ):
        res = check_rotation_preflight(
            backup_path=backup_file, target_version=3, keyring_file=secret_file
        )
        assert res["passed"] is True
        details = res["details"]
        assert details["keyring_source"] == "EXPLICIT_FILE"
        assert details["key_versions_present"] == [1, 2, 3]
        assert details["active_key_version"] == 3
        assert details["keyring_valid"] is True


# ── 25. Secret contents absent from logs/exceptions/test reports ──
def test_secret_contents_absent_from_logs_exceptions_reports(tmp_path: Path, capsys):
    secret_marker = "SUPER_SECRET_MARKER_FERNET_KEY_12345"
    k_bad = base64.urlsafe_b64encode(secret_marker.encode("utf-8")).decode("utf-8")

    secret_file = tmp_path / "leak_test.json"
    secret_file.write_text(json.dumps({"1": k_bad}), encoding="utf-8")

    # 1. Validation failure: key length mismatch or invalid Fernet
    with pytest.raises(VaultConfigurationError) as exc_info:
        load_keyring(keyring_file=str(secret_file))

    exc_str = str(exc_info.value)
    assert secret_marker not in exc_str
    assert k_bad not in exc_str

    # 2. Preflight report verification
    res = check_rotation_preflight(keyring_file=secret_file)
    dumped_report = json.dumps(res)
    assert secret_marker not in dumped_report
    assert k_bad not in dumped_report

    # 3. Format report check
    dummy_report = MagicMock(
        mode="DRY_RUN",
        target_version=3,
        active_configured_version=1,
        filter_platform_account_id=None,
        filter_current_version=None,
        total_evaluated=0,
        eligible_count=0,
        rotated_count=0,
        already_current_count=0,
        failed_count=0,
        entries=[],
    )
    formatted = format_report(dummy_report)
    assert secret_marker not in formatted


@pytest.mark.parametrize(
    "content",
    [
        '{"1": "first", "1": "second"}',
        '{"SECRET_VERSION_MARKER": "bad"}',
        '{"1": 12}',
        '{"1": ""}',
        '{"1": null}',
    ],
)
def test_additional_invalid_json_contract(tmp_path, content):
    path = tmp_path / "invalid.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(VaultConfigurationError) as error:
        load_keyring(keyring_file=str(path))
    assert "SECRET_VERSION_MARKER" not in str(error.value)


@pytest.mark.parametrize("version", ["SECRET_ACTIVE_MARKER", "", "0", "-1"])
def test_invalid_active_environment_is_safe(monkeypatch, version):
    monkeypatch.setenv("OMEGA_CURRENT_KEY_VERSION", version)
    with pytest.raises(VaultConfigurationError) as error:
        load_keyring(env_keyring_json=json.dumps({"1": _synth_key()}), default_secret_file=None)
    assert "SECRET_ACTIVE_MARKER" not in str(error.value)


@pytest.mark.parametrize("name", ["OMEGA_KEYRING_FILE", "OMEGA_KEYRING"])
def test_empty_explicit_source_never_falls_back(monkeypatch, name):
    monkeypatch.setenv(name, "")
    monkeypatch.setenv("OMEGA_SECRET_ENCRYPTION_KEY", _synth_key())
    with pytest.raises(VaultConfigurationError):
        load_keyring(default_secret_file=None)


def test_directory_file_rejected(tmp_path):
    with pytest.raises(VaultConfigurationError, match="not a regular file"):
        load_keyring(keyring_file=str(tmp_path))


def test_invalid_utf8_rejected(tmp_path):
    path = tmp_path / "invalid.json"
    path.write_bytes(bytes([255]))
    with pytest.raises(VaultConfigurationError, match="UTF-8"):
        load_keyring(keyring_file=str(path))


def test_environment_file_and_active_version(monkeypatch, tmp_path):
    path = tmp_path / "env-file.json"
    path.write_text(json.dumps({"3": _synth_key()}), encoding="utf-8")
    before = path.read_bytes()
    monkeypatch.setenv("OMEGA_KEYRING_FILE", str(path))
    monkeypatch.setenv("OMEGA_CURRENT_KEY_VERSION", "3")
    vault = CredentialVaultService()
    ciphertext, version = vault.encrypt("synthetic-payload")
    assert version == 3
    assert vault.decrypt(ciphertext, 3) == "synthetic-payload"
    assert path.read_bytes() == before


@pytest.mark.skipif(os.name != "posix", reason="POSIX mount permissions")
def test_docker_secret_readable_mode(tmp_path):
    path = tmp_path / "mounted.json"
    path.write_text(json.dumps({"1": _synth_key()}), encoding="utf-8")
    path.chmod(0o444)
    assert load_keyring(keyring_file=str(path)).versions == [1]


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink semantics")
def test_broken_default_symlink_fails_closed(tmp_path, monkeypatch):
    path = tmp_path / "broken.json"
    path.symlink_to(tmp_path / "missing.json")
    monkeypatch.setenv("OMEGA_KEYRING", json.dumps({"1": _synth_key()}))
    with pytest.raises(VaultConfigurationError, match="does not exist"):
        load_keyring(default_secret_file=str(path))


@pytest.mark.parametrize(
    "keyring,active", [({}, 1), ({True: "invalid"}, 1), ({1.5: "invalid"}, 1), ({1: "invalid"}, 0)]
)
def test_in_memory_validation_uses_same_contract(keyring, active):
    with pytest.raises(VaultConfigurationError):
        CredentialVaultService(keyring=keyring, active_version=active)


def test_keyring_diagnostics_redacted():
    from omega.application.observability.secret_sanitizer import sanitize_event_dict

    marker = "SYNTHETIC_KEYRING_MARKER"
    result = sanitize_event_dict(
        None,
        "info",
        {
            "OMEGA_KEYRING": marker,
            "OMEGA_KEYRING_FILE": "/run/secrets/omega_keyring.json",
            "active_version": 3,
        },
    )
    assert marker not in str(result)
    assert result["active_version"] == 3
    assert result["OMEGA_KEYRING_FILE"] == "/run/secrets/omega_keyring.json"


def test_compose_keyring_least_privilege():
    import yaml

    compose = yaml.safe_load(
        (Path(__file__).resolve().parents[3] / "docker-compose.prod.yml").read_text(
            encoding="utf-8"
        )
    )
    for name in ("omega-api", "omega-worker"):
        service = compose["services"][name]
        assert service["secrets"][0]["target"] == "/run/secrets/omega_keyring.json"
        assert service["environment"]["OMEGA_KEYRING_FILE"] == "/run/secrets/omega_keyring.json"
        assert "OMEGA_KEYRING" not in service["environment"]
        assert "OMEGA_SECRET_ENCRYPTION_KEY" not in service["environment"]
    assert "secrets" not in compose["services"]["omega-beat"]


def test_preflight_bad_file_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("OMEGA_KEYRING", json.dumps({"1": _synth_key()}))
    with patch(
        "scripts.p20_release_tool.check_publisher_absence",
        return_value={"passed": True, "violations": []},
    ):
        result = check_rotation_preflight(keyring_file=tmp_path / "absent.json")
    assert not result["passed"]
    assert not result["details"]["keyring_valid"]


def test_default_stat_permission_error_never_falls_back(monkeypatch):
    monkeypatch.setenv("OMEGA_KEYRING", json.dumps({"1": _synth_key()}))
    with (
        patch("os.lstat", side_effect=PermissionError("SYNTHETIC_SECRET_MARKER")),
        pytest.raises(VaultConfigurationError) as error,
    ):
        load_keyring(default_secret_file="/unreadable/omega_keyring.json")
    assert "SYNTHETIC_SECRET_MARKER" not in str(error.value)


def test_file_growth_read_is_bounded(tmp_path):
    path = tmp_path / "growing.json"
    path.write_bytes(b"x" * 70000)
    metadata = os.stat(path)
    fake = MagicMock(st_mode=metadata.st_mode, st_size=1)
    with (
        patch("os.fstat", return_value=fake),
        pytest.raises(VaultConfigurationError, match="maximum allowed size"),
    ):
        load_keyring(keyring_file=str(path))


@pytest.mark.asyncio
async def test_cli_does_not_override_configured_active_version(tmp_path, monkeypatch, capsys):
    from omega.application.vault_key_rotation import VaultKeyRotationService

    path = tmp_path / "rotation.json"
    path.write_text(json.dumps({"1": _synth_key(), "3": _synth_key()}), encoding="utf-8")
    monkeypatch.setenv("OMEGA_KEYRING_FILE", str(path))
    monkeypatch.setenv("OMEGA_CURRENT_KEY_VERSION", "1")
    with patch.object(VaultKeyRotationService, "rotate_keys", autospec=True) as rotate:
        rotate.side_effect = ValueError("Configured active version mismatch")
        assert await main_async(parse_args(["--target-version", "3", "--execute"])) == 1
        assert rotate.call_args.args[0].vault.active_version == 1
    assert "Configured active version mismatch" not in capsys.readouterr().err


def test_preflight_checks_configured_active_version(tmp_path, monkeypatch):
    path = tmp_path / "rotation.json"
    path.write_text(json.dumps({"3": _synth_key()}), encoding="utf-8")
    monkeypatch.setenv("OMEGA_CURRENT_KEY_VERSION", "1")
    with patch(
        "scripts.p20_release_tool.check_publisher_absence",
        return_value={"passed": True, "violations": []},
    ):
        result = check_rotation_preflight(keyring_file=path, target_version=3)
    assert not result["details"]["keyring_valid"]
