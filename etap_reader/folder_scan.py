"""
Scan a folder for ETAP files this app can load (project model or study
results), so you can point at a whole project folder and then pick exactly
one thing to open - nothing gets loaded until you click a specific file.
"""
import os

from . import locate, study_result

_DIRECT_KIND_LABELS = {
    "mdf": "Project Database (.MDF)",
    "bak": "Project Database Backup (.BAK)",
}

_KNOWN_EXTS = {".oti", ".mdf", ".bak"} | set(study_result.STUDY_EXTENSIONS.keys())


def _label_for(located):
    if located.kind == "study":
        ext = os.path.splitext(located.source_path)[1].lower()
        label, _ = study_result.STUDY_EXTENSIONS.get(ext, ("Study Result", None))
        return label
    return _DIRECT_KIND_LABELS.get(located.kind, located.kind.upper())


def _rank(kind):
    return {"mdf": 0, "bak": 1}.get(kind, 2)


def scan_folder(folder_path: str):
    """Non-recursive scan for loadable ETAP files. De-duplicated: an .oti
    and the .MDF/.BAK it resolves to only appear once (the direct database
    file wins)."""
    try:
        entries = sorted(os.listdir(folder_path))
    except OSError as e:
        raise ValueError(f"Could not read folder: {e}")

    files = [fn for fn in entries
             if os.path.splitext(fn)[1].lower() in _KNOWN_EXTS
             and os.path.isfile(os.path.join(folder_path, fn))]

    seen_db_paths = set()
    candidates = []

    # Pass 1: direct database files (.mdf/.bak/study results) win over an
    # .oti that happens to point at the same underlying file.
    for fn in files:
        if os.path.splitext(fn)[1].lower() == ".oti":
            continue
        full = os.path.join(folder_path, fn)
        located = locate.locate(full)
        seen_db_paths.add(located.db_path.lower())
        candidates.append({
            "path": full, "filename": fn, "kind": located.kind,
            "label": _label_for(located), "size": os.path.getsize(full),
        })

    # Pass 2: .oti pointers - only add if they resolve to something not
    # already listed, or surface the error if they don't resolve at all.
    for fn in files:
        if os.path.splitext(fn)[1].lower() != ".oti":
            continue
        full = os.path.join(folder_path, fn)
        try:
            located = locate.locate(full)
        except Exception as e:
            candidates.append({
                "path": full, "filename": fn, "kind": "oti", "label": "Project Pointer (.OTI)",
                "size": os.path.getsize(full), "error": str(e),
            })
            continue
        if located.db_path.lower() in seen_db_paths:
            continue
        seen_db_paths.add(located.db_path.lower())
        candidates.append({
            "path": full, "filename": fn, "kind": located.kind,
            "label": _label_for(located), "size": os.path.getsize(located.db_path),
        })

    candidates.sort(key=lambda c: (_rank(c["kind"]), c["filename"].lower()))
    return candidates
