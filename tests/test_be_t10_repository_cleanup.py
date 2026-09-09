from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_be_t10_removed_legacy_runtime_paths_are_absent() -> None:
    removed = [
        "run_overlay.bat",
        "launchagents",
        "src/openclaw_voice_control/overlay_app.py",
        "src/openclaw_voice_control/state.py",
        "scripts/tts_simple.py",
        "scripts/build_host_apps.sh",
        "scripts/deploy_macos.command",
        "scripts/deploy_macos.sh",
        "scripts/doctor.sh",
        "scripts/install_macos.sh",
        "scripts/openclaw_host_launcher.m",
        "scripts/restart_service.command",
        "scripts/restart_service.sh",
        "scripts/start_overlay.sh",
        "scripts/start_service.sh",
        "scripts/uninstall_macos.command",
        "scripts/uninstall_macos.sh",
    ]
    for relative in removed:
        assert not (ROOT / relative).exists(), relative


def test_be_t10_kept_windows_tools_are_present() -> None:
    kept = [
        "scripts/tts_cli.py",
        "scripts/stt_endpoint_client.py",
        "scripts/list_audio_devices.py",
        "scripts/test_microphone.py",
        "run_service.bat",
    ]
    for relative in kept:
        assert (ROOT / relative).is_file(), relative


def test_be_t10_core_and_launchers_have_no_overlay_or_macos_runtime_chain() -> None:
    candidates = list((ROOT / "src" / "openclaw_voice_control").glob("*.py"))
    candidates += [ROOT / "pyproject.toml", ROOT / "requirements.txt", ROOT / "run_service.bat"]
    forbidden = [
        "PySide6",
        "OverlayStateManager",
        "overlay_app",
        "stop_tts.flag",
        "launchctl",
        "/System/Library/Sounds",
        "E:\\",
        "F:\\",
    ]
    for path in candidates:
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{token!r} remains in {path.relative_to(ROOT)}"
