"""
Tracks loaded projects: maps a source file to a cached .sqlite dump, keyed
so we skip re-dumping (which takes a minute or two) when the source file
hasn't changed since the last load.
"""
import hashlib
import json
import os
import shutil
import time

from . import appconfig, locate, mdf_dump, sessions, study_result

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache")


def _project_id_for(db_path: str, session_id: str = sessions.SHARED) -> str:
    """Identity of a cached project.

    The session is part of the hash, not decoration. Without it two visitors
    who upload files with the same name land on the same cache entry and
    overwrite each other's work, and a project id from one session addresses
    another session's data.
    """
    basis = f"{session_id or sessions.SHARED}|{os.path.abspath(db_path).lower()}"
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


def _manifest_path(project_id: str) -> str:
    return os.path.join(CACHE_DIR, project_id + ".json")


def _sqlite_path(project_id: str) -> str:
    return os.path.join(CACHE_DIR, project_id + ".sqlite")


# --------------------------------------------------------------------------
# Durable copies of derived caches
#
# Cloud Run's filesystem lives and dies with the instance, and the instance
# goes away a few minutes after the last request. Without somewhere durable to
# put them, a visitor's project evaporates while they are still reading it -
# not an edge case, the normal experience of coming back after a coffee.
#
# So each derived cache is mirrored to object storage and pulled back when an
# instance finds it does not have one. Under lite mode that is ~3.4 MB per
# study, which makes this cheap enough not to think about. Keys are prefixed
# by session so a session's projects can be listed without a local index, and
# so the bucket lifecycle rule can expire them as a group.
# --------------------------------------------------------------------------

_remote = None


def set_remote(storage):
    """Attach object storage. Left unset locally, where the disk is durable."""
    global _remote
    _remote = storage


def _remote_keys(session_id: str, project_id: str):
    base = f"caches/{session_id or sessions.SHARED}/{project_id}"
    return base + ".json", base + ".sqlite"


def _push_remote(manifest: dict):
    if _remote is None:
        return
    mkey, skey = _remote_keys(manifest.get("session_id"), manifest["project_id"])
    tmp = manifest["sqlite_path"] + ".manifest.json"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(manifest, f)
        _remote.upload_from(tmp, mkey)
        _remote.upload_from(manifest["sqlite_path"], skey)
    except Exception:
        # A cache that fails to mirror is still usable on this instance; it
        # just will not survive the instance. Better degraded than a failed
        # import the user has already waited for.
        pass
    finally:
        if os.path.isfile(tmp):
            os.remove(tmp)


def _pull_manifest(session_id: str, project_id: str):
    """Restore a manifest this instance has never seen. Returns it, or None."""
    if _remote is None or not session_id:
        return None
    mkey, _ = _remote_keys(session_id, project_id)
    try:
        if not _remote.exists(mkey):
            return None
        local = _manifest_path(project_id)
        _remote.download_to(mkey, local)
    except Exception:
        return None
    with open(local, "r", encoding="utf-8") as f:
        m = json.load(f)
    # The stored path came from whichever instance built it; make it ours.
    m["sqlite_path"] = _sqlite_path(project_id)
    with open(_manifest_path(project_id), "w", encoding="utf-8") as f:
        json.dump(m, f, indent=2)
    return m


def ensure_sqlite(manifest: dict) -> bool:
    """Make sure the cache file backing a manifest is on local disk, fetching
    it from object storage if this instance does not have it. False means it
    is genuinely gone."""
    path = manifest.get("sqlite_path", "")
    if path and os.path.isfile(path):
        return True
    if _remote is None:
        return False
    _, skey = _remote_keys(manifest.get("session_id"), manifest["project_id"])
    try:
        if not _remote.exists(skey):
            return False
        _remote.download_to(skey, _sqlite_path(manifest["project_id"]))
        return True
    except Exception:
        return False


def hydrate_session(session_id: str):
    """Pull down manifests for a session this instance has not served before,
    so the project list survives an instance recycling."""
    if _remote is None or not session_id:
        return
    try:
        keys = _remote.list(f"caches/{session_id}/")
    except Exception:
        return
    for key in keys:
        if not key.endswith(".json"):
            continue
        project_id = os.path.basename(key)[:-len(".json")]
        if not os.path.isfile(_manifest_path(project_id)):
            _pull_manifest(session_id, project_id)


def list_projects(session_id: str = None):
    """Projects visible to a session. Passing None lists everything, which is
    only correct for the desktop app - a hosted caller must always scope."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    hydrate_session(session_id)
    out = []
    for fn in os.listdir(CACHE_DIR):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(CACHE_DIR, fn), "r", encoding="utf-8") as f:
                m = json.load(f)
        except (OSError, ValueError):
            continue
        if session_id is not None and m.get("session_id", sessions.SHARED) != session_id:
            continue
        out.append(_public_manifest(m))
    out.sort(key=lambda m: m.get("loaded_at", 0), reverse=True)
    return out


def _public_manifest(m: dict) -> dict:
    """Strip anything the client has no business seeing. The session id is a
    bearer token, and db_path/input_path leak server filesystem layout when
    the file was uploaded rather than opened locally."""
    out = dict(m)
    out.pop("session_id", None)
    out.pop("sqlite_path", None)
    if out.get("uploaded"):
        # An uploaded file's paths are server scratch space; showing them tells
        # the user nothing and tells everyone else the layout of the container.
        out.pop("db_path", None)
        out["input_path"] = out.get("display_name", "")
    return out


def db_path_index(session_id: str = None) -> dict:
    """{lowercased db_path: project_id} for everything already analyzed.

    The board uses this to tell a tile it can open straight away from one that
    has to be analyzed first. Looking caches up by the path they were built
    from rather than by recomputing the id keeps working across changes to the
    id scheme - caches written before the session hash went into the key are
    still found, and would otherwise show as un-analyzed forever.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    hydrate_session(session_id)
    index = {}
    for fn in os.listdir(CACHE_DIR):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(CACHE_DIR, fn), "r", encoding="utf-8") as f:
                m = json.load(f)
        except (OSError, ValueError):
            continue
        if session_id is not None and m.get("session_id", sessions.SHARED) != session_id:
            continue
        db_path = m.get("db_path")
        if db_path and m.get("project_id"):
            index[os.path.abspath(db_path).lower()] = m["project_id"]
    return index


def get_manifest(project_id: str, session_id: str = None):
    """Load a manifest, enforcing session ownership.

    The check matters even though project ids are derived from the session and
    so unguessable: access control that rests on an identifier being hard to
    guess is not access control.
    """
    p = _manifest_path(project_id)
    if not os.path.isfile(p):
        # Never seen on this instance - it may still exist in object storage,
        # put there by an instance that has since gone away.
        m = _pull_manifest(session_id, project_id)
        if m is None:
            return None
    else:
        with open(p, "r", encoding="utf-8") as f:
            m = json.load(f)
    if session_id is not None and m.get("session_id", sessions.SHARED) != session_id:
        return None
    return m


def load_project(input_path: str, force: bool = False, progress_cb=None,
                 session_id: str = sessions.SHARED) -> dict:
    """Locate the real database for input_path, dump it to sqlite if the
    cache is missing/stale, and return the manifest dict."""
    located = locate.locate(input_path)
    return load_located(located, input_path=input_path, force=force,
                        progress_cb=progress_cb, session_id=session_id)


def load_located(located, input_path: str = None, force: bool = False, progress_cb=None,
                 session_id: str = sessions.SHARED, display_name: str = None,
                 uploaded: bool = False) -> dict:
    """Dump an already-resolved LocatedDatabase to sqlite if the cache is
    missing/stale, and return the manifest dict."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    session_id = session_id or sessions.SHARED

    project_id = _project_id_for(located.db_path, session_id)
    stat = os.stat(located.db_path)
    source_fingerprint = {"mtime": stat.st_mtime, "size": stat.st_size}

    existing = get_manifest(project_id, session_id)
    sqlite_path = _sqlite_path(project_id)
    if (not force and existing and existing.get("source_fingerprint") == source_fingerprint
            and ensure_sqlite(existing)):
        return existing

    if located.kind == "study":
        stats = study_result.import_study_to_sqlite(located.db_path, sqlite_path, progress_cb=progress_cb)
    else:
        stats = mdf_dump.dump_to_sqlite(
            located.kind, located.db_path, sqlite_path, progress_cb=progress_cb,
        )

    manifest = {
        "project_id": project_id,
        "session_id": session_id,
        "input_path": os.path.abspath(input_path) if input_path else located.source_path,
        "display_name": display_name or os.path.basename(located.source_path),
        "uploaded": bool(uploaded),
        "db_path": located.db_path,
        "db_kind": located.kind,
        "db_name": located.db_name,
        "note": located.note,
        "category_set": located.category_set,
        "sqlite_path": sqlite_path,
        "source_fingerprint": source_fingerprint,
        "loaded_at": time.time(),
        "stats": stats,
    }
    with open(_manifest_path(project_id), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    _push_remote(manifest)
    return manifest


def unload_project(project_id: str, session_id: str = None) -> bool:
    """Remove a loaded project's cached .sqlite/.json (and, if it was loaded
    via browser upload, the uploaded copy too). Frees disk space and drops
    it from the recent-projects list.

    Scoped by session for the same reason reads are: deleting somebody else's
    project is worse than reading it."""
    manifest = get_manifest(project_id, session_id)
    if session_id is not None and manifest is None:
        return False
    removed = False

    sqlite_path = _sqlite_path(project_id)
    if os.path.isfile(sqlite_path):
        os.remove(sqlite_path)
        removed = True

    manifest_path = _manifest_path(project_id)
    if os.path.isfile(manifest_path):
        os.remove(manifest_path)
        removed = True

    if manifest:
        db_path = os.path.abspath(manifest.get("db_path", ""))
        upload_root = os.path.join(CACHE_DIR, "uploads")
        if db_path.lower().startswith(os.path.abspath(upload_root).lower()):
            shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)
        # Otherwise the next request would pull it straight back down.
        if _remote is not None:
            for key in _remote_keys(manifest.get("session_id"), project_id):
                try:
                    _remote.delete(key)
                    removed = True
                except Exception:
                    pass

    return removed


def clear_all(session_id: str = None) -> int:
    count = 0
    for m in list_projects(session_id):
        if unload_project(m["project_id"], session_id):
            count += 1
    return count


def sweep_expired(ttl_hours: int) -> int:
    """Drop caches nobody has loaded in a while.

    Only reaches what this instance can see, so a hosted deployment should set
    the same TTL as a bucket lifecycle rule and treat this as the tidy-up that
    keeps a long-lived instance from filling its disk, not as the guarantee.
    """
    if not ttl_hours or ttl_hours <= 0:
        return 0
    cutoff = time.time() - ttl_hours * 3600
    removed = 0
    for m in list_projects(None):
        if m.get("loaded_at", 0) < cutoff:
            if unload_project(m["project_id"], None):
                removed += 1
    return removed


def session_upload_count(session_id: str) -> int:
    return sum(1 for m in list_projects(session_id) if m.get("uploaded"))
