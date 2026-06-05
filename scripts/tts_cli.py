"""OpenClaw TTS CLI — 双后端（edge-tts / SAPI5），自动降级

优先从 .env 读取默认后端和语音，CLI 参数可覆盖。

用法：
  python tts_cli.py --text "你好" --output out.mp3
  python tts_cli.py --text "你好" --output out.wav --backend sapi5 --voice kangkang

后端：
  edge_tts (默认) → Microsoft Edge 神经 TTS，走 HTTP，需联网/代理
  sapi5          → Windows 本地 SAPI5/OneCore，完全离线

.env 配置项：
  TTS_BACKEND=edge_tts|sapi5       默认后端
  EDGE_TTS_VOICE=zh-CN-YunxiNeural  edge_tts 默认语音
  FALLBACK_VOICE=huihui             SAPI5 降级语音
  EDGE_TTS_PROXY=http://127.0.0.1:7890  代理地址（可选）
"""

import argparse
import asyncio
import os
import subprocess
import sys

# ---- SAPI5 后端 ----
SAPI5_VOICES = {
    "huihui": r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech\Voices\Tokens\TTS_MS_ZH-CN_HUIHUI_11.0",
    "kangkang": r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech_OneCore\Voices\Tokens\MSTTS_V110_zhCN_KangkangM",
    "yaoyao": r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech_OneCore\Voices\Tokens\MSTTS_V110_zhCN_YaoyaoM",
}


def sapi5_speak(text: str, output: str, voice: str) -> None:
    """用 comtypes + SAPI5/OneCore 合成语音，输出 WAV"""
    from comtypes.client import CreateObject
    import comtypes.gen.SpeechLib as SAPI  # noqa: F811

    token = SAPI5_VOICES.get(voice)
    if token is None:
        print(f"未知 SAPI5 语音: {voice}，可用: {', '.join(SAPI5_VOICES)}", file=sys.stderr)
        sys.exit(1)

    out_dir = os.path.dirname(output)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    speaker = CreateObject("SAPI.SpVoice")
    sp_token = CreateObject("SAPI.SpObjectToken")
    sp_token.SetId(token, "")
    speaker.Voice = sp_token.QueryInterface(SAPI.ISpObjectToken)

    stream = CreateObject("SAPI.SpFileStream")
    stream.Open(output, 3)  # SSFMCreateForWrite
    speaker.AudioOutputStream = stream
    speaker.Speak(text, 0)
    stream.Close()


# ---- edge-tts 后端 ----
def _load_env():
    """从项目 .env 文件加载环境变量（不覆盖已有的）"""
    # 脚本位置: .../scripts/tts_cli.py → .env 在上级目录
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
    if not os.path.isfile(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and val and key not in os.environ:
                os.environ[key] = val


def edge_tts_speak(text: str, output: str, voice: str) -> None:
    """用 edge-tts 命令行合成语音，输出 mp3"""
    proxy = os.environ.get("EDGE_TTS_PROXY", "")
    cmd = [sys.executable, "-m", "edge_tts", "--voice", voice, "--text", text, "--write-media", output]
    env = os.environ.copy()
    if proxy:
        cmd += ["--proxy", proxy]
    # 同时设置环境变量代理，双重保险
    if proxy and "HTTP_PROXY" not in env:
        env["HTTP_PROXY"] = proxy
        env["HTTPS_PROXY"] = proxy

    out_dir = os.path.dirname(output)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)
    if result.returncode != 0:
        raise RuntimeError(f"edge-tts 失败: {result.stderr.strip()}")


# ---- 主入口 ----
def main() -> None:
    _load_env()

    parser = argparse.ArgumentParser(description="OpenClaw TTS CLI (dual backend)")
    parser.add_argument("--text", required=True, help="要转换的文字")
    parser.add_argument("--output", required=True, help="输出音频路径")
    parser.add_argument("--backend", default=os.environ.get("TTS_BACKEND", "edge_tts"),
                        choices=["edge_tts", "sapi5"], help="后端选择")
    parser.add_argument("--voice", default=None, help="语音名（不指定则用 .env 默认）")
    args = parser.parse_args()

    # 确定语音
    if args.voice:
        voice = args.voice
    elif args.backend == "edge_tts":
        voice = os.environ.get("EDGE_TTS_VOICE", "zh-CN-YunxiNeural")
    else:
        voice = os.environ.get("FALLBACK_VOICE", "huihui")

    # 主后端
    try:
        if args.backend == "edge_tts":
            edge_tts_speak(args.text, args.output, voice)
        else:
            sapi5_speak(args.text, args.output, voice)
        print(args.output, flush=True)
        return
    except Exception as e:
        # edge_tts 失败 → 自动降级到 SAPI5
        if args.backend == "edge_tts":
            fallback_voice = os.environ.get("FALLBACK_VOICE", "huihui")
            try:
                sapi5_speak(args.text, args.output, fallback_voice)
                print(f"[降级] edge_tts 失败({e})，已切换 SAPI5/{fallback_voice}", file=sys.stderr)
                print(args.output, flush=True)
                return
            except Exception as e2:
                print(f"TTS 全部失败: edge_tts={e}, sapi5={e2}", file=sys.stderr)
                sys.exit(1)
        else:
            print(f"SAPI5 失败: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
