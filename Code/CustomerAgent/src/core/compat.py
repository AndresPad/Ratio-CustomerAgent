"""
Framework compatibility patches for agent-framework-orchestrations.

Patches ResponseStream.get_final_response to guard against the bug where a
ResponseStream created with no finalizer (e.g., from middleware termination in
streaming mode) returns ``list(self._updates)`` instead of an ``AgentResponse``.

The framework's ``_execute_stream()`` fallback creates
``ResponseStream(_empty_async_iterable())`` when the pipeline returns None.
Without a finalizer, ``get_final_response()`` returns ``[]``, which crashes the
executor at ``_agent_executor.py:412`` with:
    AttributeError: 'list' object has no attribute 'user_input_requests'

Import this module early (e.g., at service startup) to apply the patch.
"""
from __future__ import annotations

import logging
from typing import Any

from agent_framework import AgentResponse
from agent_framework._types import ResponseStream

logger = logging.getLogger(__name__)

_original_get_final_response = ResponseStream.get_final_response


async def _patched_get_final_response(self: Any) -> Any:
    """Wrapper that ensures get_final_response never returns a raw list."""
    result = await _original_get_final_response(self)
    if isinstance(result, list):
        logger.warning(
            "ResponseStream.get_final_response returned a list (len=%d) "
            "instead of AgentResponse — converting via from_updates(). "
            "This indicates a middleware terminated without setting a proper "
            "streaming result (e.g., MiddlewareTermination in streaming mode).",
            len(result),
        )
        result = AgentResponse.from_updates(result)
        # Fix the cached final_result so subsequent calls are consistent
        self._final_result = result
    return result


ResponseStream.get_final_response = _patched_get_final_response  # type: ignore[assignment]
logger.debug("Applied ResponseStream.get_final_response safety patch")
