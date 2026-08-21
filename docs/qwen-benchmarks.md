# Qwen3-TTS desktop experiments

Reference system, tested 2026-08-20:

- Intel Core i7-8650U, four cores/eight threads
- AVX2/FMA, no AVX-512 or VNNI
- 8 MB L3 cache and Intel UHD 620 integrated graphics
- turbo enabled, AC connected, Intel P-state maximum performance 100%
- Qwen3-TTS 0.6B CustomVoice

RTF is synthesis time divided by generated audio duration. Continuous reading
requires sustained RTF below 1.0, with additional margin for the source app.

## Official safetensors C runtime

The pinned runtime is also upstream HEAD at the time of testing. Its capability
report describes this AVX2/no-VNNI path as memory-bandwidth-bound. A fixed
moderate sentence produced these cold wall-clock results:

| Threads | Wall time | Audio | Cold wall/audio |
| ---: | ---: | ---: | ---: |
| 1 | 33.45 s | 8.96 s | 3.73 |
| 2 | 22.70 s | 8.80 s | 2.58 |
| 4 | 20.28 s | 9.12 s | 2.22 |
| 8 | 35.76 s | 9.68 s | 3.69 |

Four physical-core threads are optimal. The persistent companion removes model
startup but cannot reduce autoregressive compute below real time on this CPU.
The runtime's command help still describes INT4 as 1.7B-only, but source review
shows that the 0.6B path quantizes both the Talker and Code Predictor. A second
run used the same pinned revision, four threads, Aiden, and the CLI's default
benchmark sentence:

| Precision | Load | First audio | Audio | Generation | RTF | Peak RSS |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| INT8 | 2.46 s | 2.37 s | 5.60 s | 12.0 s | 2.15 | 2.30 GiB |
| INT4 | 3.52 s | 2.59 s | 5.44 s | 14.7 s | 2.70 | 1.89 GiB |

INT4 reduced peak memory by about 18 percent but was about 26 percent slower.
Precision therefore remains an explicit machine-specific choice; UtterMux must
not automatically equate lower precision with higher speed.

## Pre-quantized GGUF runtime

Tested with `ServeurpersoCom/qwentts.cpp` commit `a8a7716` and its matching
pre-converted files. CPU builds use four physical-core threads.

| Backend | Talker | Tokenizer/codec | RTF | Peak process RSS | Notes |
| --- | --- | --- | ---: | ---: | --- |
| CPU offline | Q4_K_M | Q4_K_M | 2.817 | 3.32 GB | Codec decode 17.81 s |
| CPU offline | Q4_K_M | Q8_0 | 2.792 | 3.36 GB | Q8 codec did not materially help |
| CPU streaming | Q4_K_M | Q8_0 | 2.741 | 2.70 GB | First semantic frame ~0.40 s |
| Intel Vulkan | Q4_K_M | Q8_0 | 3.826 | 0.95 GB process RSS | First frame ~5.22 s |

The GGUF implementation allocates an 896 MB FP32 talker KV cache at a maximum
sequence length of 4096. Streaming lowers resident memory but does not make
generation faster than playback. The UHD 620 exposes FP16 but no integer-dot or
matrix-core support; Vulkan saves reported process RSS but increases latency.

## Decision

Keep the official persistent INT8 companion as the optional desktop Qwen
backend because it is the best tested implementation here. Mark it heavy and do
not recommend it for continuous narration on this reference system. Piper,
Kitten, Kokoro, Pocket, or a cloud provider remain the practical reader routes.

Revisit local Qwen when at least one of these is available:

- AVX-512/VNNI or a cache-rich newer CPU;
- a supported discrete GPU backend;
- a runtime with a substantially smaller KV cache and faster streaming codec;
- a smaller official Qwen TTS checkpoint.

Android currently has a gated 0.6B Base Q4_K_M device-preview variant and
persists a prepared speaker embedding for each cloned voice. The model is not
recommended for reader use on the reference Galaxy S10: it did not produce its
first callback within three minutes and used about 1.5 GiB PSS. More recent,
faster phones may run it, but each runtime/quantization pair must pass the same
saved benchmark before its status can be promoted.
