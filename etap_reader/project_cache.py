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

from . import locate, mdf_dump, study_result

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache")


def _project_id_for(db_path: str) -> str:
    return hashlib.sha1(os.path.abspath(db_path).lower().encode("utf-8")).hexdigest()[:16]


def _manifest_path(project_id: str) -> str:
    return os.path.join(CACHE_DIR, project_id + ".json")


def _sqlite_path(project_id: str) -> str:
    return os.path.join(CACHE_DIR, project_id + ".sqlite")


def list_projects():
    os.makedirs(CACHE_DIR, exist_ok=True)
    out = []
    for fn in os.listdir(CACHE_DIR):
        if fn.endswith(".json"):
            with open(os.path.join(CACHE_DIR, fn), "r", encoding="utf-8") as f:
                out.append(json.load(f))
    out.sort(key=lambda m: m.get("loaded_at", 0), reverse=True)
    return out


def get_manifest(project_id: str):
    p = _manifest_path(project_id)
    if not os.path.isfile(p):
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def load_project(input_path: str, force: bool = False, progress_cb=None) -> dict:
    """Locate the real database for input_path, dump it to sqlite if the
    cache is missing/stale, and return the manifest dict."""
    located = locate.locate(input_path)
    return load_located(located, input_path=input_path, force=force, progress_cb=progress_cb)


def load_located(located, input_path: str = None, force: bool = False, progress_cb=None) -> dict:
    """Dump an already-resolved LocatedDatabase to sqlite if the cache is
    missing/stale, and return the manifest dict."""
    os.makedirs(CACHE_DIR, exist_ok=True)

    project_id = _project_id_for(located.db_path)
    stat = os.stat(located.db_path)
    source_fingerprint = {"mtime": stat.st_mtime, "size": stat.st_size}

    existing = get_manifest(project_id)
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
        "input_path": os.path.abspath(input_path) if input_path else located.source_path,
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


def unload_project(project_id: str) -> bool:
    """Remove a loaded project's cached .sqlite/.json (and, if it was loaded
    via browser upload, the uploaded copy too). Frees disk space and drops
    it from the recent-projects list."""
    manifest = get_manifest(project_id)
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


def clear_all() -> int:
    count = 0
    for m in list_projects():
        if unload_project(m["project_id"]):
            count += 1
    return count
