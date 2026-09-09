"""Thin client for the local Voice Core STT HTTP endpoint."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenClaw Voice Core STT client")
    parser.add_argument("--input_path", required=True, help="Audio file path")
    parser.add_argument("--host", default=os.getenv("STT_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("STT_PORT", "15900")))
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()

    data = json.dumps({"path": args.input_path}).encode("utf-8")
    req = urllib.request.Request(
        f"http://{args.host}:{args.port}/stt",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=args.timeout) as resp:
            result = json.loads(resp.read())
        print(result.get("text", ""))
    except Exception as exc:
        print(f"STT error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
