"""Slim server bootstrap module.

Responsibilities:
  * Import side-effect modules (prompts/resources/tools) to populate MCP server registry.
  * Assemble ASGI app (with optional auth middleware).
  * Register custom REST API routes (moved to api_routes.py).
  * Provide health endpoint fallback.
  * Start uvicorn and handle graceful shutdown signals.

Transport modes:
  python server.py            → HTTP/SSE server (default)
  python server.py http       → HTTP/SSE server (explicit)
  python server.py stdio      → stdio transport (for Claude Desktop, VS Code Copilot, etc.)
"""
from __future__ import annotations
import os, sys, signal, warnings, uvicorn

# Suppress uvicorn's internal websockets deprecation warning (uvicorn ≤0.38 + websockets ≥15)
warnings.filterwarnings("ignore", message="websockets.server.WebSocketServerProtocol is deprecated", category=DeprecationWarning)

from helper.auth import wrap_app_if_enabled
from core.mcp_app import mcpserver, logger
from registry.tools import user_token_var
from helper.mcp_logger import MCPLogger, get_current_xcv, set_current_xcv, generate_xcv

# Side-effect imports to register prompts/resources/tools with mcpserver
import registry.prompts  # noqa: F401
import registry.resources  # noqa: F401
import registry.tools  # noqa: F401
from core.api_routes import register_api_routes  # REST endpoints


class UserTokenMiddleware:
    """ASGI middleware that extracts X-User-Token and X-XCV headers.

    Sets user_token_var ContextVar for SQL auth passthrough, and
    sets the XCV ContextVar for end-to-end traceability logging.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path", "")

        # Extract headers
        user_token = None
        xcv = None
        for name, value in scope.get("headers", []):
            lower_name = name.lower()
            if lower_name == b"x-user-token":
                user_token = value.decode("utf-8", errors="ignore").strip()
            elif lower_name == b"x-xcv":
                xcv = value.decode("utf-8", errors="ignore").strip()

        # Generate XCV if not provided by caller
        if not xcv:
            xcv = generate_xcv()

        # Diagnostic: log whether X-User-Token arrived (only for /mcp path)
        if path.startswith("/mcp"):
            if user_token:
                logger.info("UserTokenMiddleware: X-User-Token present (len=%d) on %s", len(user_token), path)
            else:
                logger.warning("UserTokenMiddleware: NO X-User-Token header on %s", path)

        # Set XCV for this request context
        xcv_reset = set_current_xcv(xcv)

        # Log endpoint hit
        mcp_log = MCPLogger.get_instance()
        caller_ip = ""
        if scope.get("client"):
            caller_ip = scope["client"][0] or ""
        mcp_log.log_endpoint_hit(xcv, scope.get("method", ""), path, caller_ip=caller_ip)

        try:
            if user_token:
                token_reset = user_token_var.set(user_token)
                try:
                    return await self.app(scope, receive, send)
                finally:
                    user_token_var.reset(token_reset)
            else:
                return await self.app(scope, receive, send)
        finally:
            from helper.mcp_logger import _current_xcv
            _current_xcv.reset(xcv_reset)


def _run_stdio() -> None:
    """Run the MCP server over stdin/stdout (stdio transport).

    Used by MCP-aware clients (Claude Desktop, VS Code Copilot, Cursor, etc.)
    that launch the server as a subprocess and communicate via JSON-RPC over stdio.
    """
    logger.info("Starting Ratio MCP server in stdio mode")
    mcpserver.run(transport="stdio")


def _run_http() -> None:
    host = os.environ.get("MCP_SERVER_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_SERVER_PORT", os.environ.get("PORT", "8000")))
    logger.info("Starting Ratio MCP server on %s:%d", host, port)
    mcp_app = mcpserver.http_app(path="/mcp", stateless_http=True)

    # Register REST API routes and health endpoint on the raw Starlette app
    # BEFORE wrapping with middleware (middleware wrappers lack .routes).
    try:
        register_api_routes(mcp_app)
    except Exception as e:  # pragma: no cover
        logger.warning("Failed to register custom API routes: %s", e)

    if not any(getattr(r, "path", "") == "/health" for r in getattr(mcp_app, "routes", [])):
        try:
            from starlette.responses import JSONResponse
            from starlette.routing import Route
            def _health_endpoint(_request):
                return JSONResponse({"status": "ok"})
            mcp_app.routes.append(Route("/health", _health_endpoint, methods=["GET"]))
        except Exception as e:  # pragma: no cover
            logger.warning("Failed to register /health route: %s", e)

    # Wrap with user-token extraction middleware (reads X-User-Token header → ContextVar)
    app = UserTokenMiddleware(mcp_app)
    # Wrap with auth middleware last (outermost layer)
    app = wrap_app_if_enabled(app)

    # Graceful shutdown handler
    def _handle_sigterm(signum, frame):  # type: ignore[unused-argument]
        logger.info("Received signal %s; flushing telemetry & shutting down.", signum)
        try:
            from opentelemetry import trace
            provider = trace.get_tracer_provider()
            if hasattr(provider, "shutdown"):
                provider.shutdown()  # type: ignore[attr-defined]
                logger.info("Tracer provider shutdown complete.")
        except Exception as e:  # pragma: no cover
            logger.debug("Telemetry shutdown skipped: %s", e)
            sys.exit(0)

    try:
        signal.signal(signal.SIGTERM, _handle_sigterm)
        signal.signal(signal.SIGINT, _handle_sigterm)
    except Exception as e:  # pragma: no cover
        logger.debug("Signal handlers not set: %s", e)

    # Uvicorn access logging disabled — MCP request logging handled by mcp_logger.py.
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="error",
        access_log=False,
    )


def main() -> None:
    """Entry point — select transport based on CLI argument or MCP_TRANSPORT env var."""
    transport = "http"
    if len(sys.argv) > 1:
        transport = sys.argv[1].lower()
    else:
        transport = os.getenv("MCP_TRANSPORT", "http").lower()

    if transport == "stdio":
        _run_stdio()
    else:
        _run_http()


if __name__ == "__main__":  # CLI entry
    main()
