"""
ETAP Lens - a local web app for exploring ETAP project databases
(.OTI / .MDF / .BAK) and study results without needing ETAP itself installed.

Run:
    python app.py
Then open http://127.0.0.1:5151 in a browser.
"""
import os
import sqlite3
import threading
import uuid

from flask import Flask, jsonify, request, send_file, send_from_directory
from werkzeug.utils import secure_filename

from etap_reader import categories as cat_defs
from etap_reader import browse_fs, folder_scan, locate, project_cache, sld_graph, xlsx_export

# The frontend is a plain static app (no templating, no build step) so it can
# be served either from here - the local desktop case, same origin as the API -
# or uploaded to a static host with the API running elsewhere. In the split
# case web/config.js is overridden to point at the backend's absolute URL.
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

app = Flask(__name__, static_folder=WEB_DIR, static_url_path="")

# in-memory progress tracker for the (slow, one-time) load/dump step
_load_jobs = {}
_load_jobs_lock = threading.Lock()

# Table payloads are sent to the browser whole and paged client-side, which is
# fine for model tables (the biggest are a few thousand rows) but not for
# time-domain results - a year of hourly per-branch data is ~500k rows. Cap the
# response and tell the client it was capped rather than hanging the tab.
ROW_LIMIT = 25000


def _clean(v):
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, (bytes, bytearray, memoryview)):
        return f"<binary {len(v)} bytes>"
    return v


def _rows_as_dicts(cur):
    cols = [d[0] for d in cur.description]
    return [{c: _clean(v) for c, v in zip(cols, row)} for row in cur.fetchall()]


def _db(project_id):
    manifest = project_cache.get_manifest(project_id)
    if not manifest:
        return None, None
    conn = sqlite3.connect(manifest["sqlite_path"])
    return conn, manifest


def _table_exists(conn, table):
    cur = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None


def _table_row_count(conn, table):
    cur = conn.execute("SELECT row_count FROM _table_index WHERE table_name=?", (table,))
    row = cur.fetchone()
    return row[0] if row else 0


@app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.route("/api/projects")
def api_projects():
    return jsonify(project_cache.list_projects())


@app.route("/api/browse")
def api_browse():
    path = request.args.get("path", "")
    try:
        return jsonify(browse_fs.list_directory(path))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/browse/quick")
def api_browse_quick():
    return jsonify(browse_fs.quick_locations())


@app.route("/api/export/xlsx", methods=["POST"])
def api_export_xlsx():
    """Generic table -> .xlsx export. The client sends whatever columns/rows
    it's currently showing (already filtered/sorted/column-scoped by the
    user), so this is a pure formatting step - no DB access needed here."""
    data = request.get_json(force=True)
    columns = data.get("columns") or []
    rows = data.get("rows") or []
    if not columns:
        return jsonify({"error": "no columns to export"}), 400

    sheet_name = str(data.get("sheet_name") or "Data")
    filename = secure_filename(str(data.get("filename") or "export.xlsx"))
    if not filename.lower().endswith(".xlsx"):
        filename += ".xlsx"

    buf = xlsx_export.table_to_xlsx_bytes(columns, rows, sheet_name)
    return send_file(buf, as_attachment=True, download_name=filename,
                      mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _start_job(loader_fn):
    """Run loader_fn(progress_cb) -> manifest on a background thread, tracked
    under a job_id that the frontend polls via /api/load/status/<job_id>."""
    job_id = uuid.uuid4().hex
    with _load_jobs_lock:
        _load_jobs[job_id] = {"stage": "starting", "current": 0, "total": 0, "done": False, "error": None}

    def progress_cb(stage, current, total):
        with _load_jobs_lock:
            _load_jobs[job_id].update(stage=stage, current=current, total=total)

    def run():
        try:
            manifest = loader_fn(progress_cb)
            with _load_jobs_lock:
                _load_jobs[job_id].update(done=True, project_id=manifest["project_id"], manifest=manifest)
        except Exception as e:
            with _load_jobs_lock:
                _load_jobs[job_id].update(done=True, error=str(e))

    threading.Thread(target=run, daemon=True).start()
    return job_id


@app.route("/api/load", methods=["POST"])
def api_load_start():
    data = request.get_json(force=True)
    input_path = data.get("path", "").strip().strip('"')
    force = bool(data.get("force", False))
    if not input_path:
        return jsonify({"error": "path is required"}), 400
    if not os.path.exists(input_path):
        return jsonify({"error": f"Path not found: {input_path}"}), 400

    if os.path.isdir(input_path):
        try:
            candidates = folder_scan.scan_folder(input_path)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"is_folder": True, "folder": input_path, "candidates": candidates})

    job_id = _start_job(lambda progress_cb: project_cache.load_project(input_path, force=force, progress_cb=progress_cb))
    return jsonify({"job_id": job_id})


@app.route("/api/project/<project_id>/unload", methods=["DELETE"])
def api_unload(project_id):
    removed = project_cache.unload_project(project_id)
    return jsonify({"removed": removed})


@app.route("/api/projects/clear", methods=["POST"])
def api_clear_all():
    count = project_cache.clear_all()
    return jsonify({"cleared": count})


@app.route("/api/upload", methods=["POST"])
def api_upload_start():
    """Accept a database file uploaded from the browser's native folder
    picker (browsers don't expose real filesystem paths to JS, so this is
    the only way to let the user 'browse to a folder' and have us read the
    file it contains)."""
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "no file uploaded"}), 400

    filename = secure_filename(f.filename) or "upload"
    ext = os.path.splitext(filename)[1].lower()
    upload_dir = os.path.join(project_cache.CACHE_DIR, "uploads", os.path.splitext(filename)[0].lower())
    os.makedirs(upload_dir, exist_ok=True)
    dest_path = os.path.join(upload_dir, filename)
    f.save(dest_path)

    try:
        located = locate.locate(dest_path)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    located.note = f"Uploaded via browser file picker ({filename})"

    job_id = _start_job(lambda progress_cb: project_cache.load_located(located, input_path=dest_path, progress_cb=progress_cb))
    return jsonify({"job_id": job_id})


@app.route("/api/load/status/<job_id>")
def api_load_status(job_id):
    with _load_jobs_lock:
        job = _load_jobs.get(job_id)
        if not job:
            return jsonify({"error": "unknown job"}), 404
        return jsonify(job)


@app.route("/api/project/<project_id>/categories")
def api_categories(project_id):
    conn, manifest = _db(project_id)
    if not conn:
        return jsonify({"error": "unknown project"}), 404
    category_set = manifest.get("category_set", "model")
    out = []
    for key, cat in cat_defs.categories_for_set(category_set).items():
        total = 0
        for t in cat["tables"]:
            if _table_exists(conn, t):
                total += _table_row_count(conn, t)
        out.append({"key": key, "label": cat["label"], "description": cat["description"], "row_count": total})
    conn.close()
    return jsonify({"category_set": category_set, "categories": out})


@app.route("/api/project/<project_id>/category/<key>")
def api_category(project_id, key):
    conn, manifest = _db(project_id)
    if not conn:
        return jsonify({"error": "unknown project"}), 404
    category_set = manifest.get("category_set", "model")
    cat = cat_defs.categories_for_set(category_set).get(key)
    if not cat:
        return jsonify({"error": "unknown category"}), 404

    subtables = []
    for t in cat["tables"]:
        if not _table_exists(conn, t):
            continue
        row_count = _table_row_count(conn, t)
        cur = conn.execute(f'SELECT * FROM "{t}" LIMIT {ROW_LIMIT}')
        cols = [d[0] for d in cur.description]
        ordered = cat_defs.order_columns(cols)
        rows = _rows_as_dicts(cur)
        subtables.append({"table": t, "columns": ordered, "rows": rows,
                          "row_count": row_count, "truncated": len(rows) < row_count})
    conn.close()
    return jsonify({"key": key, "label": cat["label"], "description": cat["description"], "subtables": subtables})


@app.route("/api/project/<project_id>/tables")
def api_tables(project_id):
    conn, manifest = _db(project_id)
    if not conn:
        return jsonify({"error": "unknown project"}), 404
    category_set = manifest.get("category_set", "model")
    cur = conn.execute("SELECT table_name, row_count FROM _table_index ORDER BY table_name")
    out = [{"table": r[0], "rows": r[1], "category": cat_defs.category_for_table(r[0], category_set)}
           for r in cur.fetchall()]
    conn.close()
    return jsonify(out)


@app.route("/api/project/<project_id>/violations_report")
def api_violations_report(project_id):
    """Bundle every alert/violation table this project has (across whichever
    categories are tagged 'alerts' or 'violations' for its category_set)
    into one formatted multi-sheet workbook."""
    conn, manifest = _db(project_id)
    if not conn:
        return jsonify({"error": "unknown project"}), 404
    category_set = manifest.get("category_set", "model")
    categories = cat_defs.categories_for_set(category_set)

    relevant_keys = [k for k in categories if "alert" in k.lower() or "violation" in k.lower()]
    seen_tables = set()
    sheets = []
    for key in relevant_keys:
        for table in categories[key]["tables"]:
            if table in seen_tables or not _table_exists(conn, table):
                continue
            seen_tables.add(table)
            cur = conn.execute(f'SELECT * FROM "{table}"')
            ordered = cat_defs.order_columns([d[0] for d in cur.description])
            rows = _rows_as_dicts(cur)
            if rows:
                sheets.append((table, ordered, rows))
    conn.close()

    if not sheets:
        return jsonify({"error": "No violations or alerts found in this project."}), 404

    buf = xlsx_export.multi_sheet_xlsx_bytes(sheets)
    filename = secure_filename(f"{manifest['db_name']}_violations_report.xlsx")
    return send_file(buf, as_attachment=True, download_name=filename,
                      mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/api/project/<project_id>/table/<table_name>")
def api_table(project_id, table_name):
    conn, manifest = _db(project_id)
    if not conn:
        return jsonify({"error": "unknown project"}), 404
    if not _table_exists(conn, table_name):
        conn.close()
        return jsonify({"error": "unknown table"}), 404
    row_count = _table_row_count(conn, table_name)
    cur = conn.execute(f'SELECT * FROM "{table_name}" LIMIT {ROW_LIMIT}')
    cols = [d[0] for d in cur.description]
    ordered = cat_defs.order_columns(cols)
    rows = _rows_as_dicts(cur)
    conn.close()
    return jsonify({"table": table_name, "columns": ordered, "rows": rows,
                    "row_count": max(row_count, len(rows)),
                    "truncated": len(rows) < row_count})


@app.route("/api/project/<project_id>/overview")
def api_overview(project_id):
    conn, manifest = _db(project_id)
    if not conn:
        return jsonify({"error": "unknown project"}), 404
    category_set = manifest.get("category_set", "model")

    def dump(table):
        if not _table_exists(conn, table):
            return []
        cur = conn.execute(f'SELECT * FROM "{table}"')
        return _rows_as_dicts(cur)

    equipment_counts = []
    for key, cat in cat_defs.categories_for_set(category_set).items():
        total = sum(_table_row_count(conn, t) for t in cat["tables"] if _table_exists(conn, t))
        equipment_counts.append({"key": key, "label": cat["label"], "row_count": total})

    info_tables = [
        {"table": table, "title": title, "rows": dump(table)}
        for table, title in cat_defs.OVERVIEW_INFO_TABLES.items()
        if _table_exists(conn, table)
    ]

    result = {
        "manifest": manifest,
        "info_tables": info_tables,
        "equipment_counts": equipment_counts,
    }
    conn.close()
    return jsonify(result)


@app.route("/api/project/<project_id>/buses")
def api_buses(project_id):
    conn, manifest = _db(project_id)
    if not conn:
        return jsonify({"error": "unknown project"}), 404
    if not _table_exists(conn, "Bus"):
        conn.close()
        return jsonify([])
    cur = conn.execute('SELECT ID, NominalkV, InService FROM "Bus" ORDER BY ID')
    out = [{"id": _clean(r[0]), "kv": r[1], "in_service": r[2]} for r in cur.fetchall()]
    conn.close()
    return jsonify(out)


@app.route("/api/project/<project_id>/bus/<path:bus_id>/connectivity")
def api_bus_connectivity(project_id, bus_id):
    conn, manifest = _db(project_id)
    if not conn:
        return jsonify({"error": "unknown project"}), 404
    bus_id = bus_id.strip()

    bus_row = None
    if _table_exists(conn, "Bus"):
        cur = conn.execute('SELECT * FROM "Bus" WHERE TRIM(ID) = ?', (bus_id,))
        rows = _rows_as_dicts(cur)
        bus_row = rows[0] if rows else None

    connections = []
    for table, cols in cat_defs.CONNECTIVITY_TABLES.items():
        if not _table_exists(conn, table):
            continue
        cur = conn.execute(f'SELECT * FROM "{table}"')
        all_cols = [d[0] for d in cur.description]
        for row in cur.fetchall():
            row_dict = {c: _clean(v) for c, v in zip(all_cols, row)}
            matched_cols = [c for c in cols if c in row_dict and row_dict[c] == bus_id]
            if matched_cols:
                other_ends = [f"{c}={row_dict[c2]}" for c in matched_cols
                              for c2 in cols if c2 != c and row_dict.get(c2)]
                connections.append({
                    "table": table,
                    "id": row_dict.get("ID"),
                    "matched_via": matched_cols,
                    "other_end": [row_dict.get(c) for c in cols if c not in matched_cols and row_dict.get(c)],
                    "in_service": row_dict.get("InService"),
                    "row": row_dict,
                })
    conn.close()
    return jsonify({"bus": bus_row, "connections": connections})


@app.route("/api/project/<project_id>/sld_graph")
def api_sld_graph(project_id):
    """Connectivity graph for the auto-layout Single Line Diagram view -
    not ETAP's saved canvas (that data isn't accessible), just the same
    Bus/FromBus/ToBus connectivity as the tabular Single Line Explorer,
    shaped for the frontend to lay out as a tree."""
    conn, manifest = _db(project_id)
    if not conn:
        return jsonify({"error": "unknown project"}), 404
    if not _table_exists(conn, "Bus"):
        conn.close()
        return jsonify({"error": "This project has no Bus table (only project models have a single line diagram)"}), 400
    graph = sld_graph.build_graph(conn, _table_exists)
    conn.close()
    return jsonify(graph)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5151, debug=True, use_reloader=False)
