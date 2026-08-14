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


def list_projects(session_id: str = None):
    """Projects visible to a session. Passing None lists everything, which is
    only correct for the desktop app - a hosted caller must always scope."""
    os.makedirs(CACHE_DIR, exist_ok=True)
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


def get_manifest(project_id: str, session_id: str = None):
    """Load a manifest, enforcing session ownership.

    The check matters even though project ids are derived from the session and
    so unguessable: access control that rests on an identifier being hard to
    guess is not access control.
    """
    p = _manifest_path(project_id)
    if not os.path.isfile(p):
        return None
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
            and os.path.isfile(sqlite_path)):
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
