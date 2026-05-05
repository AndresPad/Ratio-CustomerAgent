"""Sandbox API for the CustomerAgentUI.

Runs a hello-world Python script on the Azure Container Apps sandbox
and streams sandbox_* events as SSE for the Sandbox view.

Usage: imported by server.py for /api/sandbox/run endpoint.
"""

import json
import os
import sys
import threading
import time
import queue

from dotenv import load_dotenv

_SRC_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
)
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

# Load .env from CustomerAgent root (same file the backend uses)
_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
load_dotenv(_ENV_PATH)

_event_queue = None
_sandbox_thread = None

# Hello World script that proves the sandbox works
HELLO_WORLD_SCRIPT = '''
import sys
import datetime

print("=" * 50)
print("  SANDBOX HELLO WORLD")
print("=" * 50)
print()
print(f"Python version: {sys.version}")
print(f"Timestamp: {datetime.datetime.utcnow().isoformat()}Z")
print()

# Simple computation
numbers = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
total = sum(numbers)
avg = total / len(numbers)
print(f"Sum of 1..10: {total}")
print(f"Average: {avg}")
print()

# Write a file to /mnt/data
from pathlib import Path
output = Path("/mnt/data/hello_world.txt")
output.write_text(f"Hello from the sandbox!\\nGenerated at {datetime.datetime.utcnow().isoformat()}Z\\n")
print(f"Wrote: {output}")
print()
print("Sandbox is working!")
'''.strip()


def _emit(event_type, data):
    """Push an SSE event to the queue."""
    if _event_queue is not None:
        data["type"] = event_type
        _event_queue.put(data)


def _run_sandbox(params):
    """Execute the sandbox test in a background thread."""
    try:
        code = params.get("code") or HELLO_WORLD_SCRIPT
        filename = params.get("filename", "hello_world.py")

        # Emit code generated
        _emit("sandbox_code_generated", {
            "code": code,
            "filename": filename,
        })
        time.sleep(0.3)  # Brief pause for UI to render code

        # Emit execution started
        _emit("sandbox_execution_started", {
            "filename": filename,
        })

        # Actually execute on sandbox
        from core.sandbox.client import SandboxClient
        import asyncio

        client = SandboxClient()

        # Run async execute in a new event loop (we're in a thread)
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                client.execute(code=code, filename=filename)
            )
        finally:
            loop.close()

        # Emit execution complete
        _emit("sandbox_execution_complete", {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "files": result.files,
            "duration_seconds": result.duration_seconds,
            "success": result.success,
        })

    except Exception as exc:
        _emit("sandbox_error", {
            "error": str(exc),
            "filename": "hello_world.py",
        })
    finally:
        # Signal stream end
        _event_queue.put(None)


def handle_sandbox_request(handler, body):
    """Handle POST /api/sandbox/run — stream SSE events."""
    global _event_queue, _sandbox_thread

    params = {}
    if body:
        try:
            params = json.loads(body)
        except json.JSONDecodeError:
            pass

    _event_queue = queue.Queue()

    _sandbox_thread = threading.Thread(target=_run_sandbox, args=(params,), daemon=True)
    _sandbox_thread.start()

    # Stream SSE response
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Connection", "keep-alive")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()

    try:
        while True:
            event = _event_queue.get(timeout=300)
            if event is None:
                # Stream complete
                handler.wfile.write(b"data: [DONE]\n\n")
                handler.wfile.flush()
                break

            line = f"data: {json.dumps(event)}\n\n"
            handler.wfile.write(line.encode("utf-8"))
            handler.wfile.flush()
    except Exception:
        pass
