# Local artifact catalog

This page is generated from release-pinned UtterMux catalog inputs. Do not edit it by hand.

It describes local artifacts in the shared interoperability catalog. It is not a cloud-voice list, an inventory of installed files, or a claim that every catalog voice is exposed by every platform. Applications may expand a multi-speaker artifact from its model metadata or expose a reviewed subset. A platform name means that an integration path exists; benchmark results and recommendations remain specific to an artifact, runtime, and hardware profile.

The machine-readable catalog contains 10 families, 187 artifact variants, and 2766 explicit voice records. Of those, Piper contributes 174 variants and 2707 speaker records.

## Curated runtime variants

`Voice records` counts entries stored in the shared catalog, not every speaker that a platform can derive from an artifact. Zero is expected for profile-based cloning models and platform-expanded speaker tables.

| Variant | Runtime | Platforms | Languages | Voice records | Download | Est. RAM | Precision | Release status | License |
|---|---|---|---|---:|---:|---:|---|---|---|
| [kitten-nano-en-v0_1-fp16](https://github.com/KittenML/KittenTTS) | sherpa-onnx | linux, android | en-US | 1 | 26 MiB | 180 MiB | FP16 | downloadable | Apache-2.0 |
| [kitten-nano-en-v0_8-int8](https://github.com/KittenML/KittenTTS) | sherpa-onnx | linux, android | en-US | 8 | 30 MiB | 180 MiB | INT8 | downloadable | Apache-2.0 |
| [kokoro-multi-lang-v1_0](https://huggingface.co/hexgrad/Kokoro-82M) | sherpa-onnx | linux, android | en-US, en-GB | 6 | 333 MiB | 560 MiB | FP32 | downloadable | Apache-2.0 |
| [kokoro-multi-lang-v1_1](https://k2-fsa.github.io/sherpa/onnx/tts/all/Chinese-English/kokoro-multi-lang-v1_1.html) | sherpa-onnx | android | en, zh | 0 | 348 MiB | 700 MiB | FP32 | downloadable | Apache-2.0 |
| [matcha-icefall-en_US-ljspeech](https://github.com/shivammehta25/Matcha-TTS) | sherpa-onnx | linux, android | en-US | 1 | 77 MiB | 320 MiB | FP32 | downloadable | MIT |
| [moss-tts-nano-100m-onnx](https://github.com/OpenMOSS/MOSS-TTS-Nano) | moss-onnx | linux, android | en, zh, ja, ko, de, fr, ru, pt, es, it, ar, cs, da, el, fi, hi, hu, nl, pl, tr | 18 | 728 MiB | 1400 MiB | FP32 ONNX | downloadable | Apache-2.0 |
| [qwen3-tts-0.6b-base-q4km](https://github.com/QwenLM/Qwen3-TTS) | qwen3-tts.cpp | android | en, zh, ja, ko, de, fr, ru, pt, es, it | 0 | 843 MiB | 3000 MiB | Q4_K_M | device-preview | Apache-2.0 |
| [qwen3-tts-0.6b-customvoice](https://github.com/QwenLM/Qwen3-TTS) | qwen-safetensors | linux | en, zh, ja, ko, de, fr, ru, pt, es, it | 9 | 2384 MiB | 3000 MiB | INT8 runtime | downloadable | Apache-2.0 |
| [sherpa-onnx-pocket-tts-int8-2026-01-26](https://github.com/kyutai-labs/pocket-tts) | sherpa-onnx | linux, android | en-US | 4 | 176 MiB | 420 MiB | INT8 | downloadable | Apache-2.0; reference recordings have their own terms |
| [sherpa-onnx-supertonic-3-tts-int8-2026-05-11](https://github.com/supertone-inc/supertonic) | sherpa-onnx | linux, android | en-US | 10 | 129 MiB | 420 MiB | INT8 | downloadable | OpenRAIL |
| [sherpa-onnx-zipvoice-distill-int8-zh-en-emilia](https://github.com/k2-fsa/ZipVoice) | sherpa-onnx | linux | en-US, zh-CN | 0 | 156 MiB | 650 MiB | INT8 | downloadable | Apache-2.0 |
| [vits-inflect-en-micro-v2](https://huggingface.co/owensong/Inflect-Micro-v2) | sherpa-onnx | android | en-US | 1 | 43 MiB | 120 MiB | FP32 | downloadable | Apache-2.0 |
| [vits-inflect-en-nano-v2](https://huggingface.co/owensong/Inflect-Nano-v2) | sherpa-onnx | linux, android | en-US | 1 | 21 MiB | 100 MiB | FP32 | downloadable | Apache-2.0 |

## Piper snapshot

The pinned Piper source contributes 174 variants across 55 BCP-47 language tags. 138 have checksum-pinned downloadable artifacts; the remaining 36 records preserve upstream identity but are marked `unavailable`.

The full per-variant URLs, checksums, sizes, speaker IDs, preview URLs, licenses, and platform flags are in [`catalog/v2/catalog.json`](../catalog/v2/catalog.json). Keeping the thousands of generated Piper speaker rows in JSON avoids turning this human-readable overview into an unwieldy table.

## Interpreting the fields

- **Download** is compressed transfer size rounded to MiB; zero means no verified downloadable artifact.
- **Est. RAM** is advisory catalog metadata, not a minimum requirement or a benchmark result.
- **Release status** describes catalog availability (`downloadable`, `device-preview`, or `unavailable`); it does not predict real-time performance on a particular computer or phone.
- Online providers are discovered at runtime and are documented separately in [`cloud-providers.md`](cloud-providers.md).
