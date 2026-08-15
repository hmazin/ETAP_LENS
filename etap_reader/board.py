"""
Builds the module board: what a project folder contains, grouped the way an
engineer thinks about it.

The board is deliberately cheap. It reads a directory listing and, for a local
folder, the .OTI pointers needed to resolve which database a project actually
uses. It never opens a study file and never triggers an import - the whole
point is that pointing at a folder costs nothing and you only pay for the
module you open.

Two entry points, one shape out:

- from_folder()  - local. The server can see the folder, so it reads it.
- from_listing() - hosted. The server can see nothing; the browser enumerates
                   the folder locally and posts names, sizes and dates. No file
                   content crosses the network until a tile is opened.
"""
import os
import time

from . import appconfig, folder_scan, modules, project_cache


def _iso(ts):
    """Normalize a modification time to ISO, whoever supplied it.

    from_folder() has a POSIX timestamp in seconds; the browser's File API
    gives milliseconds. Left unnormalized these reach the client in two
    different shapes, and a tile cannot show one date format for a study
    someone opened locally and another for the same study uploaded.
    """
    if not ts:
        return None
    if isinstance(ts, str):
        return ts
    ts = float(ts)
    if ts > 1e11:  # milliseconds since epoch, not seconds
        ts /= 1000.0
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))
    except (ValueError, OSError):
        return None


def _subfolder(rel):
    """Where inside the picked folder a file sits, "" for the top level.

    webkitRelativePath is "<picked folder>/<...>/<name>"; the first segment is
    the folder the user chose and the last is the file, so what is left is the
    part worth showing on a tile."""
    if not rel:
        return ""
    parts = [p for p in rel.replace("\\", "/").split("/") if p]
    return "/".join(parts[1:-1])


def _too_large(size) -> bool:
    """Hosted instances cap upload size. Knowing this at scan time means the
    board can say so before the engineer waits through a doomed transfer."""
    return bool(appconfig.IS_HOSTED and size and size > appconfig.MAX_UPLOAD_BYTES)


def _file(filename, size, mtime, path=None, project_id=None, error=None, rel=None):
    return {
        "filename": filename,
        # The browser's directory picker is recursive, so two subfolders can
        # each hold a "SC_Max.SA2S". This is what tells them apart, and what
        # the client keys its File handles by - matching on the bare filename
        # would open whichever one it happened to store last.
        "rel": rel,
        "subfolder": _subfolder(rel),
        # ETAP names a result file after the study case that produced it, so
        # the stem is the only thing telling fourteen unbalanced load flows
        # apart - the extension is identical across all of them.
        "name": os.path.splitext(filename)[0],
        "path": path,
        "module": modules.module_for(filename),
        "variant": modules.variant_label(filename),
        "size": size,
        # Normalized here rather than at each call site, so every path out of
        # this module emits one shape whatever it was handed.
        "mtime": _iso(mtime),
        "analyzed": bool(project_id),
        "project_id": project_id,
        "too_large": _too_large(size),
        "error": error,
    }


def from_folder(folder_path: str, session_id: str = None) -> dict:
    """Board for a folder on this machine. Raises ValueError if unreadable."""
    folder_path = os.path.abspath(folder_path)
    candidates = folder_scan.scan_folder(folder_path)
    analyzed = project_cache.db_path_index(session_id)

    files = []
    for c in candidates:
        db_path = c.get("db_path")
        # A candidate carrying an error is an .OTI whose database is missing -
        # it is still worth showing, so the engineer learns the pointer is
        # dangling rather than wondering why the model never appears.
        project_id = (analyzed.get(os.path.abspath(db_path).lower())
                      if db_path and not c.get("error") else None)
        try:
            mtime = _iso(os.path.getmtime(c["path"]))
        except OSError:
            mtime = None
        files.append(_file(c["filename"], c.get("size"), mtime,
                           path=c["path"], project_id=project_id,
                           error=c.get("error")))

    return _assemble(folder_path, os.path.basename(folder_path) or folder_path,
                     files, mode=appconfig.DEPLOY_MODE)


def from_listing(entries, session_id: str = None, folder_name: str = "") -> dict:
    """Board from a listing the browser produced. `entries` is whatever the
    client enumerated: dicts with a name, and optionally size and mtime.

    Nothing here trusts the client for anything but names and sizes - those
    only decide what the board *offers*. Opening a tile still goes through the
    ordinary upload path, which validates the file itself.
    """
    # An uploaded file has no stable path, so a cache hit can only be matched
    # by name - and it has to be the *full* filename. Matching the stem would
    # make one analyzed GM.TU1S mark GM.SA1S and GM.LF1S as analyzed too, and a
    # tile that offers to open something that was never analyzed is worse than
    # one that offers to analyze something that already was: the second is
    # merely redundant, the first is a dead end.
    known = {}
    for p in project_cache.list_projects(session_id):
        pid = p.get("project_id")
        if not pid:
            continue
        for field in ("display_name", "input_path"):
            v = (p.get(field) or "").strip()
            if v:
                known.setdefault(os.path.basename(v).lower(), pid)

    files = []
    for e in entries or []:
        name = (e.get("name") or "").strip()
        if not name or not modules.module_for(name):
            continue
        files.append(_file(name, e.get("size"), e.get("mtime"),
                           project_id=known.get(name.lower()),
                           rel=(e.get("rel") or "").strip() or None))

    # The client knows which File object each name refers to; the server never
    # sees one, so `path` stays null and the tile uploads on click.
    return _assemble(None, folder_name, files, mode=appconfig.DEPLOY_MODE)


def _assemble(folder, name, files, mode) -> dict:
    by_module = {}
    for f in files:
        by_module.setdefault(f["module"], []).append(f)

    out = []
    for m in modules.MODULES:
        mine = by_module.get(m["key"], [])
        # Group by variant, then newest first. A folder can hold a dozen runs
        # of the same study type, and the one you want is usually the one you
        # ran last. Two passes rather than one composite key: sort is stable,
        # so the second ordering survives inside each group of the first.
        mine.sort(key=lambda f: (f["mtime"] or "", f["name"].lower()), reverse=True)
        mine.sort(key=lambda f: f["variant"])
        out.append({
            "key": m["key"],
            "label": m["label"],
            "etap_tag": m.get("etap_tag"),
            "state": _state(m, mine),
            "files": mine,
        })

    return {
        "folder": folder,
        "name": name or "Project",
        "mode": mode,
        "modules": out,
        "total_files": len(files),
    }


def _state(module, files) -> str:
    """One of: unsupported, unavailable, no_results, analyzed, too_large, ready.

    Order matters. A module we cannot read at all outranks whether files happen
    to be present, because offering to open something that will fail three
    steps later is worse than saying so up front.
    """
    if not module["extensions"]:
        return "unsupported"
    if module.get("needs_sql_server") and not appconfig.CAN_READ_MODELS:
        return "unavailable"
    if not files:
        return "no_results"
    if any(f["analyzed"] for f in files):
        return "analyzed"
    if all(f["too_large"] for f in files):
        return "too_large"
    return "ready"
