# Custom local models

UtterMux reads local model manifests from `~/.config/uttermux/models.d/*.toml`.
The manifest names existing files; it does not download data or run commands.
Schema 1 supports the sherpa-onnx engines implemented by the installed broker.

## Piper/VITS example

Place the model files in `~/.local/share/uttermux/models/custom-piper/`:

```text
custom-piper/
├── model.onnx
├── tokens.txt
└── espeak-ng-data/
```

Create `~/.config/uttermux/models.d/custom-piper.toml`:

```toml
schema_version = 1
id = "custom-piper"
engine = "vits"
root = "~/.local/share/uttermux/models/custom-piper"
provider = "cpu"
num_threads = 2
length_scale = 1.0
noise_scale = 0.667
noise_scale_w = 0.8

[files]
model = "model.onnx"
tokens = "tokens.txt"
data_dir = "espeak-ng-data"

[[voice]]
id = "default"
name = "Custom Piper"
language = "en-US"
speaker_id = 0
```

Paths under `[files]` are resolved relative to `root`. Voice languages must be
BCP-47 tags such as `en-US`, not locale names such as `en_US`. Voice IDs and
display names must be unique across the loaded manifests.

Restart the broker, then check enumeration and synthesis:

```sh
systemctl --user restart uttermux.service speech-dispatcher.service
uttermux voices --search "Custom Piper"
uttermux benchmark "Custom Piper" --runs 3
```

The C++ test suite constructs this exact layout and passes the manifest through
the production parser. Missing files, unsupported engines, malformed BCP-47
tags, duplicate voices, non-CPU providers, and invalid numeric ranges are
rejected.

## Importer scope

A local-directory GUI importer can reuse this parser and is a contained change:
select a manifest, validate it, copy or reference its model directory, then
reload the broker. A network catalog is a separate security boundary. It needs
HTTPS-only fixed URLs, SHA-256 checksums, bounded extraction, license metadata,
collision handling, rollback, and a schema migration policy. The current GUI
does not import either form; catalog downloads and hand-written manifests are
the supported paths.
