# Pocket TTS runtime experiments

Measurements below were collected on the Linux reference system: an Intel
Core i7-8650U (4 cores / 8 threads), 16 GiB RAM, AVX2, CPU-only ONNX Runtime.
They exclude audio-device playback.

## sherpa-onnx runtime

The installed `sherpa-onnx-pocket-tts-int8-2026-01-26` artifact was measured
with the same 76-character sentence. Each cell used a fresh broker followed by
two warm requests.

| Refinement steps | Threads | Cold RTF | Warm RTF |
| ---: | ---: | ---: | ---: |
| 1 | 1 | 1.14 | 0.63–0.64 |
| 1 | 2 | 0.95 | 0.52–0.55 |
| 1 | 4 | 1.05 | 0.58–0.62 |
| 2 | 1 | 1.07 | 0.66–0.67 |
| 2 | 2 | 0.93 | 0.57–0.61 |
| 2 | 4 | 1.12 | 0.64 |
| 3 | 1 | 1.02 | 0.66–0.68 |
| 3 | 2 | 0.90 | 0.58–0.59 |
| 3 | 4 | 1.08 | 0.65–0.67 |

UtterMux therefore defaults Pocket to two threads independently of the
four-thread default used by Kokoro and most other sherpa models. Three
refinement steps remain the desktop quality default because their warm cost was
small on this system.

## PocketTTS.cpp candidate

[PocketTTS.cpp](https://github.com/VolgaGerm/PocketTTS.cpp) was built at commit
`e801e7d` and tested with the English 2026-04 export published by
[`stephvax/pocket-tts-onnx`](https://huggingface.co/stephvax/pocket-tts-onnx).
The test used the recommended mixed graph set: INT8 transformer backbone with
FP32 flow and Mimi decoder, two flow steps, four threads, and a cached Alba
reference.

| Metric | Result |
| --- | ---: |
| Model load | 0.56–0.61 s |
| Cached first decoded chunk | 60–83 ms |
| Warm synthesis throughput | 3.2–4.7× realtime |
| Warm peak RSS | 359–379 MiB |
| Uncached reference preparation | 2.88 s; 964 MiB peak RSS |

The default EOS threshold (`-4`) clipped the fixed sentence unpredictably.
Threshold `-3` produced 3.76–4.32 seconds in five repeated trials; `0` ran away
to roughly 33 seconds. This runtime is not exposed in the application until it
passes transcript accuracy, cancellation, long-form, and system-TTS boundary
tests. The benchmark does show that its pipelined decoder and persisted voice/KV
cache are worth integrating behind the same provider interface.
