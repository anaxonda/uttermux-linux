# UtterMux

<img src="assets/icons/io.uttermux.App.svg" width="112" alt="UtterMux jellyfish waveform logo">

[![Linux CI](https://github.com/anaxonda/uttermux-linux/actions/workflows/linux.yml/badge.svg)](https://github.com/anaxonda/uttermux-linux/actions/workflows/linux.yml)

Android system TTS engine: [`anaxonda/uttermux-android`](https://github.com/anaxonda/uttermux-android)

Use local and online text-to-speech voices everywhere on Linux.

UtterMux makes one voice catalog available to Firefox Reader View, Zotero Read
Aloud, Speech Dispatcher applications, KOReader, and a desktop shortcut for
speaking selected text. Local models stay loaded in a background broker, while
online providers use the same voice selection and language-routing rules.

> **Status:** beta. Arch Linux is the development platform; Debian trixie and
> alternate-prefix source builds run in CI. No model weights are bundled.

![UtterMux desktop voice catalog](docs/screenshots/linux-voices.png)

Maintainers can reproduce all four desktop screenshots with
`scripts/capture-linux-screenshots`. On Arch, the capture-only dependencies are
`xorg-server-xvfb`, `xorg-xwininfo`, and `imagemagick`; synthesis is not invoked.

## Why UtterMux?

Linux TTS tools usually support one engine or one application. UtterMux keeps
Speech Dispatcher as the compatibility layer and puts model management,
provider credentials, automatic language routing, cloning, caching, and
cancellation behind it.

```text
Firefox · Zotero · spd-say · KOReader · selection shortcut
                            │
                    Speech Dispatcher
                            │
                       sd_uttermux
                            │
                        uttermuxd
             ┌──────────────┼──────────────┐
        local ONNX       free network     paid APIs
   Piper/Kokoro/etc.        Edge        ElevenLabs/xAI
```

The GTK application has the same four top-level areas as the Android app:

- **Voices** — search, filter, download, preview, and choose a default voice.
- **Create voice** — create and manage Pocket, ZipVoice, or ElevenLabs clones.
- **Test & tune** — benchmark exact installed artifacts, adjust per-model
  settings, and select the tested voice.
- **Settings** — configure providers, routing, model caching, and diagnostics.

The GTK application and CLI operate on the same catalog and configuration.

## Features

- Native Firefox Web Speech API and Zotero Read Aloud integration through
  Speech Dispatcher.
- Persistent local models, bounded LRU model caching, and optional startup
  preload for the active voice.
- Local and cloud voices in one searchable catalog.
- BCP-47 language metadata, automatic language detection, per-language routes,
  and configurable fallback order.
- System-wide selected-text reading on Wayland and X11.
- Voice preview and local model downloads.
- Pocket and ZipVoice local cloning plus ElevenLabs Instant Voice Cloning.
- Tray icon that opens the normal application.
- Compatibility bridge for the existing KOReader localhost TTS plugin.
- Cancellation without changing voices halfway through an utterance.

## Models available in the Linux app

Every row below is implemented and appears in the Linux model catalog. The app
can install and run each artifact. ZipVoice does not expose a preset system
voice; it becomes selectable after the user creates or imports a voice profile.
MOSS and Qwen use maintained companion installers instead of the sherpa-onnx
archive installer. No row in this table describes Android support.

Downloads occur only after an install action. Sizes are compressed transfer
sizes; RAM is a catalog estimate used by the UI's advisory filter, not a
measurement on the reader's computer.

### Cross-platform local support

“Yes” means the released app exposes an install and synthesis path. “Profile”
means a reference recording must be configured before a system voice exists.

| Family | Linux | Android | Current boundary |
| --- | --- | --- | --- |
| Piper/VITS | Yes; generated pinned catalog | Yes; generated pinned catalog | Fixed voices |
| Inflect Nano/Micro | Nano | Nano and Micro | Fixed English voices |
| Kitten | FP16 v0.1 and INT8 v0.8 | FP16 v0.1 and INT8 v0.8 | Fixed English voices |
| Matcha | Yes | Yes | LJSpeech + Vocos artifact |
| Supertonic 3 | INT8 | INT8 | Multilingual styles |
| Pocket | Yes; presets and profiles | Yes; presets and profiles | Reference-conditioned cloning |
| Kokoro | v1.0 FP32 | v1.0 and v1.1 FP32 | INT8 and FP8 are not included |
| ZipVoice Distill | Profile; INT8 | No | Linux requires reference audio and transcript |
| MOSS-TTS-Nano | Companion adapter; FP32 | No | Android evaluation failed sustained-reader acceptance |
| Qwen3-TTS 0.6B | Companion adapter; CustomVoice | Base Q4_K_M device preview; cloning profiles | Separate persistent Linux and GGUF Android runtimes |

The hand-maintained artifact table previously in this README was incomplete.
The [generated complete model index](docs/MODELS.generated.md) is now the
authoritative table of every variant, platform, runtime, language, voice count,
download size, RAM estimate, precision, status, and license. See
[catalog architecture](docs/CATALOG.md) for how it is produced and synchronized
with Android.

## Model variants not included in the Linux app

Kokoro v1.1 INT8 is published upstream but has no UtterMux Linux catalog entry.
UtterMux also has no tested Kokoro FP8 artifact or FP8 runtime configuration.
That is an implementation status, not a claim that FP8 cannot run on other
hardware or through another ONNX Runtime execution provider. A new artifact is
added only after its model files, execution provider, runtime configuration,
checksum, license, and synthesis path are verified together.

## Online provider support

Cloud voice lists are discovered at runtime and are intentionally not frozen in
the generated local-model catalog.

| Provider | Authentication | Linux | Android |
| --- | --- | --- | --- |
| Microsoft Edge Read Aloud | none | Implemented | Implemented |
| ElevenLabs | API key | Implemented; account voices and cloning | Implemented; account voices |
| xAI / Grok | API key | Implemented | Implemented |
| OpenAI-compatible | API key, endpoint, model | Not implemented | Implemented |
| Azure Speech | resource key and region/endpoint | Not implemented | Implemented |
| Qwen / DashScope | API key, region, workspace | Local Qwen only | Implemented |
| Google Cloud TTS | restricted API key or proxy | Not implemented | Implemented |
| Amazon Polly | SigV4, Cognito, or proxy | Not implemented | Implemented |
| Deepgram | API key | Not implemented | Implemented |
| Cartesia | API key | Not implemented | Implemented |
| PlayHT | provider credentials | Not implemented | Implemented |
| Resemble | provider credentials | Not implemented | Implemented |
| Custom PCM endpoint | HTTPS endpoint and bearer token | Not implemented | Implemented |

“Implemented” means a provider adapter and voice path are present. Paid and
account-specific services still require credentials and may not be covered by
the project's public CI. They are never enabled as implicit fallbacks.

MOSS-TTS-Nano uses a persistent, cancellable two-stage adapter. Generation and
codec decoding run concurrently through bounded queues; unlike the upstream
long-form helper, UtterMux does not add fixed silence between text chunks. A
warm benchmark reached RTF 0.86 and first PCM in 0.66 seconds on the i7-8650U
test system. It is installed only by an explicit model-install action because
its 728 MiB transfer and roughly 1.4 GiB working set are substantial. See
[MOSS benchmark notes](docs/moss-benchmarks.md).

Qwen works but is too slow for continuous reading on the older i7-8650U
reference laptop. The current UtterMux adapter is a CPU path; no GPU claim is
made. Faster systems require their own benchmark before continuous-reading use.
See [Qwen benchmark notes](docs/qwen-benchmarks.md).

## Install on Arch Linux

Install the latest GitHub release with its checksum-resolved Arch package:

```sh
curl --proto '=https' --tlsv1.2 -fsSL \
  https://github.com/anaxonda/uttermux-linux/raw/main/install.sh | bash
```

The script downloads the release `PKGBUILD`, rejects unresolved checksums,
builds the pinned sherpa-onnx dependency, installs through `makepkg`, runs
`uttermux setup`, and finishes with `uttermux doctor`. Review
[`install.sh`](install.sh) before piping it to a shell. It currently supports
Arch Linux; the source procedure below remains the portable installation path.

### Build from source

Install build and runtime dependencies:

```sh
sudo pacman -S --needed \
  speech-dispatcher libspeechd rubberband onnxruntime-cpu \
  cmake ninja gcc git pkgconf python curl ffmpeg \
  python-gobject gtk4 python-numpy python-aiohttp python-certifi
```

UtterMux currently requires sherpa-onnx 1.13.6 or newer built with its TTS C
API. Once `libsherpa-onnx-c-api.so` is installed, build UtterMux:

```sh
cd uttermux
cmake -S . -B build -G Ninja
cmake --build build
ctest --test-dir build --output-on-failure
sudo cmake --install build
sudo ldconfig
uttermux setup
```

Clone or download this repository first, then run the commands from its root.
`uttermux setup` configures the Speech Dispatcher module and enables the
socket-activated broker and tray user services. Existing model paths are
migrated without deleting the old configuration.

After upgrading from a user-local prototype, check `type -a uttermux`. An old
`~/.local/bin/uttermux` can precede the packaged executable in `PATH`; rename or
remove that obsolete copy before running `uttermux setup`.

Run a health check:

```sh
uttermux doctor
spd-say 'UtterMux is ready.'
```

Restart Firefox and Zotero after first installation so they enumerate the new
system voices.

## Everyday use

Open the manager from the application menu or run:

```sh
uttermux-app
```

To test an installed local voice in the manager, open **Voices**, press
**Preview**, and watch the inline loading/playing indicator. Press **Test
model** on that voice—or open **Test & tune**—to compare CPU-thread settings.
The benchmark shows cold/warm first-audio latency, real-time factor (RTF), and
peak memory, then asks before applying its proposed setting. It neither
downloads another model nor evaluates voice quality. **Use as active voice** on
the same test row selects that exact tested voice.

Useful CLI commands:

```sh
uttermux voices
uttermux status --json
uttermux model list
uttermux model install vits-inflect-en-nano-v2
uttermux model install moss-tts-nano-100m-onnx
uttermux preview sherpa/vits-inflect-en-nano-v2/default
uttermux benchmark sherpa/vits-inflect-en-nano-v2/default --runs 3
uttermux tune sherpa/vits-inflect-en-nano-v2/default
uttermux default sherpa/vits-inflect-en-nano-v2/default
uttermux speak-selection
uttermux speak-selection --clipboard
uttermux doctor
```

Bind `uttermux speak-selection` to a desktop shortcut. It reads the current
primary selection using `wl-paste`, `xclip`, or `xsel`. The tray menu can also
read the selection or stop current speech.

For slower-loading local models such as Kokoro, enable **Settings → Global defaults →
Preload active local voice**. This spends RAM at login but removes model loading
from the first request. It cannot remove the time the model needs to synthesize
the first sentence.

Advanced performance controls are available in both Settings and the CLI:

| Setting | Fresh default | Effect |
| --- | ---: | --- |
| `local-threads` | Automatic | Uses up to 4 CPU threads per local sherpa model |
| `pocket-threads` | Automatic | Uses up to 2 CPU threads; Pocket can regress with excess parallelism |
| `local-silence-scale` | 0.2 | Scales pauses generated inside one local utterance |
| `pocket-num-steps` | 3 | Pocket quality/latency tradeoff |
| `pocket-chunk-size` | 4 | Pocket continuity/responsiveness tradeoff |
| `zipvoice-num-steps` | 4 | ZipVoice quality/latency tradeoff |
| `moss-threads` | Automatic | Uses up to 2 threads for each concurrent MOSS ONNX stage |
| `moss-batch-frames` | 4 | MOSS first-audio latency versus decode throughput |
| `external-idle-seconds` | 120 | Releases Qwen/MOSS process memory after inactivity; zero keeps it resident |
| `max-loaded-models` | Automatic | Keeps one model below 8 GiB total RAM, otherwise two |

Zero means Automatic in the config and GUI; the CLI also accepts `auto` for
thread settings. For example, `uttermux setting local-threads 2` applies a
device-specific override, while `uttermux setting local-threads auto` restores
automatic selection. Both reload the broker. The automatic policy is a safe
starting point based on engine behavior and available cores/RAM, not an attempt
to predict the fastest setting for every processor. Benchmark documents report
the exact reference hardware and should not be read as universal tuning advice.

The GUI batches all global changes and reloads only once. To change only one
installed artifact, open **Test & tune → Model settings**. Manual model settings
take precedence over a saved benchmark profile; a saved profile takes precedence
over global defaults and automatic selection. Playback buffering, model-cache
size, language routing, and cloud caching remain global.
The panel is engine-aware: all sherpa artifacts expose threads and generated
silence; Pocket adds refinement and decoder chunk size, ZipVoice adds generation
steps, and MOSS adds decode batching. Every row reports its effective value and
source and can be reset independently.

The same controls are scriptable. Values omitted from the JSON object inherit:

```sh
uttermux model-setting replace kokoro-multi-lang-v1_1 '{"threads": 4, "silence_scale": 0.15}'
uttermux model-setting list kokoro-multi-lang-v1_1
uttermux model-setting reset kokoro-multi-lang-v1_1
```

MOSS has independent settings because increasing the normal sherpa thread count
does not tune its concurrent generator/decoder pipeline.

## Model variants and custom models

Catalog entries represent concrete artifacts, not only model families. FP32,
FP16, INT8, size, or quality variants can coexist when each has a verified
runtime configuration. The manager shows transfer size, estimated working
memory, quantization, and an advisory performance class, and can sort by
download or RAM.
The manager combines those fields with detected CPU features and available
memory and labels each artifact **Recommended here**, **Likely usable**, **May
be slow**, or with a memory warning. Those labels are deterministic heuristics
derived from logical CPU count, available RAM, catalog RAM estimates, and the
catalog performance class. They are not benchmark results. The current Linux
release configures ONNX Runtime's CPU execution provider and does not implement
GPU selection. Labels do not download, disable, or hide artifacts.

Inspect the non-identifying local capability report with:

```sh
uttermux hardware --json
```

Measure broker synthesis without audio-device playback:

```sh
uttermux benchmark "Kokoro Bella" --runs 3 --json
```

Repeated MOSS and Qwen benchmarks require the model estimate plus 2 GiB of
currently available host memory. The CLI refuses a heavy run below that margin;
`--force-low-memory` is available for controlled testing where an out-of-memory
kill is acceptable.

The report contains time to first PCM, synthesis wall time, generated audio
duration, and real-time factor (RTF). Run 1 includes model initialization only
when that model was not already warm; later runs measure the warm path. The
command does not control CPU frequency, temperature, or background load.
Published results should state the machine, text, thread settings, and whether
the broker was restarted.

The **Tune** page and `uttermux tune VOICE` compare thread counts for one exact
installed artifact. Benchmark requests carry an ephemeral override and never
rewrite the active configuration. The smallest thread count within 5% of the
fastest measured RTF is proposed; apply it only after review:

```sh
uttermux tuning apply vits-inflect-en-nano-v2 2
uttermux tuning reset vits-inflect-en-nano-v2
```

Profiles are artifact-specific and record the catalog checksum, broker
protocol, and tuning-runtime revision. FP32, FP16, INT8, and GGUF variants therefore retain independent
measurements. Performance results cannot determine pronunciation, missing
phonemes, artifacts, or preferred voice quality; use the adjacent Preview
buttons to compare variants before changing the default voice.

Use [`docs/custom-models.md`](docs/custom-models.md) for a complete schema-1
Piper/VITS manifest and verification commands. The production C++ parser tests
that example. The GTK GUI does not yet import custom manifests.

## Distribution support

Arch Linux is currently the only locally tested and packaged desktop target. The core
uses standard CMake, GTK 4, Speech Dispatcher 0.12+, ONNX Runtime, Rubber Band,
Python, and systemd user services, so it should be portable to current
systemd-based distributions. Debian trixie source builds and staged installs
are exercised in CI, but no `.deb` repository is published yet. Ubuntu,
Fedora, openSUSE, NixOS, Flatpak, and non-systemd sessions are not yet
release-supported. `/usr/local` and alternate `lib64` prefixes are exercised by
the portable-prefix CI job.

## Language routing

Applications may declare a language. Otherwise, UtterMux detects sufficiently
long text and tries:

1. the selected global voice when it supports that language;
2. the exact and base-language routes configured by the user;
3. enabled providers in the configured order;
4. the cross-language fallback, if enabled.

Short or uncertain text uses the configured default language. Language tags are
always normalized to BCP-47 form such as `en-US` and `fr-FR`.

Examples:

```sh
uttermux default --language fr elevenlabs/VOICE_ID
uttermux detect 'Ceci est un paragraphe français suffisamment long.'
uttermux routes
```

## Configuring online providers

Providers and credentials can be configured in the GTK Settings page. Keys are
stored in mode-0600 files rather than the main configuration or process command
line.

```sh
# ElevenLabs
printf '%s\n' 'YOUR_API_KEY' | uttermux credential-set elevenlabs
uttermux provider enable elevenlabs

# xAI / Grok
printf '%s\n' 'YOUR_API_KEY' | uttermux credential-set grok
uttermux provider enable grok

# Edge locales exposed to desktop applications
uttermux edge-locales en-US en-GB fr-FR
uttermux provider enable edge
```

Edge uses an unofficial endpoint that may change upstream. Paid-provider use can
incur charges; UtterMux does not manage quotas or billing.

## Voice cloning

Only clone a voice when you have the necessary rights and consent.

```sh
# Pocket: one to ten useful seconds of reference audio
uttermux profile-create pocket --name 'My reader' --language en-US \
  --audio reference.wav

# ZipVoice: reference audio plus its exact transcript
uttermux profile-create zipvoice --name 'My bilingual reader' --language en-US \
  --audio reference.wav --transcript 'The exact words in the recording.'

# ElevenLabs Instant Voice Clone
uttermux elevenlabs-clone --name 'My cloud voice' --language en-US \
  --audio sample-one.wav --audio sample-two.wav --confirm-rights
```

Local profiles can be exported and imported as `.uttermux-voice` bundles:

```sh
uttermux profile-export PROFILE_ID my-reader.uttermux-voice
uttermux profile-import my-reader.uttermux-voice
```

Reference recordings are normalized to mono PCM and stored with user-only
permissions under `~/.local/share/uttermux/voice-profiles`.
Prepared runtime data is stored as named, checksummed schema-2 artifacts beside
the recording. Schema-1 voice bundles remain importable.

## KOReader desktop bridge

The optional bridge preserves the localhost API used by the existing KOReader
TTS plugin while delegating voice selection and routing to UtterMux:

```sh
systemctl --user disable --now koreader-tts-edge.service
systemctl --user enable --now uttermux-koreader.service
```

Do not run both services because both listen on port 5000. By default the bridge
follows the global UtterMux voice and language fallbacks.

## Configuration and data

| Purpose | Default location |
| --- | --- |
| Configuration | `~/.config/uttermux/config.toml` |
| Provider credentials | `~/.config/uttermux/credentials/` |
| Model manifests | `~/.config/uttermux/models.d/` |
| Downloaded models | `~/.local/share/uttermux/models/` |
| Local voice profiles | `~/.local/share/uttermux/voice-profiles/` |
| UI state | `~/.local/state/uttermux/ui.json` |

The broker owns synthesis and configuration. The GTK process can be closed;
Firefox, Zotero, KOReader, and Speech Dispatcher continue to work through the
socket-activated user service.

## Development

```sh
cmake --build build
ctest --test-dir build --output-on-failure
python -m unittest discover -s tests -v
python -m py_compile cli/uttermux daemon/uttermuxd.py ui/uttermux-app.py
```

Architecture and benchmark notes:

- [Android/desktop catalog contract](docs/DESKTOP_PARITY.md)
- [Catalog generation and synchronization](docs/CATALOG.md)
- [Catalog schema v2](docs/interop/catalog-v2.schema.json)
- [Generated complete model index](docs/MODELS.generated.md)
- [MOSS-TTS-Nano experiments](docs/moss-benchmarks.md)
- [Kokoro runtime experiments](docs/kokoro-benchmarks.md)
- [Pocket TTS runtime experiments](docs/pocket-benchmarks.md)
- [Qwen3-TTS experiments](docs/qwen-benchmarks.md)
- [Saved benchmark format](docs/benchmark-format.md)
- [Gated runtime candidates](docs/runtime-candidates.md)

Related projects and design references:

- [Speech Dispatcher](https://github.com/brailcom/speechd) defines the Linux
  SSIP boundary and external-module API.
- [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) supplies the shared local
  inference API and model exports.
- [Pied](https://github.com/Elleo/pied) demonstrated desktop Piper management
  through Speech Dispatcher.
- [voicego](https://github.com/Ravino/voicego) is an independent SSIP server
  with isolated engines and streaming PCM.
- [NekoSpeak](https://github.com/siva-sub/NekoSpeak) informed the separation of
  synthesis, bounded buffering, playback, and cancellation.
- [HayaiTTS](https://github.com/HayaiApp/HayaiTTS) is a similar Android system
  engine built around sherpa-onnx and a large offline catalog.
- [Read Aloud](https://github.com/ken107/read-aloud) covers many browser cloud
  providers; its provider contracts informed UtterMux's online adapters.
- [KOReader](https://github.com/koreader/koreader), Firefox Reader View, and
  Zotero Read Aloud are interoperability targets, not embedded components.

Contributions should keep stable voice IDs, BCP-47 language metadata,
cancellation, and application highlighting semantics intact. A provider must
not fall back after it has emitted audio, because that would mix voices within
one highlighted utterance.

## License

UtterMux is GPL-3.0-or-later. Models, provider services, and packaged
dependencies retain their own licenses and terms. See
[model and service licensing](docs/MODEL_LICENSES.md) and the
[security policy](SECURITY.md).
