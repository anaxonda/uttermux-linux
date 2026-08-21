# Kokoro runtime experiments

Measurements were collected on the Linux reference system: Intel Core
i7-8650U (4 cores / 8 threads), 16 GiB RAM, AVX2, CPU-only ONNX Runtime. The
fixed input contains 76 characters and audio-device playback is excluded.

## FP32 thread scaling

The installed `kokoro-multi-lang-v1_0` model was loaded in a fresh broker for
each thread count and then invoked three times.

| Threads | Cold RTF | Warm RTF | Warm time to first audio |
| ---: | ---: | ---: | ---: |
| 1 | 2.04 | 1.41–1.46 | 6.47–6.66 s |
| 2 | 1.39 | 0.88–0.90 | 4.03–4.10 s |
| 4 | 1.16 | 0.72–0.74 | 3.29–3.36 s |

Four threads is the default. Warm throughput is faster than playback, but the
sherpa Kokoro API returns the completed chunk rather than incremental PCM, so
time to first audio remains the complete chunk synthesis time. Preloading
removes model construction from the first request; it does not remove that
per-chunk latency.

## Official v1.1 INT8 artifact

`kokoro-int8-multi-lang-v1_1` was tested in an isolated broker with four
threads. Cold RTF was 2.51; warm RTF was 2.09–2.19, with 9.14–9.58 seconds to
first audio. The runtime also reported skipped phonemes for the test sentence.
This is slower and less reliable than FP32 on the reference x86 system, and
known ARM regressions make it unsuitable as an Android default. UtterMux does
not expose this artifact as a recommended runnable variant.

## Short-turn graph

[`soniqo/speech-android`](https://github.com/soniqo/speech-android) uses a
distinct bounded graph (`kokoro-e2e-realtime.onnx`) that shares Kokoro's large
external weight file with its full graph. Its runtime applies guarded token and
output limits and recursively retries unsafe output as smaller text chunks.
Those checks are part of the correctness contract: substituting the graph into
sherpa-onnx without the matching split/retry implementation can clip or repeat
speech. It remains a candidate for an optional native runtime, not a model-file
swap.
