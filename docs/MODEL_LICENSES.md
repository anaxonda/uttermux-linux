# Model and service licensing

UtterMux itself is GPL-3.0-or-later. Voice models and online services are
separate works with their own licenses and terms. The manager displays the
license recorded for each downloadable artifact before installation.

| Family | Code/runtime | Model or voice terms | Distribution policy |
| --- | --- | --- | --- |
| Piper / VITS | sherpa-onnx Apache-2.0 | Per voice; commonly MIT, but catalog metadata is authoritative | Download on request only |
| Inflect Nano/Micro v2 | Apache-2.0 | Apache-2.0 | Download on request only |
| Kitten Nano | Apache-2.0 tooling | Apache-2.0 for current catalog artifact | Download on request only |
| Kokoro | Apache-2.0 tooling | Apache-2.0 for current catalog artifact | Download on request only |
| Matcha / Vocos | Project-specific open licenses | Catalog entry identifies the artifact terms | Download on request only |
| Supertonic | Apache-2.0 tooling | Catalog entry identifies the artifact terms | Download on request only |
| Pocket TTS | Apache-2.0 model code | Preset/reference recordings can have separate attribution and reuse terms | Download model and presets on request only |
| ZipVoice | Apache-2.0 tooling | Model and reference recordings retain their stated terms | Download on request only |
| MOSS-TTS-Nano | Apache-2.0 | Apache-2.0 current artifacts | Downloaded on request; Linux uses a companion installer |
| Qwen3-TTS | Apache-2.0 current release | Model license and voice-use policy apply | Downloaded on request through a platform-specific installer |
| Edge, ElevenLabs, xAI and other APIs | Provider service | Provider terms, quotas, and voice-consent rules apply | No service data bundled |

Never clone or distribute a person's voice without the rights and consent
required in the relevant jurisdiction and service terms. Before a release,
`python scripts/release-audit.py` verifies that each catalog model has a
license, HTTPS source, and checksums for every directly downloaded asset.
