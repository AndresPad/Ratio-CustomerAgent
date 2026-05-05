"""Pipeline error taxonomy.

Each exception class represents a failure category with different
handling semantics:

| Error           | Retryable? | Handling                                  |
|-----------------|------------|-------------------------------------------|
| NetworkError    | yes        | Retry with backoff, surface as transient   |
| AuthError       | no         | Fail fast, alert (credential / permission) |
| LLMError        | sometimes  | Retry if transient, log token budget       |
| ParseError      | no         | Fallback to markdown extraction            |
| ConfigError     | no         | Fail fast at startup / load time           |
| ToolError       | depends    | Log, return empty result, continue         |

All classes inherit from ``PipelineError`` so callers can still
catch the whole family with ``except PipelineError``.
"""
from __future__ import annotations


class PipelineError(Exception):
    """Base class for all CustomerAgent pipeline errors."""

    retryable: bool = False

    def __init__(self, message: str, *, cause: BaseException | None = None) -> None:
        super().__init__(message)
        self.__cause__ = cause


class NetworkError(PipelineError):
    """Transient network / connectivity failure (DNS, socket, HTTP 5xx)."""

    retryable = True


class AuthError(PipelineError):
    """Authentication or authorisation failure (401 / 403 / token refresh)."""

    retryable = False


class LLMError(PipelineError):
    """LLM service error (rate-limit, content filter, model unavailable).

    ``retryable`` is ``True`` for transient failures (429, 503) and
    ``False`` for content-filter rejections or invalid-request errors.
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message, cause=cause)
        self.retryable = retryable
        self.status_code = status_code


class ParseError(PipelineError):
    """JSON / schema parse failure from agent output."""

    retryable = False


class ConfigError(PipelineError):
    """Configuration file load / validation failure."""

    retryable = False


class ToolError(PipelineError):
    """MCP tool or local tool execution failure.

    ``retryable`` may be ``True`` for transient tool errors (timeout,
    service unavailable) and ``False`` for argument / schema errors.
    """

    def __init__(
        self,
        message: str,
        *,
        tool_name: str = "",
        retryable: bool = False,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message, cause=cause)
        self.retryable = retryable
        self.tool_name = tool_name


def classify_exception(exc: BaseException) -> PipelineError:
    """Best-effort classification of an arbitrary exception.

    Inspects the exception type and message to return the most
    specific ``PipelineError`` subclass.  If no match is found,
    returns a generic ``PipelineError``.
    """
    if isinstance(exc, PipelineError):
        return exc

    exc_type = type(exc).__name__
    msg = str(exc).lower()

    # ── Network errors ───────────────────────────────────────────
    if isinstance(exc, (ConnectionError, OSError, TimeoutError)):
        return NetworkError(str(exc), cause=exc)
    if "timeout" in msg or "timed out" in msg:
        return NetworkError(str(exc), cause=exc)
    if any(tok in msg for tok in ("connection", "socket", "dns", "resolve")):
        return NetworkError(str(exc), cause=exc)

    # ── Auth errors ──────────────────────────────────────────────
    if any(tok in msg for tok in ("401", "403", "unauthorized", "forbidden", "credential", "token expired")):
        return AuthError(str(exc), cause=exc)
    if "authentication" in msg or "authoriz" in msg:
        return AuthError(str(exc), cause=exc)

    # ── LLM errors ───────────────────────────────────────────────
    if "rate" in msg and "limit" in msg:
        return LLMError(str(exc), retryable=True, status_code=429, cause=exc)
    if "content_filter" in msg or "content filter" in msg:
        return LLMError(str(exc), retryable=False, cause=exc)
    if exc_type in ("RateLimitError", "APIStatusError", "APIConnectionError", "APITimeoutError"):
        retryable = "rate" in msg or "429" in msg or "503" in msg or "timeout" in msg
        code = 429 if "429" in msg else (503 if "503" in msg else None)
        return LLMError(str(exc), retryable=retryable, status_code=code, cause=exc)
    if any(tok in msg for tok in ("openai", "chat completion", "model_not_found")):
        return LLMError(str(exc), retryable=False, cause=exc)

    # ── Parse errors ─────────────────────────────────────────────
    if isinstance(exc, (ValueError, KeyError)) and any(
        tok in msg for tok in ("json", "decode", "parse", "unexpected token", "schema")
    ):
        return ParseError(str(exc), cause=exc)

    # ── Config errors ────────────────────────────────────────────
    if isinstance(exc, (FileNotFoundError,)):
        return ConfigError(str(exc), cause=exc)
    if "config" in msg and ("missing" in msg or "invalid" in msg or "not found" in msg):
        return ConfigError(str(exc), cause=exc)

    # ── Fallback ─────────────────────────────────────────────────
    return PipelineError(str(exc), cause=exc)
