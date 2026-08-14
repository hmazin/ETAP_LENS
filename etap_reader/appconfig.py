"""
Runtime configuration, read from the environment.

The app has two deployment shapes and they want opposite things:

- **local** (the default): the desktop case. The app runs on your machine as
  you, so browsing the filesystem and loading a file by absolute path are the
  whole point, and there is nothing to protect against - you already have the
  files.

- **hosted**: the app runs on a server that is not yours and serves people who
  are not you. The same filesystem features become an unauthenticated
  directory lister over the server's disk, and loading by path lets a caller
  name any file the process can read. Both are disabled, and upload becomes
  the only way in.

Defaulting to *local* matters: someone who clones the repo and runs `python
app.py` gets the tool they expected, and a deployment has to opt in to being
exposed rather than opt out.
"""
import os

LOCAL = "local"
HOSTED = "hosted"


def _env(name, default=""):
    return os.environ.get(name, default).strip()


def _env_int(name, default):
    try:
        return int(_env(name) or default)
    except ValueError:
        return default


def _env_list(name):
    return [v.strip() for v in _env(name).split(",") if v.strip()]


DEPLOY_MODE = (_env("ETAP_LENS_MODE", LOCAL) or LOCAL).lower()
if DEPLOY_MODE not in (LOCAL, HOSTED):
    raise SystemExit(
        f"ETAP_LENS_MODE must be '{LOCAL}' or '{HOSTED}', got {DEPLOY_MODE!r}")

IS_HOSTED = DEPLOY_MODE == HOSTED
IS_LOCAL = not IS_HOSTED

# Rejected by Flask with a 413 before the body is read into memory.
MAX_UPLOAD_MB = _env_int("ETAP_LENS_MAX_UPLOAD_MB", 300)
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

# Exact origins allowed to call the API cross-origin, e.g.
# "https://etaplens.example.com,https://www.etaplens.example.com".
CORS_ORIGINS = _env_list("ETAP_LENS_CORS_ORIGINS")

# Optional regex for preview/branch deployments, whose hostnames are generated
# per build. Anchor it to your own project prefix - a bare ".*\.vercel\.app"
# would let *anybody's* Vercel app call this API from a user's browser.
CORS_ORIGIN_REGEX = _env("ETAP_LENS_CORS_ORIGIN_REGEX")


def public_config():
    """What the frontend needs in order to render the right UI. Deliberately
    small - the server enforces every one of these regardless of what the
    client does with them."""
    return {
        "deploy_mode": DEPLOY_MODE,
        "local_filesystem": IS_LOCAL,
        "max_upload_mb": MAX_UPLOAD_MB,
    }
