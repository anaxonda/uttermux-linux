# Runtime candidate policy

UtterMux separates a model family from the runtime that executes a particular
artifact. A catalog entry is selectable only when its exact platform runtime,
files, checksums, cancellation behavior, and synthesis path have been tested.
Unverified integrations remain in this document rather than becoming dead
voice rows in the applications.

| Runtime | Platform being evaluated | Relevant families | Potential gain | Current gate |
| --- | --- | --- | --- | --- |
| [PocketTTS.cpp](https://github.com/VolgaGerm/PocketTTS.cpp) | Linux, then Android | Pocket TTS | Pipelined latent/decode streaming; persisted `.emb` and conditioned `.kv` caches | Its export requires `tokenizer.model` and differently packaged ONNX graphs; the current sherpa-onnx Pocket download is not directly compatible |
| [audio.cpp](https://github.com/0xShug0/audio.cpp) | Linux | Qwen, MOSS, Chatterbox and other large families | One C++ host with CPU, CUDA, HIP, Vulkan and Metal backends | No Android deployment contract; each backend/artifact combination still needs benchmark and cancellation tests |
| [PocketTTS.cpp](https://github.com/VolgaGerm/PocketTTS.cpp) | Linux prototype | Pocket TTS | Pipelined ONNX generation/decoding plus persistent voice and KV caches | Fast local benchmark; EOS stability and system-TTS tests remain before exposure |
| [Soniqo speech-core](https://github.com/soniqo/speech-android) | Android reference | Kokoro short-turn graph | Bounded 3-second graph with guarded split/retry and shared full-model weights | Distinct runtime/export; cannot be substituted into sherpa without porting its safety logic |
| [qts](https://github.com/yet-another-ai/qts) | Linux | Qwen3-TTS | GGUF/GGML transformer with an ONNX vocoder | No published UtterMux-quality latency, memory, long-form, or cancellation result |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) | Linux/Android watchlist | Qwen3-TTS | Widely deployed GGUF backend | TTS support is new and upstream Qwen repetition/EOS behavior is not yet stable enough for document reading |
| [speech-android](https://github.com/soniqo-ai/speech-android) | Android experiment | Kokoro | Fixed-shape mobile graph may improve latency on recent Snapdragon devices | Separate artifact/runtime; must preserve exact Android TTS ranges and pass sustained thermal testing |

## Acceptance contract

A candidate becomes downloadable only after it:

1. builds reproducibly for the named architecture;
2. verifies every artifact checksum and license;
3. emits the caller's text exactly once with valid range callbacks;
4. stops promptly and produces exactly one terminal callback;
5. survives Firefox/Zotero or Android system-TTS multi-client tests;
6. records first-audio latency, sustained RTF, peak memory, and runtime identity;
7. remains ahead of playback during a long-form thermal run before being marked
   suitable for continuous reading.

Adaptive startup buffering belongs only to playback paths owned by UtterMux
(preview and KOReader bridge). Speech Dispatcher and Android system-TTS clients
retain their original request boundaries so highlighting stays synchronized.
