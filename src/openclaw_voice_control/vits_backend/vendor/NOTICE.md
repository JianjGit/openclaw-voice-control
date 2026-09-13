# Vendored classic VITS inference source

Model-matched source requested for this backend:

- https://huggingface.co/spaces/ulysses115/vits-uma-genshin-honkai

The files in this directory retain only the classic VITS inference path needed by
`openclaw-voice-control`. Package imports were changed to relative imports and
training-only discriminator/alignment/audio-preprocessing code was omitted.
The Chinese/Japanese text-cleaner logic is retained from the model-matched source.

One upstream compatibility shim globally replaced `torch.jit.script` and
`torch.jit.script_method`. That process-wide monkeypatch is deliberately omitted here
so loading the optional VITS backend cannot alter Torch/JIT behavior used by FunASR or
other components in the resident voice-control process. The VITS scripted helper itself
still uses the normal `torch.jit.script` decorator.

The Hugging Face Space declares Apache-2.0 for its VITS application source. The
text frontend contains additional upstream code from Keith Ito; its MIT license is
preserved in `text/LICENSE`.

No model weights are included. In particular, `config.json` and `G_953000.pth`
must be supplied locally at runtime and are intentionally outside this repository.
