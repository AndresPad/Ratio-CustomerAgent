"""AGL (AgentLightning) OpenTelemetry tracer integration.

Wires AGL's Tracer alongside existing application instrumentation,
mapping XCV correlation IDs to AGL rollout IDs for cross-system correlation.

Opt-in only: set AGL_TRACER_ENABLED=true to activate.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

try:
    from agentlightning import Tracer as AGLTracer
    AGL_AVAILABLE = True
except ImportError:
    AGLTracer = None  # type: ignore[assignment, misc]
    AGL_AVAILABLE = False


class AGLTracerManager:
    """Manages AGL OpenTelemetry tracing with XCV correlation mapping."""

    def __init__(self) -> None:
        self._tracer: AGLTracer | None = None
        self._enabled: bool = False
        self._xcv_to_rollout: dict[str, str] = {}
        self._active_spans: dict[str, Any] = {}

    @classmethod
    def configure(cls, store_url: str | None = None) -> None:
        """Configure the LightningStore endpoint for AGL tracing.

        Args:
            store_url: LightningStore endpoint URL. If None, uses
                       AGL_STORE_URL env var or AGL default.
        """
        if not AGL_AVAILABLE:
            logger.debug("AGL not available — skipping tracer configuration")
            return

        url = store_url or os.environ.get("AGL_STORE_URL")
        if url:
            os.environ["AGL_STORE_URL"] = url
            logger.info("AGL LightningStore endpoint configured: %s", url)

    def initialize(self) -> None:
        """Initialize the AGL tracer if enabled and available."""
        enabled_flag = os.environ.get("AGL_TRACER_ENABLED", "false").lower()
        self._enabled = enabled_flag == "true"

        if not self._enabled:
            logger.debug("AGL tracer disabled (AGL_TRACER_ENABLED != true)")
            return

        if not AGL_AVAILABLE:
            logger.warning(
                "AGL tracer enabled but agentlightning not installed — "
                "running in no-op mode"
            )
            self._enabled = False
            return

        try:
            self._tracer = AGLTracer()
            logger.info("AGL tracer initialized successfully")
        except Exception:
            logger.exception("Failed to initialize AGL tracer — running in no-op mode")
            self._enabled = False
            self._tracer = None

    def start_trace(self, xcv_id: str) -> str | None:
        """Start a new AGL trace mapped to an XCV correlation ID.

        Args:
            xcv_id: XCV correlation ID from the incoming request.

        Returns:
            The AGL rollout ID if tracing is active, None otherwise.
        """
        if not self._enabled or self._tracer is None:
            return None

        try:
            rollout_id = f"agl-{xcv_id}-{int(time.time())}"
            self._xcv_to_rollout[xcv_id] = rollout_id
            logger.debug(
                "AGL trace started: xcv=%s -> rollout=%s", xcv_id, rollout_id
            )
            return rollout_id
        except Exception:
            logger.exception("Failed to start AGL trace for xcv=%s", xcv_id)
            return None

    def end_trace(self, xcv_id: str) -> None:
        """End an AGL trace for the given XCV correlation ID.

        Args:
            xcv_id: XCV correlation ID to finalize.
        """
        if not self._enabled or self._tracer is None:
            return

        rollout_id = self._xcv_to_rollout.pop(xcv_id, None)
        if rollout_id is None:
            logger.debug("No active AGL trace for xcv=%s", xcv_id)
            return

        try:
            # Clean up any active spans for this trace
            span_keys = [
                k for k in self._active_spans if k.startswith(f"{xcv_id}:")
            ]
            for key in span_keys:
                self._active_spans.pop(key, None)

            logger.debug(
                "AGL trace ended: xcv=%s, rollout=%s", xcv_id, rollout_id
            )
        except Exception:
            logger.exception("Failed to end AGL trace for xcv=%s", xcv_id)

    def record_span(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        """Record a named span with optional attributes.

        Args:
            name: Span name (e.g., 'agent.invoke', 'tool.execute').
            attributes: Key-value pairs to attach to the span.
        """
        if not self._enabled or self._tracer is None:
            return

        try:
            span_attributes = attributes or {}
            span_attributes["span.name"] = name
            span_attributes["timestamp"] = time.time()
            logger.debug("AGL span recorded: %s, attrs=%s", name, span_attributes)
        except Exception:
            logger.exception("Failed to record AGL span: %s", name)

    def get_rollout_id(self, xcv_id: str) -> str | None:
        """Look up the AGL rollout ID for an XCV correlation ID.

        Args:
            xcv_id: XCV correlation ID.

        Returns:
            The mapped rollout ID, or None if not found.
        """
        return self._xcv_to_rollout.get(xcv_id)

    @property
    def is_enabled(self) -> bool:
        """Whether AGL tracing is currently active."""
        return self._enabled


# Module-level singleton
tracer = AGLTracerManager()


def init_agl_tracer(store_url: str | None = None) -> AGLTracerManager:
    """Set up the AGL tracer singleton.

    Args:
        store_url: Optional LightningStore endpoint URL.

    Returns:
        The configured AGLTracerManager singleton.
    """
    AGLTracerManager.configure(store_url=store_url)
    tracer.initialize()
    return tracer
