from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable


class STTServer:
    """Local HTTP adapter around the service's unified transcription callable."""

    def __init__(
        self,
        transcribe: Callable[[Path], str],
        host: str = "127.0.0.1",
        port: int = 15900,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self.transcribe = transcribe
        self.host = host
        self.port = port
        self.logger = logger or logging.getLogger("openclaw.voice_control.stt_server")
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def bound_port(self) -> int:
        server = self._server
        return int(server.server_address[1]) if server is not None else self.port

    def start(self) -> None:
        with self._lock:
            if self._server is not None:
                return
            handler = self._build_handler()
            server = ThreadingHTTPServer((self.host, self.port), handler)
            thread = threading.Thread(
                target=server.serve_forever,
                name="openclaw-stt-http",
                daemon=True,
            )
            self._server = server
            self._thread = thread
            thread.start()
        self.logger.info("STT HTTP server listening on http://%s:%d/stt", self.host, self.bound_port)

    def close(self) -> None:
        with self._lock:
            server = self._server
            thread = self._thread
            self._server = None
            self._thread = None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    def _build_handler(self):
        server_ref = self

        class STTHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                if self.path != "/stt":
                    self.send_error(404)
                    return
                try:
                    content_length = int(self.headers.get("Content-Length", 0))
                    payload = json.loads(self.rfile.read(content_length) or b"{}")
                    audio_path = payload.get("path", "")
                    path = Path(audio_path) if isinstance(audio_path, str) and audio_path else None
                    if path is None or not path.is_file():
                        self.send_error(400, "Missing or invalid path")
                        return
                    text = server_ref.transcribe(path)
                    body = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (json.JSONDecodeError, TypeError, ValueError):
                    self.send_error(400, "Invalid JSON request")
                except Exception:
                    server_ref.logger.exception("STT HTTP error")
                    self.send_error(500)

            def log_message(self, format: str, *args) -> None:
                return None

        return STTHandler
