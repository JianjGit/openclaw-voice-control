"""OpenClaw STT 薄客户端 — POST 到 openclaw-voice-control 常驻服务

供 OpenClaw tools.media.audio CLI 调用：
  python stt_endpoint_client.py --input_path recording.ogg
"""

import argparse
import json
import sys
import urllib.request


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenClaw STT client")
    parser.add_argument("--input_path", required=True, help="音频文件路径")
    args = parser.parse_args()

    data = json.dumps({"path": args.input_path}).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:15900/stt",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read())
        text = result.get("text", "")
        print(text)
    except Exception as e:
        print(f"STT error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
