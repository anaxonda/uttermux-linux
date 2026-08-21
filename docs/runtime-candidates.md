# Runtime candidate policy

UtterMux separates a model family from the runtime that executes a particular
artifact. A catalog entry is selectable only when its exact platform runtime,
files, checksums, cancellation behavior, and synthesis path have been tested.
Unverified integrations remain in this document rather than becoming dead
voice rows in the applications.

| Runtime | Platform being evaluated | Relevant families | Potential gain | Current gate |
| --- | --- | --- | --- | --- |
| [audio.cpp](https://github.com/0xShug0/audio.cpp) | Linux | Qwen, MOSS, Chatterbox and other large families | One C++ host with CPU, CUDA, HIP, Vulkan and Metal backends | No Android deployment contract; each backend/artifact combination still needs benchmark and cancellation tests |
| [PocketTTS.cpp](https://github.com/VolgaGerm/PocketTTS.cpp) | Linux prototype, then Android | Pocket TTS | Pipelined ONNX generation/decoding plus persistent voice and KV caches | Fast local benchmark; its graph packaging differs from sherpa-onnx, and EOS stability plus system-interface tests remain before exposure |
| [speech-android](https://github.com/soniqo/speech-android) | Android reference | Kokoro short-turn graph | Bounded graph with guarded split/retry and shared full-model weights | Distinct runtime/export; cannot be substituted into sherpa without porting its safety logic |
| [qts](https://github.com/yet-another-ai/qts) | Linux | Qwen3-TTS | GGUF/GGML transformer with an ONNX vocoder | No published UtterMux-quality latency, memory, long-form, or cancellation result |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) | Linux/Android watchlist | Qwen3-TTS | Widely deployed GGUF backend | TTS support is new and upstream Qwen repetition/EOS behavior is not yet stable enough for document reading |

## Acceptance contract

A candidate becomes downloadable only after it:

1. builds reproducibly for the named architecture;
2. verifies every artifact checksum and license;
3. emits the caller's text exactly once with valid range callbacks;
4. stops promptly and produces exactly one terminal callback;
5. survives multi-request and multi-client tests through the platform's public
   speech interface;
6. records first-audio latency, sustained RTF, peak memory, and runtime identity;
7. remains ahead of playback during a long-form thermal run before being marked
   suitable for continuous reading.

Adaptive startup buffering belongs only to playback paths owned by UtterMux
(preview and the legacy localhost adapter). Speech Dispatcher and Android
system-TTS clients
retain their original request boundaries so highlighting stays synchronized.
