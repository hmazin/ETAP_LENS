"""
Given a path the user hands us, figure out which physical database file
actually holds the data, and how to read it.

- .OTI only stores a pointer (DBQ=<db name>) - the real data sits in
  "<DBQ>.MDF" (live SQL Server file) or "<DBQ>.BAK" (backup) in the same
  folder. We prefer the .MDF (current data) over .BAK (a snapshot) unless
  the .MDF is missing.
- .MDF / .BAK are read via SQL Server LocalDB (see mdf_dump.py).
- .SA1S / .SA2S (and other analysis result extensions in study_result.py)
  are already plain SQLite - read directly, no LocalDB involved.
"""
import os

from . import oti_parser, study_result


class LocatedDatabase:
    def __init__(self, source_path, kind, db_path, db_name, note="", category_set="model"):
        self.source_path = source_path   # what the user actually gave us
        self.kind = kind                 # "mdf", "bak", or "study"
        self.db_path = db_path           # physical file to attach/restore/copy
        self.db_name = db_name           # logical database name (from OTI, or filename)
        self.note = note
        self.category_set = category_set  # "model", "sc_duty", "sc_fault", ...

    def to_dict(self):
        return {
            "source_path": self.source_path,
            "kind": self.kind,
            "db_path": self.db_path,
            "db_name": self.db_name,
            "note": self.note,
            "category_set": self.category_set,
        }


def locate(path: str) -> LocatedDatabase:
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)

    ext = os.path.splitext(path)[1].lower()
    folder = os.path.dirname(path)

    if ext == ".mdf":
        return LocatedDatabase(path, "mdf", path, os.path.splitext(os.path.basename(path))[0])

    if ext == ".bak":
        return LocatedDatabase(path, "bak", path, os.path.splitext(os.path.basename(path))[0])

    if ext in study_result.STUDY_EXTENSIONS:
        info = study_result.study_info(path)
        return LocatedDatabase(
            path, "study", path, os.path.splitext(os.path.basename(path))[0],
            note=f"{info['label']} study result (read directly - already SQLite)",
            category_set=info["category_set"],
        )

    if ext == ".oti":
        info = oti_parser.get_connection_info(path)
        db_name = info.get("DBQ", os.path.splitext(os.path.basename(path))[0])

        mdf_candidate = os.path.join(folder, db_name + ".MDF")
        if not os.path.isfile(mdf_candidate):
            # case-insensitive fallback
            for f in os.listdir(folder):
                if f.lower() == (db_name + ".mdf").lower():
                    mdf_candidate = os.path.join(folder, f)
                    break
        if os.path.isfile(mdf_candidate):
            return LocatedDatabase(
                path, "mdf", mdf_candidate, db_name,
                note=f"Resolved from .OTI connection string (DSN={info.get('DSN', '?')}) to {os.path.basename(mdf_candidate)}",
            )

        bak_candidate = os.path.join(folder, db_name + ".BAK")
        if not os.path.isfile(bak_candidate):
            for f in os.listdir(folder):
                if f.lower() == (db_name + ".bak").lower():
                    bak_candidate = os.path.join(folder, f)
                    break
        if os.path.isfile(bak_candidate):
            return LocatedDatabase(
                path, "bak", bak_candidate, db_name,
                note=f"No .MDF found next to the .OTI - falling back to backup {os.path.basename(bak_candidate)}",
            )

        raise FileNotFoundError(
            f"Could not find '{db_name}.MDF' or '{db_name}.BAK' next to {path}. "
            f"The .OTI file only stores a pointer (DSN={info.get('DSN', '?')}); "
            f"point this tool directly at the .MDF/.BAK if it lives elsewhere."
        )

    supported = ".oti, .mdf, .bak, " + ", ".join(study_result.STUDY_EXTENSIONS)
    raise ValueError(f"Unsupported file type '{ext}'. Expected one of: {supported}")
