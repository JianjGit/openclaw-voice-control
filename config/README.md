# Configuration files

Use `config/default.example.yaml` as the tracked template and keep machine-specific runtime settings in `config/local.yaml`.

`config/local.yaml` is ignored by Git, so changing Gateway endpoints, delivery targets, audio devices, wakeword settings, or other local values will not make `git pull` stop because of local YAML edits.

## First setup

On Windows PowerShell or cmd:

```powershell
copy config\default.example.yaml config\local.yaml
```

Then edit `config/local.yaml`. `run_service.bat` also creates `config/local.yaml` automatically from the template when it does not exist.

The environment template points `VOICE_CONTROL_CONFIG` at `config/local.yaml`.

## Migrating an existing clone

Older clones may already have local edits in the tracked `config/default.yaml`. The legacy file is intentionally frozen at the pre-migration template so a clone still based on commit `9328ae6` can pull this migration without a new net remote change to that path.

After pulling, move your machine-specific values into the ignored local file and restore the tracked legacy file once:

```powershell
copy config\default.yaml config\local.yaml
git restore config/default.yaml
```

If your existing `.env` contains this old setting:

```dotenv
VOICE_CONTROL_CONFIG=config/default.yaml
```

change it to:

```dotenv
VOICE_CONTROL_CONFIG=config/local.yaml
```

`run_service.bat` explicitly uses `config/local.yaml`, so the launcher is safe even before that `.env` line is updated. Review `config/local.yaml` after copying. From then on, edit `config/local.yaml`, not `config/default.yaml`.

`config/default.yaml` is retained temporarily and frozen for backward compatibility with older scripts and clones. New runtime usage should use `config/local.yaml`, and future template changes belong only in `config/default.example.yaml`.

## Optional classic multi-speaker VITS TTS

Windows SAPI remains the default. The VITS frontend is optional and its extra Python
packages should be installed only inside this project's virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[vits]"
```

Keep model weights outside the repository. For the local model described for this
installation, put the machine-specific values in the ignored `config/local.yaml`:

```yaml
tts:
  provider: vits
  fallback: windows_sapi  # windows_sapi | none
  engine: windows_sapi5   # retained for older configuration consumers
  voice: zh-CN-HUIHUI     # used by the SAPI fallback
  vits:
    model_dir: 'E:\models\VITS语音模型'
    config_file: config.json
    checkpoint_file: G_953000.pth
    speaker: '角色A'
    speaker_id: 120
    sample_rate: 22050
    language: zh
    device: cpu
    noise_scale: 0.45
    noise_scale_w: 0.5
    length_scale: 1.28
```

`VITS_MODEL_DIR`, `VITS_SPEAKER`, `VITS_SPEAKER_ID`, and `VITS_DEVICE` can override
those local values. `TTS_PROVIDER` and `TTS_FALLBACK` can override provider selection.

When `provider: vits` is selected, the resident voice-control process loads the VITS
config and checkpoint once while the service object is starting. The speech worker then
reuses that same model for every utterance. VITS output is written only under the
project-owned `tmp/tts/` directory and `vits_*.wav` files are removed after playback.
A VITS initialization/inference error is logged and does not crash the voice-control
process; `fallback: windows_sapi` uses the existing SAPI backend, while `fallback: none`
returns silent failure for that utterance.

Gateway-free checks are available with:

```powershell
# VITS: loads the local model and produces a WAV without constructing OpenClawClient.
.\.venv\Scripts\python.exe scripts\tts_smoke_test.py --config config\local.yaml --provider vits --keep-wav

# Windows SAPI: opens the existing SAPI backend; add --speak-sapi to hear the sample.
.\.venv\Scripts\python.exe scripts\tts_smoke_test.py --config config\local.yaml --provider windows_sapi --speak-sapi
```

Changing `tts.provider`, the VITS model path, speaker, or inference parameters requires a
restart of `openclaw-voice-control`, because the VITS model is intentionally loaded only
once per process lifetime.
