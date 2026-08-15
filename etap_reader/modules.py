"""
ETAP study modules - the engineer-facing grouping the board is built on.

The board lists every module ETAP itself has, not only the ones this tool can
read, because "this project has no harmonics study" and "this tool doesn't read
harmonics" are different answers and an engineer has to be able to tell them
apart. A module with no extensions is one we don't read yet; it still gets a
tile, greyed.

The unsupported entries are ETAP's own study tags, taken from the abbreviations
in OLV.Stream's header (LF, SC, MS, TS, OPF, ULF, HA - see
docs/FILE_FORMAT_NOTES.md). Using ETAP's list rather than inventing one means
the board is complete by construction: any study an engineer can run has a
place to appear.

Order is the order studies tend to get run, not alphabetical.
"""
import os

from . import study_result

# Which module each project-database extension belongs to, and how to name it
# in a tile. Study result extensions get their labels from
# study_result.STUDY_EXTENSIONS so that map stays the single source of truth.
_MODEL_VARIANTS = {
    ".mdf": "Project Database",
    ".bak": "Project Database (backup)",
    ".oti": "Project Pointer",
}

MODULES = [
    {
        "key": "model",
        "label": "Model",
        "extensions": (".oti", ".mdf", ".bak"),
        # The project database is SQL Server; a hosted instance has none, so
        # this module can never be opened there however many files are found.
        "needs_sql_server": True,
    },
    {
        "key": "short_circuit",
        "label": "Short Circuit",
        "extensions": (".sa1s", ".sa2s"),
        "etap_tag": "SC",
    },
    {
        "key": "load_flow",
        "label": "Load Flow",
        "extensions": (".lf1s", ".ul1s"),
        "etap_tag": "LF",
    },
    {
        "key": "time_domain",
        "label": "Time Domain",
        "extensions": (".tu1s",),
        "etap_tag": "TDLF",
    },
    {"key": "motor_starting", "label": "Motor Starting", "extensions": (), "etap_tag": "MS"},
    {"key": "harmonics", "label": "Harmonics", "extensions": (), "etap_tag": "HA"},
    {"key": "transient_stability", "label": "Transient Stability", "extensions": (), "etap_tag": "TS"},
    {"key": "opf", "label": "Optimal Power Flow", "extensions": (), "etap_tag": "OPF"},
]

# ext -> module key, built from the table above so the two cannot drift.
_EXT_TO_MODULE = {
    ext: m["key"] for m in MODULES for ext in m["extensions"]
}

KNOWN_EXTENSIONS = frozenset(_EXT_TO_MODULE)


def module_for(filename: str):
    """Module key for a filename, or None if we don't recognize the extension."""
    return _EXT_TO_MODULE.get(os.path.splitext(filename)[1].lower())


def variant_label(filename: str) -> str:
    """How this specific file is named inside its module's tile - the thing that
    distinguishes an ANSI duty run from a fault-current one."""
    ext = os.path.splitext(filename)[1].lower()
    if ext in study_result.STUDY_EXTENSIONS:
        label = study_result.STUDY_EXTENSIONS[ext][0]
        # "Short Circuit - ANSI Duty" -> "ANSI Duty": the module name is
        # already the tile's heading, so repeating it in every row is noise.
        return label.split(" - ", 1)[-1]
    return _MODEL_VARIANTS.get(ext, ext.lstrip(".").upper())
