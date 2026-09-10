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
