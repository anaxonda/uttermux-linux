# Android/Desktop parity contract

UtterMux keeps the Linux and Android applications in separate repositories, but
they use the generated versioned contract in `docs/interop/catalog-v2.schema.json` as the
public vocabulary for providers, model variants, voices, profiles, and runtime
state. Stable voice IDs use `provider/model/speaker@language`; installation and
activity are runtime state and must not be encoded into static catalog records.

The reviewed catalog sources and generator live in the
[Linux repository](https://github.com/anaxonda/uttermux-linux). Linux consumes
the reviewed TOML directly and also installs the generated schema-2 projection.
Android commits an identical generated JSON file so its Gradle build remains
offline and reproducible. Updating the Android copy is a reviewed repository
change; Android never downloads a mutable catalog during its build. Cloud voice
lists are discovered by provider adapters at runtime and are not part of this
static contract.

The desktop implementation preserves the Speech Dispatcher boundary.
The broker and CLI, rather than the GTK process, will own configuration and
synthesis. The required JSON commands are:

| Command | Contract result |
| --- | --- |
| `uttermux catalog --json` | providers, models, and voices |
| `uttermux status --json` | configured, routed, and active voice state |
| `uttermux profiles --json` | local clone profiles; reference paths are local-only |
| `uttermux settings-schema --json` | provider fields and safe playback controls |

The GTK interface mirrors the Android information architecture: Voices for
discovery/defaults, Create for Pocket/ZipVoice/ElevenLabs clone workflows, Test
& tune for artifact benchmarks and overrides, and Settings for providers,
routing, storage, advanced playback, and diagnostics.
It invokes broker/CLI operations and does not edit TOML directly. A separate
StatusNotifierItem opens the ordinary window; Waybar only needs its standard
tray module.

Local voice profiles can be exported as versioned `.uttermux-voice` bundles.
The bundle is engine-specific: Pocket carries its normalized WAV, ZipVoice
carries the WAV plus exact transcript, and Qwen may carry named prepared
speaker-embedding or ICL-prompt artifacts. Profile schema 2 permits multiple
checksummed artifacts and still imports schema-1 `artifactFile` bundles.
Unsupported bundles may be retained by another platform, but they are not
selectable until the matching runtime is installed.

Incompatible contract changes require a new schema version. Additive optional
fields do not. Each repository keeps a conformance fixture and validates it in
CI before accepting a schema update.
