"""
Lightweight server-side filesystem browser backing the in-app folder-browse
modal. This exists because browsers never expose real filesystem paths from
native file/folder pickers (security), and a native "select folder" dialog
on Windows doesn't show files while browsing (OS behavior). Since this app
already runs locally with full filesystem access, we can just browse it
directly and skip the native dialogs entirely.
"""
import os
import string

try:
    import winreg
except ImportError:
    winreg = None

from . import study_result

_EXT_KIND_LABEL = {
    ".mdf": ("mdf", "Project Database (.MDF)"),
    ".bak": ("bak", "Project Database Backup (.BAK)"),
    ".oti": ("oti", "Project Pointer (.OTI)"),
}
for _ext, (_label, _cat) in study_result.STUDY_EXTENSIONS.items():
    _EXT_KIND_LABEL[_ext] = ("study", _label)


def list_drives():
    return [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]


def _shell_folder(value_name):
    """Read a "known folder" path from the registry - handles OneDrive
    Known Folder Move redirection, where e.g. Desktop isn't at the naive
    <home>\\Desktop but somewhere like <home>\\OneDrive\\Desktop."""
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders") as key:
            value, _ = winreg.QueryValueEx(key, value_name)
            return os.path.expandvars(value)
    except OSError:
        return None


def quick_locations():
    """Common jump-to locations for the folder browser, matching what a
    normal Windows file dialog offers (Desktop, Documents, Downloads, ...)."""
    home = os.path.expanduser("~")
    candidates = [
        ("Desktop", _shell_folder("Desktop") or os.path.join(home, "Desktop")),
        ("Documents", _shell_folder("Personal") or os.path.join(home, "Documents")),
        ("Downloads", _shell_folder("{374DE290-123F-4565-9164-39C4925E467B}") or os.path.join(home, "Downloads")),
        ("Home", home),
    ]
    return [{"name": name, "path": path} for name, path in candidates if path and os.path.isdir(path)]


def _parent_of(path: str):
    normalized = os.path.normpath(path)
    if len(normalized) <= 3 and normalized[1:3] == ":\\":
        return ""  # a drive root's "parent" is the drive list
    return os.path.dirname(normalized)


def list_directory(path: str):
    """List a folder's loadable files and subfolders, one level deep. An
    empty path means "show available drives"."""
    if not path:
        return {
            "path": "", "parent": None,
            "folders": [{"name": d, "path": d} for d in list_drives()],
            "files": [],
        }

    path = os.path.normpath(path)
    if not os.path.isdir(path):
        raise ValueError(f"Not a folder: {path}")

    try:
        entries = sorted(os.listdir(path), key=str.lower)
    except OSError as e:
        raise ValueError(f"Could not read folder: {e}")

    folders, files = [], []
    for fn in entries:
        full = os.path.join(path, fn)
        try:
            is_dir = os.path.isdir(full)
        except OSError:
            continue
        if is_dir:
            folders.append({"name": fn, "path": full})
        else:
            ext = os.path.splitext(fn)[1].lower()
            if ext in _EXT_KIND_LABEL:
                kind, label = _EXT_KIND_LABEL[ext]
                try:
                    size = os.path.getsize(full)
                except OSError:
                    size = 0
                files.append({"name": fn, "path": full, "kind": kind, "label": label, "size": size})

    return {"path": path, "parent": _parent_of(path), "folders": folders, "files": files}
