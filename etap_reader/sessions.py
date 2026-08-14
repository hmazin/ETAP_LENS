"""
Anonymous session identity.

The hosted app has no login, but it still has to keep one visitor's uploads
away from another's. Without that, `list_projects()` hands everybody's
engineering models to whoever loads the page, and two people who both upload
a file called GM.TU1S collide on the same cache entry and overwrite each
other.

So: the browser generates a 128-bit random id, keeps it in localStorage, and
sends it as X-Session-Id. Everything cached is namespaced by it. That is a
bearer token in effect - whoever holds it can read that session's projects -
which is why it must be random rather than sequential, and why it is checked
on read rather than trusted because the id was hard to guess.

Deliberately not cookies: the frontend and API can be on different origins,
and a header sidesteps SameSite=None, credentialed CORS, and the third-party
cookie rules that are steadily tightening around exactly this pattern.

Locally there is one user - you - so no session is required and the cache is
shared, which keeps the desktop app behaving as it always has.
"""
import re
import secrets

HEADER = "X-Session-Id"

# 32-64 hex chars: 128 bits of randomness at the low end, with headroom.
_SESSION_RE = re.compile(r"^[a-f0-9]{32,64}$")

# Namespace used when sessions are not in play (the desktop app).
SHARED = "_shared"


def new_session_id() -> str:
    return secrets.token_hex(16)


def is_valid(session_id) -> bool:
    return bool(session_id) and bool(_SESSION_RE.match(str(session_id)))


def from_request(request, required: bool):
    """Pull the session id off a request.

    Returns (session_id, error). When sessions are not required - the desktop
    case - everything shares one namespace and callers need not send anything.
    """
    raw = (request.headers.get(HEADER) or "").strip().lower()
    if not required:
        return (raw if is_valid(raw) else SHARED), None
    if not raw:
        return None, f"Missing {HEADER}."
    if not is_valid(raw):
        return None, f"Malformed {HEADER}."
    return raw, None
