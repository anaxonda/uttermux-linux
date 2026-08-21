# Benchmark records

`uttermux benchmark VOICE --runs 3 --save` writes a private JSON record under
`~/.local/share/uttermux/benchmarks/`. Use `--output PATH` to choose a location.
Playback-device latency is deliberately excluded.

Schema 2 records the UtterMux version, UTC time, stable voice/model/provider
IDs, text length, architecture, logical cores, available CPU features, memory,
per-run first-audio latency and RTF, plus median values. `continuousReading` is
true only when the measured median RTF is below 1.0; it is a measurement, not a
promise that thermal throttling or a competing reader application cannot make a
long session slower.

Benchmark records intentionally omit synthesized text, credentials, profile
paths, reference audio, host names, and machine identifiers.
