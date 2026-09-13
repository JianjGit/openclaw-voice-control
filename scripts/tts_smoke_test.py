from __future__ import annotations

import argparse
import os
import tempfile
import wave
from pathlib import Path

from openclaw_voice_control.config import load_config
from openclaw_voice_control.tts import WindowsTTS
from openclaw_voice_control.vits_backend import ClassicVITSEngine


def check_vits(config, text: str, keep_wav: bool) -> None:
    """Load the configured local VITS model once and generate a WAV without Gateway."""
    temp_dir = config.tts.vits.temp_dir or (config.app.base_dir / "tmp" / "tts")
    Path(temp_dir).mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="vits_smoke_", suffix=".wav", dir=temp_dir)
    os.close(fd)
    path = Path(name)
    engine = ClassicVITSEngine(config.tts.vits)
    keep_path = False
    try:
        engine.open()
        engine.synthesize_to_wav(text, path)
        with wave.open(str(path), "rb") as wav_file:
            frames = wav_file.getnframes()
            rate = wav_file.getframerate()
            channels = wav_file.getnchannels()
        if frames <= 0:
            raise RuntimeError("VITS smoke test generated an empty WAV")
        print(
            "VITS OK: "
            f"speaker_id={engine.speaker_id} rate={rate} channels={channels} "
            f"frames={frames} path={path}"
        )
        keep_path = keep_wav
        if keep_wav:
            print("Keeping generated WAV for manual listening.")
    finally:
        engine.close()
        if not keep_path:
            path.unlink(missing_ok=True)


def check_sapi(config, text: str, speak: bool) -> None:
    """Open the existing Windows SAPI backend without touching Gateway."""
    original_provider = config.tts.provider
    config.tts.provider = "windows_sapi"
    try:
        backend = WindowsTTS(config.tts)
    finally:
        config.tts.provider = original_provider

    try:
        backend.open()
        print("Windows SAPI OK: backend opened successfully")
        if speak:
            if not backend.speak(text, lambda: False):
                raise RuntimeError("Windows SAPI playback did not finish successfully")
            print("Windows SAPI OK: sample utterance completed")
    finally:
        backend.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="TTS smoke tests that do not construct OpenClawClient or connect to Gateway"
    )
    parser.add_argument("--config", default=None, help="Path to local YAML config")
    parser.add_argument(
        "--provider",
        choices=("vits", "windows_sapi", "all"),
        default="all",
    )
    parser.add_argument("--text", default="你好，这是 VITS 语音合成测试。")
    parser.add_argument("--keep-wav", action="store_true")
    parser.add_argument("--speak-sapi", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    if args.provider in {"vits", "all"}:
        check_vits(config, args.text, args.keep_wav)
    if args.provider in {"windows_sapi", "all"}:
        check_sapi(config, args.text, args.speak_sapi)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
