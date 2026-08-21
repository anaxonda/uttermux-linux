# Changelog

## 0.4.0-beta.2 - 2026-08-21

- Added adaptive hardware tuning, per-model controls, benchmark reports, and heavy-model guidance.
- Added generated cross-platform catalogs and reproducible Arch release packaging.
- Improved Pocket, Kokoro, MOSS, and Qwen runtime diagnostics and memory behavior.
- Fixed stale empty Speech Dispatcher requests and VITS/Pocket contraction artifacts.
- Added the full-color tray icon and public release documentation.

## 0.4.0-beta.1 - 2026-08-21

First public beta of the Linux desktop broker and voice manager.

- Speech Dispatcher integration for Firefox, Zotero, `spd-say`, and other
  system TTS clients.
- Searchable local/online catalog, previews, downloads, language routing, and
  selected-text playback.
- Persistent sherpa-onnx engines for Piper/VITS, Inflect, Kitten, Kokoro,
  Matcha, Supertonic, Pocket, and ZipVoice models.
- Optional Edge, ElevenLabs, xAI, MOSS, and Qwen adapters.
- Pocket and ZipVoice local profile support and ElevenLabs cloning.
- GTK manager, tray launcher, CLI, diagnostics, and KOReader bridge.

Known limitations are documented in the README. Arch Linux is the primary
tested desktop; Debian trixie is source-built in CI.
