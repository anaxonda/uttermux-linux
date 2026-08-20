# UtterMux

Use local and online text-to-speech voices everywhere on Linux.

UtterMux makes one voice catalog available to Firefox Reader View, Zotero Read
Aloud, Speech Dispatcher applications, KOReader, and a desktop shortcut for
speaking selected text. Local models stay loaded in a background broker, while
online providers use the same voice selection and language-routing rules.

> **Project status:** active development. The Linux desktop stack works on the
> project's Arch Linux test system, but packaging and cross-distribution testing
> are not finished. No voice model is bundled; users choose what to download.

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

The GTK application has the same three top-level areas as the Android app:

- **Voices** — search, filter, download, preview, and choose a default voice.
- **Create voice** — create and manage Pocket, ZipVoice, or ElevenLabs clones.
- **Settings** — configure providers, routing, model caching, and diagnostics.

The application is optional. Every operation also has a CLI equivalent.

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

## Supported voices

No model is downloaded automatically.

| Engine or provider | Runs | Status | Cloning | Typical model download | Notes |
| --- | --- | --- | --- | ---: | --- |
| Piper / VITS | Locally | Supported | No | 20–150 MB | Fast, dependable baseline |
| Inflect Nano v2 | Locally | Supported | No | 21 MB | Very small English voice |
| Kitten Nano INT8 | Locally | Supported | No | 30 MB | Eight English speakers |
| Kokoro 82M FP32 | Locally | Supported | No | 333 MB | Higher quality; preload recommended |
| Matcha | Locally | Downloadable | No | 77 MB | Uses a separate vocoder |
| Supertonic 3 INT8 | Locally | Downloadable | No | 129 MB | Multilingual styles |
| Pocket INT8 | Locally | Supported | Yes | 176 MB | Presets or a reference recording |
| ZipVoice Distill INT8 | Locally | Supported | Yes | 156 MB | English/Chinese; exact transcript required |
| Qwen3-TTS 0.6B CustomVoice | Local companion | Optional | Planned | ~2.4 GB | Intended for newer/faster systems |
| Microsoft Edge | Online | Supported | No | — | Free unofficial consumer endpoint |
| ElevenLabs | Online | Supported | Yes | — | Subscription and API key required |
| xAI / Grok | Online | Supported | Provider managed | — | Multilingual automatic-language mode |

Azure, Google, AWS, OpenAI, Deepgram, Cartesia, PlayHT, and Resemble have
catalog/provider scaffolding but are not advertised as working until each has
passed credentialed end-to-end tests.

MOSS-TTS-Nano is being reevaluated against its April 2026 ONNX release. A
parallel two-stage benchmark reached RTF 0.86 and first PCM in 0.66 seconds on
the i7-8650U test system; production support still needs a cancellable streaming
adapter and reader testing. See [MOSS benchmark notes](docs/moss-benchmarks.md).

Qwen works but is too slow for continuous reading on the older i7-8650U
reference laptop. Faster CPUs and supported GPUs can run it at or above real
time. See [Qwen benchmark notes](docs/qwen-benchmarks.md).

## Install on Arch Linux

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

Useful CLI commands:

```sh
uttermux voices
uttermux status --json
uttermux model list
uttermux model install vits-inflect-en-nano-v2
uttermux preview sherpa/vits-inflect-en-nano-v2/default
uttermux default sherpa/vits-inflect-en-nano-v2/default
uttermux speak-selection
uttermux speak-selection --clipboard
uttermux doctor
```

Bind `uttermux speak-selection` to a desktop shortcut. It reads the current
primary selection using `wl-paste`, `xclip`, or `xsel`. The tray menu can also
read the selection or stop current speech.

For slower-loading local models such as Kokoro, enable **Settings → Advanced →
Preload active local voice**. This spends RAM at login but removes model loading
from the first request. It cannot remove the time the model needs to synthesize
the first sentence.

Advanced performance controls are available in both Settings and the CLI:

| Setting | Recommended | Effect |
| --- | ---: | --- |
| `local-threads` | 4 | ONNX threads per local sherpa model; excessive threads can be slower |
| `local-silence-scale` | 0.2 | Scales pauses generated inside one local utterance |
| `pocket-num-steps` | 3 | Pocket quality/latency tradeoff |
| `pocket-chunk-size` | 4 | Pocket continuity/responsiveness tradeoff |
| `zipvoice-num-steps` | 4 | ZipVoice quality/latency tradeoff |
| `max-loaded-models` | 2 | Warm-model count versus RAM use |

For example, `uttermux setting local-threads 2` applies the new value and
reloads the broker. The GUI batches all advanced changes and reloads only once.
MOSS uses a separate two-stage pipeline and will receive independent generation
and decoder controls when that backend is promoted to supported status.

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

## Online providers

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
- [Catalog schema](docs/interop/catalog-v1.schema.json)
- [MOSS-TTS-Nano experiments](docs/moss-benchmarks.md)
- [Qwen3-TTS experiments](docs/qwen-benchmarks.md)

Contributions should keep stable voice IDs, BCP-47 language metadata,
cancellation, and application highlighting semantics intact. A provider must
not fall back after it has emitted audio, because that would mix voices within
one highlighted utterance.

## License

UtterMux is GPL-3.0-or-later. Models, provider services, and packaged
dependencies retain their own licenses and terms.
