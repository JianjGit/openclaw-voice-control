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

Older clones may already have local edits in the tracked `config/default.yaml`. After pulling the migration commit, copy those settings once into the ignored local file and restore the tracked legacy file:

```powershell
copy config\default.yaml config\local.yaml
git restore config/default.yaml
```

Review `config/local.yaml` after copying. From then on, edit `config/local.yaml`, not `config/default.yaml`.

`config/default.yaml` is retained temporarily for backward compatibility with older scripts and clones, but new runtime usage should use `config/local.yaml`. New template changes belong in `config/default.example.yaml`.
