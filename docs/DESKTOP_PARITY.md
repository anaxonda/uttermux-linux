# Android/Desktop parity contract

UtterMux keeps the Linux and Android applications in separate repositories, but
they use the versioned contract in `docs/interop/catalog-v1.schema.json` as the
public vocabulary for providers, model variants, voices, profiles, and runtime
state. Stable voice IDs use `provider/model/speaker@language`; installation and
activity are runtime state and must not be encoded into static catalog records.

The desktop migration will preserve the existing Speech Dispatcher boundary.
The broker and CLI, rather than the GTK process, will own configuration and
synthesis. The required JSON commands are:

| Command | Contract result |
| --- | --- |
| `uttermux catalog --json` | providers, models, and voices |
| `uttermux status --json` | configured, routed, and active voice state |
| `uttermux profiles --json` | local clone profiles; reference paths are local-only |
| `uttermux settings-schema --json` | provider fields and safe playback controls |

The later GTK interface will mirror the Android information architecture:
Voices for discovery/defaults, Create for clone-capable local engines, and
Settings for providers, routing, storage, advanced playback, and diagnostics.
It must invoke broker/CLI operations and must not edit TOML directly.

Incompatible contract changes require a new schema version. Additive optional
fields do not. Each repository keeps a conformance fixture and validates it in
CI before accepting a schema update.
