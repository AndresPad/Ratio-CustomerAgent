"""
Post-install patch for Agent Lightning on Windows.

Agent Lightning v0.3.x has a hard dependency on gunicorn which uses fcntl
(Unix-only). This script patches agentlightning/utils/server_launcher.py
in site-packages to make the gunicorn import conditional, allowing the
package to be imported on Windows.

Usage:
    python Code/CustomerAgent/scripts/patch_agl_windows.py

Idempotent — safe to run multiple times.
"""

import importlib.util
import re
import sys
from pathlib import Path

PATCH_SENTINEL = "_GUNICORN_AVAILABLE"

# --- Patch 1: wrap gunicorn imports in try/except ----------------------------

ORIGINAL_IMPORTS = """\
from gunicorn.app.base import BaseApplication
from gunicorn.arbiter import Arbiter"""

PATCHED_IMPORTS = """\
try:
    from gunicorn.app.base import BaseApplication
    from gunicorn.arbiter import Arbiter
    _GUNICORN_AVAILABLE = True
except ImportError:
    BaseApplication = None  # type: ignore[assignment,misc]
    Arbiter = None  # type: ignore[assignment,misc]
    _GUNICORN_AVAILABLE = False"""

# --- Patch 2: make GunicornApp class conditional -----------------------------

# Regex that matches the entire class block starting with
# `class GunicornApp(BaseApplication):` up to (and including) the last
# method's return statement. Uses a non-greedy match terminated by the
# `return self.application` line which is the final line of the class.
CLASS_PATTERN = re.compile(
    r"^(class GunicornApp\(BaseApplication\):.*?return self\.application)\n",
    re.MULTILINE | re.DOTALL,
)


def _indent_block(text: str, prefix: str = "    ") -> str:
    """Add *prefix* to every non-empty line in *text*."""
    lines = text.splitlines(keepends=True)
    return "".join(
        prefix + line if line.strip() else line for line in lines
    )


def _build_conditional_class(class_body: str) -> str:
    """Wrap *class_body* in ``if _GUNICORN_AVAILABLE:`` guard."""
    indented = _indent_block(class_body)
    return (
        f"if _GUNICORN_AVAILABLE:\n"
        f"\n"
        f"{indented}\n"
        f"\n"
        f"else:\n"
        f"    GunicornApp = None  # type: ignore[assignment,misc]\n"
    )


def main() -> int:
    spec = importlib.util.find_spec("agentlightning")
    if spec is None or spec.submodule_search_locations is None:
        print("ERROR: agentlightning is not installed in this environment.")
        return 1

    pkg_dir = Path(spec.submodule_search_locations[0])
    target = pkg_dir / "utils" / "server_launcher.py"

    if not target.exists():
        print(f"ERROR: expected file not found: {target}")
        return 1

    source = target.read_text(encoding="utf-8")

    # --- idempotency check ---------------------------------------------------
    if PATCH_SENTINEL in source and "if _GUNICORN_AVAILABLE:" in source:
        print(f"SKIP: {target} is already patched.")
        return 0

    patched = source
    changes: list[str] = []

    # -- Patch 1 --------------------------------------------------------------
    if ORIGINAL_IMPORTS in patched:
        patched = patched.replace(ORIGINAL_IMPORTS, PATCHED_IMPORTS, 1)
        changes.append("Patch 1: wrapped gunicorn imports in try/except")
    elif PATCH_SENTINEL not in patched:
        print("WARNING: could not locate original gunicorn imports — file may "
              "have an unexpected layout. Aborting.")
        return 1

    # -- Patch 2 --------------------------------------------------------------
    match = CLASS_PATTERN.search(patched)
    if match:
        original_class = match.group(1)
        replacement = _build_conditional_class(original_class)
        patched = patched[:match.start()] + replacement + patched[match.end():]
        changes.append("Patch 2: made GunicornApp class conditional on _GUNICORN_AVAILABLE")
    elif "if _GUNICORN_AVAILABLE:" in patched:
        pass  # already conditional
    else:
        print("WARNING: could not locate GunicornApp class definition. Aborting.")
        return 1

    if not changes:
        print(f"SKIP: {target} is already patched (partial match).")
        return 0

    target.write_text(patched, encoding="utf-8")

    print(f"PATCHED: {target}")
    for c in changes:
        print(f"  - {c}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
