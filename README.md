# UtterMux

UtterMux is a provider-neutral Linux text-to-speech stack. Firefox Reader View,
Zotero Read Aloud, `spd-say`, and desktop selection shortcuts all see one
Speech Dispatcher module while a socket-activated broker owns local models and
online connections.

```text
Firefox / Zotero / spd-say / selection shortcut
                      |
              Speech Dispatcher
                      |
                 sd_uttermux
                      |
                 uttermuxd
             /          |          \
 sherpa-onnx local   Microsoft Edge   ElevenLabs
 Kokoro/Kitten/      online voices    subscription
 Piper/Inflect
```

The module reports `END` only after it has submitted all audio for that client
utterance. This deliberately preserves Zotero's sentence highlighting instead
of hiding provider latency with cross-utterance read-ahead. Models are loaded
lazily and retained in a two-entry LRU by default. Online audio has a bounded,
memory-only cache and falls back to a configured local voice only if a request
fails before playback begins.

## Build and migrate

Arch dependencies:

```sh
sudo pacman -S --needed speech-dispatcher libspeechd rubberband \
  onnxruntime-cpu cmake ninja gcc git pkgconf python curl ffmpeg \
  python-numpy python-aiohttp python-certifi
```

Build sherpa-onnx **1.13.6 or newer** with its TTS C API, then UtterMux:

```sh
cmake -S . -B build -G Ninja
cmake --build build
ctest --test-dir build --output-on-failure
sudo cmake --install build
uttermux setup
```

`uttermux setup` copies existing `speech-dispatcher-sherpa` manifests without
altering their model paths, installs a small managed Speech Dispatcher block,
and enables `uttermux.socket`. It does not delete the old configuration. Once
the new module is verified, old `sd_sherpa` files can be removed separately.

## CLI

```sh
uttermux setup
uttermux doctor
uttermux voices
uttermux voices --language fr --provider elevenlabs
uttermux discover --provider elevenlabs --language fr --search narrator
uttermux default sherpa/vits-piper-en_US-lessac-medium/lessac
uttermux default --language fr elevenlabs/VOICE_ID
uttermux provider-default elevenlabs elevenlabs/VOICE_ID
uttermux routes
uttermux detect 'Ceci est un paragraphe français suffisamment long.'
uttermux preview sherpa/vits-inflect-en-nano-v2/default
uttermux speak-selection              # primary selection
uttermux speak-selection --clipboard
uttermux model list
uttermux model install vits-inflect-en-nano-v2
```

Bind `uttermux speak-selection` to a desktop shortcut. It uses `wl-paste`,
`xclip`, or `xsel`, whichever is available.

`uttermux-panel` provides the optional single-click Waybar control panel. It
has one searchable view with language, provider, and model filters, plus voice
selection, per-voice preview, selected-text playback, and stop actions. Searches combine
installed voices with provider catalogs: all Edge locales are immediately
usable, ElevenLabs Voice Library entries can be added with one click, and
downloadable local catalog voices can be installed from the same list. When
`gtk4-layer-shell` is used through Python GI, preload
`/usr/lib/libgtk4-layer-shell.so` so the panel anchors below Waybar.

Enable Edge for selected locales:

```sh
uttermux edge-locales en-US en-GB
uttermux provider enable edge
```

Configure ElevenLabs without placing the API key in the main config:

```sh
uttermux elevenlabs-key 'YOUR_API_KEY'
uttermux elevenlabs-voice VOICE_ID 'Display Name' en-US
uttermux provider enable elevenlabs
```

The key is stored mode 0600 under
`~/.config/uttermux/credentials/elevenlabs-api-key`. UtterMux uses ElevenLabs'
streaming 24 kHz PCM endpoint and defaults to `eleven_flash_v2_5`. A voice's
native accent is separate from its model's language capabilities, so a voice
such as Bill can be assigned to French without creating a duplicate Firefox
voice entry.

Configure xAI/Grok by saving the API key at
`~/.config/uttermux/credentials/grok-key` (mode 0600), then run:

```sh
uttermux provider enable grok
```

UtterMux discovers the current built-in Grok voice roster at broker startup.
Grok voices are multilingual and use the provider's `language=auto` mode by
default, including language changes within one request. Set
`automatic_language = false` under `[providers.grok]` to send the language
chosen by UtterMux routing instead.

## Configuration

`~/.config/uttermux/config.toml`:

```toml
schema_version = 2
default_voice = "sherpa/vits-piper-en_US-lessac-medium/lessac"
fallback_voice = "sherpa/vits-piper-en_US-lessac-medium/lessac"
max_loaded_models = 2
audio_cache_mb = 64

[providers.edge]
enabled = true
locales = ["en-US", "en-GB"]

[providers.elevenlabs]
enabled = false
model = "eleven_flash_v2_5"
credential_file = "/home/USER/.config/uttermux/credentials/elevenlabs-api-key"

[providers.grok]
enabled = false
credential_file = "/home/USER/.config/uttermux/credentials/grok-key"
automatic_language = true

[routing]
auto_detect = true
minimum_characters = 40
minimum_confidence = 0.8
default_language = "en-US"
provider_order = ["elevenlabs", "grok", "edge", "local"]
cross_language_fallback = true

[routing.voices]
fr = ["elevenlabs/VOICE_ID"]
```

Local manifests live in `~/.config/uttermux/models.d`; model data lives in
`~/.local/share/uttermux/models`. The migration reader also accepts the former
`speech-dispatcher-sherpa` manifest directory.

## KOReader bridge

The optional `uttermux-koreader.service` preserves the existing localhost:5000
`/voices`, `/`, `/play`, `/remaining`, and `/stop` API while routing synthesis
through the broker. It caches up to 20 WAVs in memory and uses PulseAudio's
`paplay` for playback.

```sh
systemctl --user disable --now koreader-tts-edge.service
systemctl --user enable --now uttermux-koreader.service
```

No KOReader plugin changes should be necessary. Keep the old service disabled
because both services use port 5000.

The bridge delegates voice selection to UtterMux by default, even if KOReader
has retained an old `server_extra_args.voice` value. The broker then tries a
language-compatible global default first, followed by the exact/base-language
route and provider fallbacks. Set `UTTERMUX_KOREADER_FOLLOW_DEFAULT=0` on the
bridge service only if an explicit KOReader voice should override the global
selection.

## Provider contract

Providers expose stable voice IDs plus BCP-47 language tags, synthesize one
client utterance into framed PCM, and honor cancellation. Adding a provider is
isolated to the broker; applications and the Speech Dispatcher module do not
change. Cloud providers must not trigger fallback after any audio has reached
the client, which prevents mixed voices within a highlighted Zotero sentence.

Declared Firefox/Zotero language metadata takes precedence. When it is absent,
UtterMux detects sufficiently long text with `py3langid`; short or uncertain
utterances use `routing.default_language`. Routing preserves a compatible
persona, then tries exact/base-language routes and one compatible voice from
each configured provider. Edge keeps all discovered locales available for
routing while only `providers.edge.locales` are exposed to Speech Dispatcher.

UtterMux is GPL-3.0-or-later. Packaged dependencies retain their upstream
licenses: `edge-tts` is LGPL-3.0 and `py3langid` is BSD-3-Clause.
