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
The runtime's current command help describes INT4 as 1.7B-only; do not interpret
a 0.6B invocation accepting that flag as a validated 0.6B INT4 benchmark.

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

The prior Android proposal was 0.6B CustomVoice with a Q5-class talker and an
aggressively quantized tokenizer, targeting roughly 0.7--0.9 GB of model data.
It was never implemented. These desktop results reinforce keeping local Android
Qwen deferred until it passes an actual sub-real-time device benchmark.
