"""
ETAP Lens - a local web app for exploring ETAP project databases
(.OTI / .MDF / .BAK) and study results without needing ETAP itself installed.

Run:
    python app.py
Then open http://127.0.0.1:5151 in a browser.
"""
import functools
import os
import re
import shutil
import sqlite3
import threading
import uuid

from flask import Flask, jsonify, request, send_file, send_from_directory
from werkzeug.utils import secure_filename

from etap_reader import appconfig
from etap_reader import categories as cat_defs
from etap_reader import (board, browse_fs, folder_scan, locate, project_cache,
                         sessions, sld_graph, storage, turnstile, upload_guard,
                         xlsx_export)

# The frontend is a plain static app (no templating, no build step) so it can
# be served either from here - the local desktop case, same origin as the API -
# or uploaded to a static host with the API running elsewhere. In the split
# case web/config.js is overridden to point at the backend's absolute URL.
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

app = Flask(__name__, static_folder=WEB_DIR, static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = appconfig.MAX_UPLOAD_BYTES

# Only needed when the frontend is served from a different origin than the API.
# Imported lazily so a local install without flask-cors still runs.
if appconfig.CORS_ORIGINS or appconfig.CORS_ORIGIN_REGEX:
    from flask_cors import CORS

    _origins = list(appconfig.CORS_ORIGINS)
    if appconfig.CORS_ORIGIN_REGEX:
        _origins.append(re.compile(appconfig.CORS_ORIGIN_REGEX))
    CORS(app, resources={r"/api/*": {"origins": _origins}}, max_age=3600)


def local_only(view):
    """Guard for the filesystem-backed endpoints.

    These read whatever path the caller names. That is exactly what you want
    when the app runs as you on your own machine, and an unauthenticated
    directory lister over the server's disk when it doesn't."""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if appconfig.IS_HOSTED:
            return jsonify({
                "error": "Reading server paths is disabled on the hosted app. "
                         "Upload a file instead."
            }), 403
        return view(*args, **kwargs)
    return wrapped


@app.errorhandler(413)
def _payload_too_large(_e):
    return jsonify({
        "error": f"That file is larger than the {appconfig.MAX_UPLOAD_MB} MB limit."
    }), 413

# in-memory progress tracker for the (slow, one-time) load/dump step
_load_jobs = {}
_load_jobs_lock = threading.Lock()

# Where uploads and derived caches live. GCS when a bucket is configured,
# otherwise the local cache directory.
_storage = storage.build(appconfig.GCS_BUCKET,
                         os.path.join(project_cache.CACHE_DIR, "objects"))

# Derived caches are mirrored to object storage only when hosted. Locally the
# disk outlives the process, so there is nothing to protect against.
if appconfig.IS_HOSTED:
    project_cache.set_remote(_storage)


def current_session():
    """(session_id, error_response_or_None) for this request."""
    sid, err = sessions.from_request(request, appconfig.REQUIRE_SESSION)
    if err:
        return None, (jsonify({"error": err}), 400)
    return sid, None


def scoped_session():
    """The session to filter cache reads by.

    None means "no filtering", which is right for the desktop app - one user,
    one shared cache, and the behaviour it has always had.
    """
    if not appconfig.REQUIRE_SESSION:
        return None
    sid, _ = sessions.from_request(request, True)
    return sid


def with_session(view):
    """Reject requests with a missing or malformed session before they reach
    anything that reads the cache."""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        _, err = current_session()
        if err:
            return err
        return view(*args, **kwargs)
    return wrapped

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
    """Open a project's cache, scoped to the caller's session.

    Every project route goes through here, so this one check is what keeps a
    hosted instance from serving one visitor's uploads to another. A project
    id belonging to a different session reads as "unknown project" rather than
    "forbidden" - there is no reason to confirm it exists."""
    manifest = project_cache.get_manifest(project_id, scoped_session())
    if not manifest:
        return None, None
    # This instance may never have built this cache - fetch it back if some
    # earlier instance did and has since been recycled.
    if not project_cache.ensure_sqlite(manifest):
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


@app.route("/api/config")
def api_config():
    """Lets the frontend render the right UI for how this instance is running.
    Advisory only - every restriction it describes is enforced server-side."""
    return jsonify(appconfig.public_config())


@app.route("/api/projects")
@with_session
def api_projects():
    return jsonify(project_cache.list_projects(scoped_session()))


@app.route("/api/board")
@local_only
@with_session
def api_board():
    """What a project folder contains, grouped by ETAP module.

    Cheap by construction: a directory listing plus the .OTI pointers needed to
    resolve which database a model uses. No study file is opened here.
    """
    path = request.args.get("path", "").strip().strip('"')
    if not path:
        return jsonify({"error": "path is required"}), 400
    if not os.path.isdir(path):
        return jsonify({"error": f"Not a folder: {path}"}), 400
    try:
        return jsonify(board.from_folder(path, scoped_session()))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/board/scan", methods=["POST"])
@with_session
def api_board_scan():
    """Board built from a listing the browser enumerated locally.

    The hosted path. The body carries names, sizes and dates only - the folder
    is read on the engineer's machine and no file content is sent until they
    open a tile.
    """
    data = request.get_json(force=True, silent=True) or {}
    entries = data.get("files")
    if not isinstance(entries, list):
        return jsonify({"error": "files must be a list"}), 400
    if len(entries) > 5000:
        # A project folder holds tens of files. Anything at this scale is a
        # mistake (a drive root, say) and the answer would be useless anyway.
        return jsonify({"error": "Too many files - pick a project folder rather than a drive."}), 400
    return jsonify(board.from_listing(entries, scoped_session(),
                                      folder_name=(data.get("folder") or "").strip()))


@app.route("/api/browse")
@local_only
def api_browse():
    path = request.args.get("path", "")
    try:
        return jsonify(browse_fs.list_directory(path))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/browse/quick")
@local_only
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


def _start_job(loader_fn, session_id=None):
    """Run loader_fn(progress_cb) -> manifest on a background thread, tracked
    under a job_id that the frontend polls via /api/load/status/<job_id>."""
    job_id = uuid.uuid4().hex
    with _load_jobs_lock:
        _load_jobs[job_id] = {"stage": "starting", "current": 0, "total": 0,
                              "done": False, "error": None, "_session": session_id}

    def progress_cb(stage, current, total):
        with _load_jobs_lock:
            _load_jobs[job_id].update(stage=stage, current=current, total=total)

    def run():
        try:
            manifest = loader_fn(progress_cb)
            with _load_jobs_lock:
                _load_jobs[job_id].update(
                    done=True, project_id=manifest["project_id"],
                    # The stored manifest carries the session id and server
                    # paths; only the scrubbed view goes back to the client.
                    manifest=project_cache._public_manifest(manifest))
        except Exception as e:
            with _load_jobs_lock:
                _load_jobs[job_id].update(done=True, error=str(e))

    threading.Thread(target=run, daemon=True).start()
    return job_id


@app.route("/api/load", methods=["POST"])
@local_only
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
@with_session
def api_unload(project_id):
    removed = project_cache.unload_project(project_id, scoped_session())
    return jsonify({"removed": removed})


@app.route("/api/projects/clear", methods=["POST"])
@with_session
def api_clear_all():
    count = project_cache.clear_all(scoped_session())
    return jsonify({"cleared": count})


def _upload_dir(session_id, filename):
    """Uploads are namespaced by session. Without that, two people uploading
    a file called GM.TU1S write to the same path and clobber each other."""
    stem = os.path.splitext(filename)[0].lower() or "upload"
    d = os.path.join(project_cache.CACHE_DIR, "uploads",
                     secure_filename(session_id or sessions.SHARED), secure_filename(stem))
    os.makedirs(d, exist_ok=True)
    return d


def _ingest(dest_path, filename, session_id):
    """Validate an uploaded file and queue the import. Returns a Flask
    response tuple."""
    try:
        upload_guard.validate(
            dest_path, filename,
            allowed_extensions=set(appconfig.ACCEPTED_EXTENSIONS))
    except upload_guard.RejectedUpload as e:
        shutil.rmtree(os.path.dirname(dest_path), ignore_errors=True)
        return jsonify({"error": str(e)}), 400

    try:
        located = locate.locate(dest_path)
    except ValueError as e:
        shutil.rmtree(os.path.dirname(dest_path), ignore_errors=True)
        return jsonify({"error": str(e)}), 400
    located.note = f"Uploaded ({filename})"

    job_id = _start_job(
        lambda progress_cb: project_cache.load_located(
            located, input_path=dest_path, progress_cb=progress_cb,
            session_id=session_id, display_name=filename, uploaded=True),
        session_id=session_id)
    return jsonify({"job_id": job_id})


def _stage_companion(dest_path, filename, primary):
    """Validate a plot companion that has already been written to disk.

    Nothing is imported here and no project is created - the file is only
    parked next to its .HA1S so that ha_plots finds it when the study itself
    is ingested a moment later. Uploading the primary is what starts work.
    """
    try:
        upload_guard.validate_companion(dest_path, filename, primary)
    except upload_guard.RejectedUpload as e:
        # Only the offending file goes; the directory may already hold a
        # valid companion, and one bad file should not discard it.
        if os.path.isfile(dest_path):
            os.remove(dest_path)
        return jsonify({"error": str(e)}), 400
    return jsonify({"staged": filename, "bytes": os.path.getsize(dest_path)})


@app.route("/api/upload/companion", methods=["POST"])
def api_upload_companion():
    """Direct multipart upload of a .fspdb/.hfpdb beside its .HA1S.

    Companions are keyed to the primary's upload directory, which is named
    after the filename stem - the same stem the companion must have - so the
    two arrive side by side without either knowing the other's path.
    """
    session_id, err = current_session()
    if err:
        return err
    over = _quota_exceeded(session_id)
    if over:
        return over

    f = request.files.get("file")
    primary = secure_filename(request.form.get("primary", "")) or ""
    if not f or not f.filename:
        return jsonify({"error": "no file uploaded"}), 400
    if not primary:
        return jsonify({"error": "primary is required"}), 400

    filename = secure_filename(f.filename) or "upload"
    try:
        upload_guard.check_companion_name(filename, primary)
    except upload_guard.RejectedUpload as e:
        return jsonify({"error": str(e)}), 400

    dest_path = os.path.join(_upload_dir(session_id, primary), filename)
    f.save(dest_path)
    return _stage_companion(dest_path, filename, primary)


@app.route("/api/upload/companion/complete", methods=["POST"])
def api_upload_companion_complete():
    """Signed-URL equivalent of the above: the object is already in storage,
    pull it down beside its .HA1S."""
    session_id, err = current_session()
    if err:
        return err
    over = _quota_exceeded(session_id)
    if over:
        return over

    data = request.get_json(silent=True) or {}
    key = data.get("key", "")
    primary = secure_filename(data.get("primary", "")) or ""
    if not key.startswith(f"uploads/{session_id}/"):
        return jsonify({"error": "Key does not belong to this session."}), 403
    if not primary:
        return jsonify({"error": "primary is required"}), 400

    filename = secure_filename(os.path.basename(key)) or "upload"
    try:
        upload_guard.check_companion_name(filename, primary)
    except upload_guard.RejectedUpload as e:
        _storage.delete(key)
        return jsonify({"error": str(e)}), 400

    try:
        if not _storage.exists(key):
            return jsonify({"error": "Upload not found. Try uploading again."}), 404
        size = _storage.size(key)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if size > appconfig.MAX_UPLOAD_BYTES:
        _storage.delete(key)
        return jsonify({
            "error": f"That file is larger than the {appconfig.MAX_UPLOAD_MB} MB limit."
        }), 413

    dest_path = os.path.join(_upload_dir(session_id, primary), filename)
    _storage.download_to(key, dest_path)
    _storage.delete(key)
    return _stage_companion(dest_path, filename, primary)


def _quota_exceeded(session_id):
    if not appconfig.REQUIRE_SESSION:
        return None
    n = project_cache.session_upload_count(session_id)
    if n >= appconfig.MAX_UPLOADS_PER_SESSION:
        return jsonify({
            "error": f"You have {n} files loaded, which is the limit. "
                     f"Unload one before adding another."
        }), 429
    return None


@app.route("/api/upload", methods=["POST"])
def api_upload_start():
    """Direct multipart upload.

    Fine locally and for small files. On Cloud Run the request body limit is
    32 MB, well under a typical study result, which is why the hosted flow
    uses /api/upload/url and has the browser PUT straight to the bucket."""
    session_id, err = current_session()
    if err:
        return err
    over = _quota_exceeded(session_id)
    if over:
        return over

    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "no file uploaded"}), 400

    filename = secure_filename(f.filename) or "upload"
    dest_path = os.path.join(_upload_dir(session_id, filename), filename)
    f.save(dest_path)
    return _ingest(dest_path, filename, session_id)


@app.route("/api/upload/url", methods=["POST"])
def api_upload_url():
    """Hand out a one-shot upload URL.

    This is the endpoint Turnstile guards: every URL it returns is permission
    to write a few hundred MB into the bucket, which is the only part of this
    app that costs real money to abuse."""
    session_id, err = current_session()
    if err:
        return err
    over = _quota_exceeded(session_id)
    if over:
        return over

    data = request.get_json(silent=True) or {}
    filename = secure_filename(data.get("filename", "")) or ""
    if not filename:
        return jsonify({"error": "filename is required"}), 400

    try:
        upload_guard.check_extension(filename, set(appconfig.ACCEPTED_EXTENSIONS))
        # A study's plot companions are granted in the same breath rather than
        # per call. A Turnstile token is single-use, so three grants would
        # need three challenges - and the companions are already bounded to
        # "<this study's stem>.<companion ext>", which is the whole reason
        # they can be handed out without a challenge of their own.
        companions = [secure_filename(n) or "" for n in (data.get("companions") or [])]
        companions = [n for n in companions if n]
        for name in companions:
            upload_guard.check_companion_name(name, filename)
    except upload_guard.RejectedUpload as e:
        return jsonify({"error": str(e)}), 400

    if appconfig.TURNSTILE_ENABLED:
        ok, msg = turnstile.verify(
            data.get("turnstile_token", ""), appconfig.TURNSTILE_SECRET_KEY,
            remote_ip=request.headers.get("CF-Connecting-IP") or request.remote_addr)
        if not ok:
            return jsonify({"error": msg}), 403

    def grant(name):
        key = f"uploads/{session_id}/{uuid.uuid4().hex}/{name}"
        return {"key": key, "upload": _storage.signed_upload_url(
            key, content_type="application/octet-stream",
            max_bytes=appconfig.MAX_UPLOAD_BYTES)}

    primary = grant(filename)
    return jsonify({**primary,
                    "companions": [{"filename": n, **grant(n)} for n in companions],
                    "max_upload_mb": appconfig.MAX_UPLOAD_MB})


@app.route("/api/upload/direct", methods=["PUT"])
def api_upload_direct():
    """Receiving end of a LocalStorage 'signed URL'.

    Only reachable when no bucket is configured; with GCS the browser PUTs to
    Google and this never runs. It exists so the hosted upload flow can be
    exercised end to end without a cloud account."""
    if _storage.kind != "local":
        return jsonify({"error": "Not available with object storage configured."}), 404
    session_id, err = current_session()
    if err:
        return err

    key = request.args.get("key", "")
    if not key.startswith(f"uploads/{session_id}/"):
        return jsonify({"error": "Key does not belong to this session."}), 403

    tmp = os.path.join(project_cache.CACHE_DIR, "objects", key.replace("/", os.sep))
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, "wb") as fh:
        fh.write(request.get_data())
    return jsonify({"key": key, "bytes": os.path.getsize(tmp)})


@app.route("/api/upload/complete", methods=["POST"])
def api_upload_complete():
    """Told that an object is in storage, pull it down and import it."""
    session_id, err = current_session()
    if err:
        return err
    over = _quota_exceeded(session_id)
    if over:
        return over

    data = request.get_json(silent=True) or {}
    key = data.get("key", "")
    # A key naming another session's prefix is the whole attack here.
    if not key.startswith(f"uploads/{session_id}/"):
        return jsonify({"error": "Key does not belong to this session."}), 403
    try:
        if not _storage.exists(key):
            return jsonify({"error": "Upload not found. Try uploading again."}), 404
        size = _storage.size(key)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    if size > appconfig.MAX_UPLOAD_BYTES:
        _storage.delete(key)
        return jsonify({
            "error": f"That file is larger than the {appconfig.MAX_UPLOAD_MB} MB limit."
        }), 413

    filename = secure_filename(os.path.basename(key)) or "upload"
    dest_path = os.path.join(_upload_dir(session_id, filename), filename)
    _storage.download_to(key, dest_path)
    # The source object has served its purpose; keeping it doubles storage for
    # a file we have already copied out.
    _storage.delete(key)
    return _ingest(dest_path, filename, session_id)


@app.route("/api/load/status/<job_id>")
def api_load_status(job_id):
    session_id, err = current_session()
    if err:
        return err
    with _load_jobs_lock:
        job = _load_jobs.get(job_id)
        if not job:
            return jsonify({"error": "unknown job"}), 404
        # Job ids are random, but a job carries a manifest, so ownership is
        # checked rather than assumed from unguessability.
        if appconfig.REQUIRE_SESSION and job.get("_session") != session_id:
            return jsonify({"error": "unknown job"}), 404
        return jsonify({k: v for k, v in job.items() if not k.startswith("_")})


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


# Rows per sheet in a whole-database export. Excel's own ceiling is ~1.05M,
# but a sheet nobody can scroll is not the constraint that matters: a
# time-domain study holds 473k rows in each of two tables, and writing them
# costs minutes and a workbook nothing opens comfortably. Tables past this are
# written truncated and the index sheet says which.
EXPORT_ROWS_PER_SHEET = 50_000


@app.route("/api/project/<project_id>/export_all")
def api_export_all(project_id):
    """Every table's contents in one workbook, a sheet each.

    Distinct from the client-side index export, which lists what the tables
    are. This is what they hold.
    """
    conn, manifest = _db(project_id)
    if not conn:
        return jsonify({"error": "unknown project"}), 404

    category_set = manifest.get("category_set", "model")
    tables = conn.execute(
        "SELECT table_name, row_count FROM _table_index ORDER BY table_name").fetchall()

    index_columns = ["Table", "Category", "Rows", "Exported"]
    index_rows = []
    included = []
    for name, count in tables:
        count = count or 0
        index_rows.append({
            "Table": name,
            "Category": cat_defs.category_for_table(name, category_set) or "",
            "Rows": count,
            "Exported": min(count, EXPORT_ROWS_PER_SHEET) if count else "empty - no sheet",
        })
        if count:
            included.append(name)

    def sheets():
        for name in included:
            cur = conn.execute(f'SELECT * FROM "{name}" LIMIT {EXPORT_ROWS_PER_SHEET}')
            cols = [d[0] for d in cur.description]
            yield name, cat_defs.order_columns(cols), _rows_as_dicts(cur)

    try:
        buf = xlsx_export.all_tables_xlsx_bytes(sheets(), index_columns, index_rows)
    finally:
        conn.close()

    filename = secure_filename(f"{manifest['db_name']}_all_tables.xlsx")
    return send_file(buf, as_attachment=True, download_name=filename,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


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
        {"table": table, "title": title, "rows": dump(table),
         "reference": table in cat_defs.OVERVIEW_REFERENCE_TABLES}
        for table, title in cat_defs.OVERVIEW_INFO_TABLES.items()
        if _table_exists(conn, table)
    ]

    result = {
        # Sanitized, like every other manifest that leaves this process. The
        # raw one carries the session id - a bearer token - along with the
        # server's own paths, and this endpoint renders straight onto the
        # overview page, so those were being displayed on screen.
        "manifest": project_cache._public_manifest(manifest),
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
    # Hosted deployments run this under gunicorn (see Dockerfile), so this
    # block is the local desktop path. debug is gated on mode regardless -
    # Werkzeug's debugger is remote code execution if it ever faces a network.
    app.run(host="127.0.0.1", port=5151,
            debug=appconfig.IS_LOCAL, use_reloader=False)
