"""Reproducible provisioning script for Kokoro-82M ONNX model weights and voice pack.

Downloads and verifies SHA-256 checksums for:
- kokoro-v1.0.fp16.onnx (release model-files-v1.1)
- voices-v1.0.bin (release model-files-v1.1)
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import urllib.request
from pathlib import Path

# Authoritative model artifacts and checksums (release model-files-v1.1)
KOKORO_MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/kokoro-v1.0.fp16.onnx"
KOKORO_MODEL_SHA256 = "f3a290d384fbb27966d462905c71a46cef9e5fd00516b40df32a0b4afe77ac96"
KOKORO_MODEL_SIZE = 163527961

KOKORO_VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/voices-v1.0.bin"
KOKORO_VOICES_SHA256 = "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"
KOKORO_VOICES_SIZE = 28214398

DEFAULT_MODEL_DIR = Path(os.getenv("LOCAL_TTS_MODEL_DIR", "/app/models/tts/kokoro"))
BENCHMARK_CACHE_DIR = Path("/tmp/omega_tts_benchmark/kokoro")


def compute_sha256(file_path: Path) -> str:
    """Compute SHA-256 checksum of a file in 64KB blocks."""
    hasher = hashlib.sha256()
    with file_path.open("rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def verify_file(file_path: Path, expected_sha256: str, expected_size: int | None = None) -> bool:
    """Verify that a file exists, matches expected size (if provided), and matches expected SHA-256."""
    if not file_path.exists():
        return False
    if expected_size is not None and file_path.stat().st_size != expected_size:
        return False
    actual_sha = compute_sha256(file_path)
    return actual_sha.lower() == expected_sha256.lower()


def download_with_progress(url: str, dest_path: Path) -> None:
    """Download a file via HTTP with chunked streaming to temporary file before atomic rename."""
    temp_path = dest_path.with_suffix(".tmp")
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Downloading {url} -> {dest_path} ...")
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "OMEGA-Model-Provisioner/1.0"},
    )
    with urllib.request.urlopen(req) as resp, temp_path.open("wb") as out_f:
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        while chunk := resp.read(65536):
            out_f.write(chunk)
            downloaded += len(chunk)
            if total > 0:
                percent = (downloaded / total) * 100.0
                sys.stdout.write(f"\r  {downloaded:,} / {total:,} bytes ({percent:.1f}%)")
                sys.stdout.flush()
    sys.stdout.write("\n")
    temp_path.replace(dest_path)


def provision_file(
    name: str,
    target_path: Path,
    url: str,
    expected_sha256: str,
    expected_size: int,
    source_cache_dir: Path | None = None,
) -> Path:
    """Ensure file exists at target_path and passes SHA-256 verification."""
    target_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Check if already present and valid
    if verify_file(target_path, expected_sha256, expected_size):
        print(f"[OK] {name} already verified at {target_path}")
        return target_path

    # 2. Check if cached file exists (e.g. from benchmark run)
    if source_cache_dir:
        cached_path = source_cache_dir / target_path.name
        if verify_file(cached_path, expected_sha256, expected_size):
            print(f"[CACHE] Copying verified {name} from cache {cached_path} -> {target_path} ...")
            shutil.copy2(cached_path, target_path)
            if verify_file(target_path, expected_sha256, expected_size):
                print(f"[OK] {name} verified after copy.")
                return target_path

    # 3. Download from authoritative release URL
    download_with_progress(url, target_path)

    # 4. Strict SHA-256 verification
    actual_sha = compute_sha256(target_path)
    if actual_sha.lower() != expected_sha256.lower():
        if target_path.exists():
            target_path.unlink()
        raise ValueError(
            f"Checksum mismatch for {name}: expected {expected_sha256}, got {actual_sha}"
        )

    print(f"[OK] {name} downloaded and verified (SHA-256: {actual_sha}).")
    return target_path


def provision_kokoro_models(
    target_dir: Path | str | None = None,
    source_cache_dir: Path | str | None = None,
) -> dict[str, Path]:
    """Provision Kokoro-82M ONNX model and voice pack into target_dir."""
    t_dir = Path(target_dir) if target_dir else DEFAULT_MODEL_DIR
    c_dir = Path(source_cache_dir) if source_cache_dir else BENCHMARK_CACHE_DIR
    t_dir.mkdir(parents=True, exist_ok=True)

    model_path = t_dir / "kokoro-v1.0.fp16.onnx"
    voices_path = t_dir / "voices-v1.0.bin"

    m_path = provision_file(
        "kokoro-v1.0.fp16.onnx",
        model_path,
        KOKORO_MODEL_URL,
        KOKORO_MODEL_SHA256,
        KOKORO_MODEL_SIZE,
        source_cache_dir=c_dir,
    )
    v_path = provision_file(
        "voices-v1.0.bin",
        voices_path,
        KOKORO_VOICES_URL,
        KOKORO_VOICES_SHA256,
        KOKORO_VOICES_SIZE,
        source_cache_dir=c_dir,
    )
    return {"model_path": m_path, "voices_path": v_path}


def main() -> None:
    parser = argparse.ArgumentParser(description="Provision Kokoro TTS ONNX model files.")
    parser.add_argument(
        "--target-dir",
        type=Path,
        default=DEFAULT_MODEL_DIR,
        help="Destination directory for model files (default: /app/models/tts/kokoro)",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=BENCHMARK_CACHE_DIR,
        help="Local benchmark cache directory (default: /tmp/omega_tts_benchmark/kokoro)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Do not copy from local cache; download directly from upstream release",
    )
    args = parser.parse_args()

    cache_dir = None if args.no_cache else args.cache_dir

    print("=== OMEGA Kokoro Model Provisioning ===")
    print(f"Target Directory: {args.target_dir}")
    print(f"Cache Directory:  {cache_dir}")
    res = provision_kokoro_models(args.target_dir, cache_dir)
    print("\nProvisioning complete:")
    for k, p in res.items():
        print(f"  {k}: {p} ({p.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
