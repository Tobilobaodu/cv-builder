"""HTTP health shim so a Celery worker satisfies Temps' HTTP-project contract.

Temps expects every project to listen on the injected PORT and answer an HTTP
health check; a bare `celery worker` process has no HTTP listener at all. This
runs the given Celery command as a subprocess and serves GET /health on PORT:
200 while that subprocess is alive, 503 once it has exited. SIGTERM is
forwarded to the subprocess so it can drain within Temps' shutdown deadline.

Usage (mirrors the existing docker-compose `command:` for each worker):
    python -m app.workers.health_shim -A app.workers.tasks worker -Q maintenance --concurrency=1 --loglevel=info
"""

import os
import signal
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_worker_process: subprocess.Popen | None = None


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/health":
            self.send_response(404)
            self.end_headers()
            return
        alive = _worker_process is not None and _worker_process.poll() is None
        self.send_response(200 if alive else 503)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}' if alive else b'{"status":"down"}')

    def log_message(self, *args) -> None:
        pass  # don't let routine health probes spam stdout logs


def main(celery_args: list[str]) -> None:
    global _worker_process
    _worker_process = subprocess.Popen([sys.executable, "-m", "celery", *celery_args])

    def _forward_sigterm(signum, frame) -> None:
        if _worker_process is not None and _worker_process.poll() is None:
            _worker_process.terminate()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _forward_sigterm)

    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), _HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    _worker_process.wait()
    sys.exit(_worker_process.returncode)


if __name__ == "__main__":
    main(sys.argv[1:])
