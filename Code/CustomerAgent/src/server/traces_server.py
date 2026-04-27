"""
Standalone trace server — serves Log Analytics trace events for the
Investigation Reasoning Flow UI.

Uses the same routes from traces_api.py (which includes /api/traces/{xcv}/stream)
but avoids importing agent_framework or other heavy deps.

Run:
    cd Code/CustomerAgent/src
    python -m uvicorn server.traces_server:app --port 8503 --reload
"""
import logging
import os
import sys

# Path setup so service-local imports resolve.
_SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_SRC_DIR, "..", ".env"))
except ImportError:
    pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Traces Server (standalone)", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:3010", "http://localhost:3010",
                   "http://127.0.0.1:3000", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "traces-standalone"}


# Import the full traces router (includes /api/traces/{xcv}, /stream, /health)
from server.traces_api import register_traces_routes  # noqa: E402
register_traces_routes(app)

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8503"))
    uvicorn.run("server.traces_server:app", host="127.0.0.1", port=port, reload=True)
