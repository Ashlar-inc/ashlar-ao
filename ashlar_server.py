"""Backward-compatibility shim.

Re-exports everything from ashlar_ao.server so that existing imports
(``from ashlar_server import …`` and ``import ashlar_server``) keep working.
"""

from ashlar_ao.server import *  # noqa: F401,F403

# Private names used by tests — not covered by wildcard import.
from ashlar_ao.server import (  # noqa: F401
    _strip_ansi,
    _check_agent_ownership,
    _make_slug,
    _extract_session_cookie,
    _suggest_followup,
    _keyword_parse_command,
    _extract_question,
    _resolve_agent_refs,
    _alert_throttle,
    _get_rate_tier,
    _RATE_LIMIT_TIERS,
    _validate_workflow_specs,
    _SECRET_PATTERNS,
    _ANSI_ESCAPE_RE,
)

if __name__ == "__main__":
    from ashlar_ao.server import main
    main()
