"""
Parse an ETAP .OTI file (an OLE2/Compound File Binary container holding
connection info, users, and permissions - NOT the engineering model itself).
"""
import os
import re

import olefile


def _try_decode_text(data: bytes):
    for enc in ("utf-16-le", "utf-8"):
        try:
            text = data.decode(enc, errors="strict")
        except UnicodeDecodeError:
            continue
        printable = sum(1 for c in text if c.isprintable() or c in "\r\n\t")
        if len(text) > 0 and printable / len(text) > 0.85:
            cleaned = text.replace("\x00", "").strip()
            if cleaned:
                return cleaned
    return None


def _find_strings(data: bytes, min_len: int = 4):
    found = set()
    for m in re.finditer(rb"[\x20-\x7e]{%d,}" % min_len, data):
        found.add(m.group().decode("ascii"))
    for m in re.finditer(rb"(?:[\x20-\x7e]\x00){%d,}" % min_len, data):
        try:
            found.add(m.group().decode("utf-16-le"))
        except UnicodeDecodeError:
            pass
    return sorted(found, key=len, reverse=True)


def parse_oti(path: str) -> dict:
    """Return a structured dump of every storage/stream in the .oti file."""
    if not olefile.isOleFile(path):
        raise ValueError(f"{path} is not a recognized OLE/Compound File (.oti) file")

    ole = olefile.OleFileIO(path)
    try:
        streams = []
        for entry in ole.listdir(streams=True, storages=False):
            path_str = "/" + "/".join(entry)
            data = ole.openstream(entry).read()
            text = _try_decode_text(data)
            strings = [] if text else _find_strings(data)
            streams.append({
                "path": path_str,
                "size": len(data),
                "text": text,
                "strings": strings,
            })
        return {
            "file": path,
            "size": os.path.getsize(path),
            "streams": streams,
        }
    finally:
        ole.close()


def get_connection_info(path: str) -> dict:
    """Extract DSN/DBQ from the ODBCInfo/ConnectionString stream, e.g.
    'ODBC;DBQ=etapmodel;DSN=otilocaldb19;FIL=Local SQL DB;...;UID=WS1;PWD=;'
    """
    parsed = parse_oti(path)
    for s in parsed["streams"]:
        if s["path"] == "/ODBCInfo/ConnectionString":
            candidates = [s["text"]] if s["text"] else s["strings"]
            for candidate in candidates:
                if candidate and "DSN=" in candidate:
                    info = {}
                    for part in candidate.split(";"):
                        if "=" in part:
                            k, v = part.split("=", 1)
                            info[k.strip().upper()] = v.strip()
                    return info
    return {}
