"""
Cloudflare Turnstile verification.

A public instance hands out signed upload URLs, and each one is permission to
put a few hundred megabytes into the bucket. Turnstile is what stops that
being a free-for-all without asking real users to log in or solve anything.

Only the upload-URL endpoint is gated. Gating page loads or reads would spend
the friction budget in the wrong place - reading is cheap, uploading is not.

Disabled unless both keys are set, so a deployment can be stood up and tested
before bot protection is wired.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def verify(token: str, secret_key: str, remote_ip: str = None, timeout: float = 8.0):
    """Returns (ok, error_message)."""
    if not token:
        return False, "Verification token missing. Reload the page and try again."

    payload = {"secret": secret_key, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip

    data = urllib.parse.urlencode(payload).encode()
    try:
        with urllib.request.urlopen(VERIFY_URL, data=data, timeout=timeout) as r:
            body = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        # Fail closed. If we cannot tell a human from a bot, the safe answer
        # for an endpoint that hands out write access to a bucket is "no".
        return False, f"Could not complete verification ({type(e).__name__}). Try again."

    if body.get("success"):
        return True, None
    codes = ", ".join(body.get("error-codes") or []) or "unknown"
    return False, f"Verification failed ({codes}). Reload the page and try again."
