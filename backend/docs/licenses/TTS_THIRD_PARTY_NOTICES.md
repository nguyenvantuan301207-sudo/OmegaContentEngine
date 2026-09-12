# OMEGA Local TTS Third-Party Notices & License Clearance

This document records the licensing posture and redistribution requirements for the local text-to-speech narration stack integrated into OMEGA.

---

## 1. Kokoro-82M Model Weights

- **Upstream Repository**: [`hexgrad/Kokoro-82M`](https://huggingface.co/hexgrad/Kokoro-82M)
- **Artifacts**:
  - `kokoro-v1.0.fp16.onnx` (Release: `model-files-v1.1`)
  - `voices-v1.0.bin` (Release: `model-files-v1.1`)
- **License**: Apache-2.0
- **Usage**: Local inference inside OMEGA backend container.
- **Commercial Status**: Permitted under Apache-2.0 terms.

---

## 2. kokoro-onnx Inference Runtime

- **Upstream Repository**: [`thewh1teagle/kokoro-onnx`](https://github.com/thewh1teagle/kokoro-onnx)
- **License**: MIT
- **Usage**: Lightweight Python ONNX runtime wrapper for Kokoro-82M.
- **Commercial Status**: Permitted under MIT terms.

---

## 3. espeak-ng / espeakng-loader (Phonemizer Dependency)

- **Upstream**: [`espeak-ng`](https://github.com/espeak-ng/espeak-ng) via `espeakng-loader` and `phonemizer`
- **License**: GPL-3.0
- **Usage**: Grapheme-to-phoneme conversion for English (US / UK) pronunciation.
- **Licensing Analysis**:
  - **In-House SaaS / Private Server Inference**: `COMMERCIAL_INFERENCE_OK`. Running speech inference on an internal server or SaaS platform does not constitute distribution under the GPL-3.0 and does not require opening private application source code.
  - **Packaged Redistribution**: `REDISTRIBUTION_REVIEW_REQUIRED`. If pre-built container images or binaries bundling `espeak-ng` are distributed or sold to third parties, the distributor must provide corresponding source code for the GPL-3.0 components as required by GPL-3.0 Section 6.
  - **No UI Exposure**: License text is maintained for developer and compliance auditing; it is not displayed in the runtime end-user UI.
