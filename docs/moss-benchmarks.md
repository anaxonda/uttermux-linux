# MOSS-TTS-Nano desktop experiment

Tested on the project's i7-8650U reference machine with the official April
2026 ONNX exports, ONNX Runtime 1.29, four CPU threads, and the fixed sampling
path.

## Runtime footprint

- TTS graphs and shared weights: 641.5 MiB
- audio-tokenizer graphs and shared weights: 86.4 MiB
- total model payload: about 728 MiB
- model/session load: 5.7 seconds
- built-in presets: 18
- voice cloning: supported by the upstream model

UtterMux's fixed-sampling streaming companion loads only the four graphs used
by that path: prefill, autoregressive decode, the fixed-frame sampler, and the
streaming codec decoder. The upstream runtime otherwise creates five unused
sessions for codec encoding, full decoding, and alternate sampling paths.

With the reduced graph set, a load-only run reached its health endpoint in
5.23 seconds at 946 MiB RSS. One short synthesis peaked at 1.22 GiB RSS and
generated 4.16 seconds of audio in 4.30 seconds (RTF 1.03). This validates the
lower-footprint path; it does not establish a throughput improvement because
sampled output length and short-run timing vary.

The ONNX runtime still imports PyTorch and torchaudio for reference-audio
loading, although built-in-preset inference itself only needs ONNX Runtime,
NumPy, SentencePiece, and the model assets. A production UtterMux adapter
should remove that dependency from the ONNX-only path.

## Measurements

A short English sentence produced 4.72 seconds of stereo 48 kHz audio. Full
generation followed by one codec decode took 4.60 seconds (RTF 0.97). The first
generated audio-token frame was available after 0.27 seconds.

Incremental codec decoding is considerably more expensive:

| Decode batch | First PCM | RTF |
| ---: | ---: | ---: |
| 1 frame / 80 ms | 0.46 s | 2.01 |
| 2 frames | 0.61 s | 2.05 |
| 4 frames | 0.89 s | 1.56 |
| 8 frames | 0.95 s | 1.15 |
| 12 frames | 1.18 s | 1.06 |
| 16 frames | 1.69 s | 1.08 |
| 24 frames | 2.12 s | 1.01 |

The variation between runs is normal for sampled generation, but the result is
consistent: small low-latency codec batches cannot keep up on this CPU, while
large batches approach real time only by increasing startup latency.

## Integration finding

Upstream's `realtime_streaming_decode` option performs incremental codec work,
but its public Python `synthesize()` path accumulates the decoded blocks and
returns a completed waveform. It does not expose progressive PCM to the caller.
UtterMux would need a callback or iterator adapter, cancellation checks inside
the generation loop, and its adaptive reserve/queue before this can become a
system TTS provider.

The upstream long-text path also deliberately inserts 240 or 400 ms of silence
between model-created chunks. Those pauses must not be stacked with application
or UtterMux sentence boundaries.

### Parallel pipeline follow-up

The upstream callback performs codec decoding synchronously inside the token
generation loop. Moving frames through a queue to a separate decoder worker
changes the result significantly. With two ONNX intra-op threads and independent
generation/decoder workers:

| Decode batch | First PCM | RTF |
| ---: | ---: | ---: |
| 4 frames | 0.66 s | 0.86 |
| 8 frames | 0.91 s | 0.87 |
| 12 frames | 1.21 s | 0.85 |

Using four intra-op threads in both concurrent stages oversubscribed this
four-core/eight-thread CPU and regressed to RTF 1.18–1.27. Two threads per stage
is the appropriate starting point on this machine.

## Decision

The parallel producer/decoder adapter is now integrated as an optional local
companion. It has bounded frame and PCM queues, cancellation, persistent model
loading, four-frame codec batches, and no artificial inter-chunk silence. The
model is fast enough on the reference CPU when its stages overlap. Continuous
KOReader/Librera/Firefox testing is still required before recommending it as a
default, particularly to detect audible seams at internal 75-token boundaries.

There is currently no official INT8 or static-KV-cache ONNX artifact. Further
large throughput gains likely require a new validated export or an inference
runtime that avoids copying the growing autoregressive cache, rather than a
larger playback buffer.
