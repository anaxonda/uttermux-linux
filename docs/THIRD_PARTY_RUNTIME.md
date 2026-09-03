# Private neural runtime

UtterMux binary packages include these runtime components in a private library
directory so distribution upgrades cannot change their ABI independently:

- [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx), Apache-2.0;
- [ONNX Runtime](https://github.com/microsoft/onnxruntime), MIT.

They are used only by UtterMux local neural voices. Their libraries are not
installed into the system-wide linker namespace. UtterMux itself remains
GPL-3.0-or-later.
