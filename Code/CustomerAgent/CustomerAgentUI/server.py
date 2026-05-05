"""Static file server for the CustomerAgentUI.

Serves the SPA on port 5020 and proxies /api/* requests to the
CustomerAgent FastAPI service on port 8020.

Usage:
    python server.py
    # Then open http://localhost:5020 in your browser

Environment variables:
    UI_PORT   — port for this server (default: 5020)
    API_BASE  — backend base URL  (default: http://127.0.0.1:8020)
"""

import http.server
import json
import os
import socketserver
import urllib.request
import urllib.error
from pathlib import Path

UI_PORT = int(os.environ.get("UI_PORT", "5020"))
API_BASE = os.environ.get("API_BASE", "http://127.0.0.1:8503")
UI_DIR = Path(__file__).resolve().parent


class UIHandler(http.server.SimpleHTTPRequestHandler):
    """Request handler that serves static files and proxies API calls.

    Static files are served from the directory containing this script.
    Any path starting with /api/ or /health is reverse-proxied to the
    backend (API_BASE).  SSE responses (text/event-stream) are streamed
    through in real time.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(UI_DIR), **kwargs)

    # Suppress per-request log noise
    def log_message(self, fmt, *args):
        if "/api/" not in str(args[0]) and "/health" not in str(args[0]):
            return
        super().log_message(fmt, *args)

    def do_OPTIONS(self):
        """Handle CORS preflight requests."""
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_GET(self):
        # Proxy API and health requests to the backend
        if self.path.startswith("/api/") or self.path == "/health":
            self._proxy_get()
            return
        # SPA fallback: serve index.html for non-file routes.
        # Strip query string before checking file existence (e.g. ?v=2 cache busters).
        clean_path = self.path.split("?", 1)[0].split("#", 1)[0]
        file_path = UI_DIR / clean_path.lstrip("/")
        if not file_path.exists() or file_path.is_dir():
            # Don't fallback for actual static asset directories
            if not any(clean_path.startswith(p) for p in ["/views/", "/components/", "/lib/"]):
                self.path = "/index.html"
        super().do_GET()

    def end_headers(self):
        """Disable caching for static assets during development."""
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def do_POST(self):
        if self.path == "/api/sandbox/run":
            from sandbox_api import handle_sandbox_request
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length else b""
            handle_sandbox_request(self, body)
            return
        if self.path == "/api/learning/start":
            from learning_api import handle_learning_request
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length else b""
            handle_learning_request(self, body)
            return
        if self.path.startswith("/api/"):
            self._proxy_post()
            return
        self.send_error(404)

    def do_PUT(self):
        if self.path.startswith("/api/"):
            self._proxy_method("PUT")
            return
        self.send_error(404)

    def do_DELETE(self):
        if self.path.startswith("/api/"):
            self._proxy_method("DELETE")
            return
        self.send_error(404)

    def _cors_headers(self):
        """Add CORS headers to responses."""
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _proxy_method(self, method):
        """Forward PUT/DELETE requests to the backend API."""
        url = f"{API_BASE}{self.path}"
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length else None
        try:
            req = urllib.request.Request(
                url, data=body, method=method,
                headers={"Content-Type": "application/json"} if body else {},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp_body = resp.read()
                self.send_response(resp.status)
                self.send_header("Content-Type", resp.headers.get("Content-Type", "application/json"))
                self._cors_headers()
                self.end_headers()
                self.wfile.write(resp_body)
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self._cors_headers()
            self.end_headers()
            error_body = e.read() if hasattr(e, 'read') else b""
            self.wfile.write(error_body or json.dumps({"error": str(e)}).encode())
        except Exception as e:
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self._cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"Backend unavailable: {e}"}).encode())

    def _proxy_get(self):
        """Forward GET requests to the backend API."""
        url = f"{API_BASE}{self.path}"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=120) as resp:
                content_type = resp.headers.get("Content-Type", "application/json")
                # Stream SSE responses line-by-line to the client.
                # IMPORTANT: use readline() instead of read(N) — Python's
                # BufferedReader.read(N) waits until N bytes accumulate,
                # which blocks SSE events (each ~200 bytes) from reaching
                # the browser until the buffer fills.
                if "text/event-stream" in content_type:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("X-Accel-Buffering", "no")
                    self._cors_headers()
                    self.end_headers()
                    while True:
                        line = resp.readline()
                        if not line:
                            break
                        self.wfile.write(line)
                        self.wfile.flush()
                else:
                    body = resp.read()
                    self.send_response(resp.status)
                    self.send_header("Content-Type", content_type)
                    self._cors_headers()
                    self.end_headers()
                    self.wfile.write(body)
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self._cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())
        except Exception as e:
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self._cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"Backend unavailable: {e}"}).encode())

    def _proxy_post(self):
        """Forward POST requests to the backend API, with SSE streaming support."""
        url = f"{API_BASE}{self.path}"
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length else b""
        try:
            req = urllib.request.Request(
                url, data=body, method="POST",
                headers={"Content-Type": "application/json"},
            )
            # Long timeout for investigations (can take minutes)
            with urllib.request.urlopen(req, timeout=600) as resp:
                content_type = resp.headers.get("Content-Type", "application/json")
                if "text/event-stream" in content_type:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("X-Accel-Buffering", "no")
                    self._cors_headers()
                    self.end_headers()
                    # Read line-by-line for real-time SSE forwarding.
                    # See _proxy_get for why readline() is required.
                    while True:
                        line = resp.readline()
                        if not line:
                            break
                        self.wfile.write(line)
                        self.wfile.flush()
                else:
                    resp_body = resp.read()
                    self.send_response(resp.status)
                    self.send_header("Content-Type", content_type)
                    self._cors_headers()
                    self.end_headers()
                    self.wfile.write(resp_body)
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self._cors_headers()
            self.end_headers()
            error_body = e.read() if hasattr(e, 'read') else b""
            self.wfile.write(error_body or json.dumps({"error": str(e)}).encode())
        except Exception as e:
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self._cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"Backend unavailable: {e}"}).encode())


if __name__ == "__main__":
    # Use ThreadingHTTPServer so SSE streaming doesn't block static file serving
    import http.server
    server = http.server.ThreadingHTTPServer(("", UI_PORT), UIHandler)
    print(f"CustomerAgentUI serving on http://localhost:{UI_PORT}")
    print(f"Proxying /api/* to {API_BASE}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
