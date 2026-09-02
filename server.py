#!/usr/bin/env python3
"""EPR local server: static viewer/admin UI + JSON API backed by SQLite.

Run:  python3 server.py  [--port 8080] [--host 0.0.0.0]
Then open http://<host-ip>:8080/         (viewer, read-only)
     or   http://<host-ip>:8080/admin/   (editor)

All deployment state lives under $EPR_DATA_DIR (default ./data). The code never
writes outside it, so replacing the code / image never touches patient data.

No authentication. Intended for an isolated simulation network only.
"""
import argparse
import datetime
import json
import mimetypes
import os
import threading
import uuid

from flask import Flask, abort, g, jsonify, request, send_file, send_from_directory

import db as store
from db import CHANGELOG_PATH, DATA_DIR, DB_PATH, FILES_DIR

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(BASE_DIR, "app")

SHEET_NAMES = ("details", "flowsheets", "results", "notes", "mar")
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB per file

# ------------------------------------------------------------- sheet templates
# New patients are seeded with these so Flowsheets / Results / MAR are never
# empty. Flowsheets + MAR label columns are treated as fixed by the admin
# editors (see app/shared.js); Results is a starting point you edit per patient.
DETAILS_STANDARD_ROWS = [
    "Name:",
    "DOB:",
    "Gender ID:",
    "Legal Sex:",
    "Sex Assigned at Birth:",
    "Ethnicity:",
    "Languages:",
    "MRN:",
    "NHS No:",
    "NOK:",
    "Ward:",
    "Bed No:",
    "Allergy:",
]

FLOWSHEET_STANDARD_ROWS = [
    "Respiratory Rate",
    "SpO2",
    "Oxygen Therapy",
    "BP",
    "Heart Rate",
    "Level of Consciousness",
    "Temperature",
    "Temp Source",
]

MAR_BLOCK_LABELS = ["Drug", "Start Date", "Duration", "Prescriber", "Dose", "Route", "Freq"]
MAR_DAY_COLUMNS = ["3 DAYS AGO", "2 DAYS AGO", "YESTERDAY", "TODAY"]
MAR_SEED_DRUG_BLOCKS = 4

RESULTS_STANDARD_PANEL = [
    ("Hb", "130 to 180"),
    ("MCV", "80 to 100"),
    ("WBC", "4.0 to 11.0"),
    ("Platelets", "150 to 450"),
    ("Neutrophils", "2.0 to 7.5"),
    ("INR", ""),
    ("Sodium", "135 to 145"),
    ("Potassium", "3.5 to 5.1"),
    ("Urea", "2.1 to 7.1"),
    ("Creatinine", "84 to 114"),
    ("eGFR", ""),
    ("Bilirubin", "5 to 21"),
    ("Albumin", "34 to 48"),
    ("Total Protein", "64 to 83"),
    ("ALP", "25 to 114"),
    ("ALT", "7 to 56"),
    ("Adj Ca", "2.15 to 2.50"),
    ("Phosphate", "0.87 to 1.45"),
    ("CRP", "0 to 10"),
]


def sheet_template(name):
    if name == "details":
        return [[label, ""] for label in DETAILS_STANDARD_ROWS]
    if name == "flowsheets":
        grid = [["", "", "", ""]]
        for label in FLOWSHEET_STANDARD_ROWS:
            grid.append([label, "", "", ""])
        return grid
    if name == "results":
        grid = [["", "Reference range", "Today"]]
        for test, ref in RESULTS_STANDARD_PANEL:
            grid.append([test, ref, ""])
        return grid
    if name == "mar":
        width = 3 + len(MAR_DAY_COLUMNS)
        grid = [["", "", ""] + list(MAR_DAY_COLUMNS)]
        for _ in range(MAR_SEED_DRUG_BLOCKS):
            for label in MAR_BLOCK_LABELS:
                grid.append([label] + [""] * (width - 1))
        return grid
    return []

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
app.json.sort_keys = False  # preserve meta / grid key order


# --------------------------------------------------------------------------- db
_booted = False


def boot():
    """Create/upgrade the database. Safe to call repeatedly; runs once."""
    global _booted
    if _booted:
        return
    con = store.connect()
    try:
        store.migrate(con)
    finally:
        con.close()
    _booted = True


def get_db():
    boot()
    if "db" not in g:
        g.db = store.connect()
    return g.db


@app.teardown_appcontext
def close_db(exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


# ---------------------------------------------------------------- serialisers
def patient_row(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "meta": json.loads(row["meta"] or "{}"),
        "sort_order": row["sort_order"],
    }


def normalise_grid(value):
    """Accept {'grid': [[...]]} or a bare 2D list; return a list of list[str]."""
    if isinstance(value, dict):
        value = value.get("grid")
    if not isinstance(value, list):
        abort(400, "grid must be a 2D array")
    out = []
    for r in value:
        if not isinstance(r, list):
            abort(400, "grid must be a 2D array")
        out.append(["" if c is None else str(c) for c in r])
    return out


def normalise_meta(value):
    if value is None:
        return {}
    if not isinstance(value, dict):
        abort(400, "meta must be an object")
    return {str(k): ("" if v is None else str(v)) for k, v in value.items()}


# ------------------------------------------------------------------ change log
# Every write through the admin API appends human-readable, tab-separated lines
# to data/changes.log:
#   <iso-timestamp> \t <editor> \t <ip> \t <action> \t <patient> \t <sheet> \t <detail>
# The editor name comes from the X-Editor header the admin UI attaches (set once
# per browser). Read back via GET /api/changes and app/history.html.
_changelog_lock = threading.Lock()


def _clean(text):
    return str(text).replace("\t", " ").replace("\r", "").replace("\n", " / ")


def log_change(action, patient, sheet, details):
    if isinstance(details, str):
        details = [details]
    details = [d for d in details if d]
    if not details:
        return
    editor = (request.headers.get("X-Editor") or "").strip() or "unknown"
    ip = request.remote_addr or "?"
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    rows = [
        "\t".join([ts, _clean(editor), ip, action, _clean(patient), sheet or "-", _clean(d)])
        for d in details
    ]
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with _changelog_lock, open(CHANGELOG_PATH, "a", encoding="utf-8") as fh:
            fh.write("\n".join(rows) + "\n")
    except OSError:
        pass


def diff_meta(old, new):
    out = []
    for k in list(old) + [k for k in new if k not in old]:
        o, n = old.get(k, ""), new.get(k, "")
        if o == n:
            continue
        if k not in old:
            out.append(f'added "{k}": "{n}"')
        elif k not in new:
            out.append(f'removed "{k}" (was "{o}")')
        else:
            out.append(f'"{k}": "{o}" -> "{n}"')
    return out


def diff_grid(old, new, cap=60):
    def at(g, r, c):
        try:
            return str(g[r][c])
        except IndexError:
            return ""

    def row_label(r):
        src = new if r < len(new) else old
        return (at(src, r, 0) or "").strip() or f"row {r + 1}"

    def col_label(c):
        return (at(new, 0, c) or at(old, 0, c) or "").strip() or f"col {c + 1}"

    out = []
    for r in range(max(len(old), len(new))):
        orow = old[r] if r < len(old) else None
        nrow = new[r] if r < len(new) else None
        if orow is None:
            if any(str(x).strip() for x in nrow):
                out.append(f'row added: {" | ".join(str(x) for x in nrow)}')
            continue
        if nrow is None:
            if any(str(x).strip() for x in orow):
                out.append(f'row removed: {" | ".join(str(x) for x in orow)}')
            continue
        for c in range(max(len(orow), len(nrow))):
            o = orow[c] if c < len(orow) else ""
            n = nrow[c] if c < len(nrow) else ""
            if str(o) == str(n):
                continue
            if r == 0:
                out.append(f'column header {c + 1}: "{o}" -> "{n}"')
            else:
                out.append(f'"{row_label(r)}" / "{col_label(c)}": "{o}" -> "{n}"')
        if len(out) > cap:
            out.append("… more changes not listed")
            return out
    return out


def patient_name(db, pid):
    row = db.execute("SELECT name FROM patients WHERE id = ?", (pid,)).fetchone()
    return row["name"] if row else f"#{pid}"


# ---------------------------------------------------------------- static UI
def _send_app_file(path):
    if not path or path.endswith("/"):
        path = (path or "") + "index.html"
    full = os.path.normpath(os.path.join(APP_DIR, path))
    if not full.startswith(APP_DIR + os.sep) and full != APP_DIR:
        abort(404)
    if not os.path.isfile(full):
        abort(404)
    return send_from_directory(APP_DIR, os.path.relpath(full, APP_DIR))


@app.route("/")
def root():
    return _send_app_file("index.html")


@app.route("/admin")
@app.route("/admin/")
def admin_root():
    return _send_app_file("index.html")


@app.route("/admin/<path:path>")
def admin_asset(path):
    return _send_app_file(path)


@app.route("/<path:path>")
def asset(path):
    if path == "api" or path.startswith("api/"):
        abort(404)  # unmatched /api/* never falls through to a file lookup
    return _send_app_file(path)


# ------------------------------------------------------------------- meta API
@app.get("/api/meta")
def meta():
    return jsonify(
        {
            "sheets": list(SHEET_NAMES),
            "details_standard_rows": DETAILS_STANDARD_ROWS,
            "flowsheet_standard_rows": FLOWSHEET_STANDARD_ROWS,
            "mar_block_labels": MAR_BLOCK_LABELS,
            "mar_day_columns": MAR_DAY_COLUMNS,
        }
    )


@app.get("/api/changes")
def changes():
    try:
        limit = max(1, min(int(request.args.get("limit", 400)), 5000))
    except ValueError:
        limit = 400
    if not os.path.isfile(CHANGELOG_PATH):
        return jsonify([])
    with open(CHANGELOG_PATH, "r", encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    out = []
    for ln in lines[-limit:]:
        p = ln.split("\t")
        if len(p) >= 7:
            out.append(
                {
                    "ts": p[0], "editor": p[1], "ip": p[2], "action": p[3],
                    "patient": p[4], "sheet": p[5], "detail": "\t".join(p[6:]),
                }
            )
    out.reverse()
    return jsonify(out)


# ---------------------------------------------------------------- patients API
@app.get("/api/patients")
def list_patients():
    rows = get_db().execute(
        "SELECT * FROM patients ORDER BY sort_order, name COLLATE NOCASE"
    ).fetchall()
    return jsonify([patient_row(r) for r in rows])


@app.post("/api/patients")
def create_patient():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        abort(400, "name is required")
    meta = normalise_meta(body.get("meta"))
    db = get_db()
    nxt = db.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 AS n FROM patients").fetchone()["n"]
    cur = db.execute(
        "INSERT INTO patients (name, meta, sort_order) VALUES (?, ?, ?)",
        (name, json.dumps(meta), nxt),
    )
    pid = cur.lastrowid
    for sheet in SHEET_NAMES:
        db.execute(
            "INSERT INTO sheets (patient_id, name, grid) VALUES (?, ?, ?)",
            (pid, sheet, json.dumps(sheet_template(sheet))),
        )
    db.commit()
    row = db.execute("SELECT * FROM patients WHERE id = ?", (pid,)).fetchone()
    log_change("create", name, None, "patient created")
    return jsonify(patient_row(row)), 201


@app.get("/api/patients/<int:pid>")
def get_patient(pid):
    row = get_db().execute("SELECT * FROM patients WHERE id = ?", (pid,)).fetchone()
    if row is None:
        abort(404)
    return jsonify(patient_row(row))


@app.put("/api/patients/<int:pid>")
def update_patient(pid):
    db = get_db()
    row = db.execute("SELECT * FROM patients WHERE id = ?", (pid,)).fetchone()
    if row is None:
        abort(404)
    body = request.get_json(silent=True) or {}
    old_name = row["name"]
    old_meta = json.loads(row["meta"] or "{}")
    name = old_name
    if "name" in body:
        name = (body.get("name") or "").strip()
        if not name:
            abort(400, "name cannot be empty")
    meta = old_meta
    if "meta" in body:
        meta = normalise_meta(body.get("meta"))
    sort_order = row["sort_order"]
    if "sort_order" in body:
        try:
            sort_order = int(body["sort_order"])
        except (TypeError, ValueError):
            abort(400, "sort_order must be an integer")
    db.execute(
        "UPDATE patients SET name = ?, meta = ?, sort_order = ? WHERE id = ?",
        (name, json.dumps(meta), sort_order, pid),
    )
    db.commit()
    if name != old_name:
        log_change("rename", old_name, None, f'"{old_name}" -> "{name}"')
    log_change("meta", name, None, diff_meta(old_meta, meta))
    return jsonify(patient_row(db.execute("SELECT * FROM patients WHERE id = ?", (pid,)).fetchone()))


@app.delete("/api/patients/<int:pid>")
def delete_patient(pid):
    db = get_db()
    prow = db.execute("SELECT name FROM patients WHERE id = ?", (pid,)).fetchone()
    if prow is None:
        abort(404)
    rows = db.execute("SELECT path FROM files WHERE patient_id = ?", (pid,)).fetchall()
    nsheets = db.execute("SELECT COUNT(*) c FROM sheets WHERE patient_id = ?", (pid,)).fetchone()["c"]
    db.execute("DELETE FROM patients WHERE id = ?", (pid,))
    db.commit()
    log_change("delete", prow["name"], None,
               f"patient deleted ({nsheets} sheets, {len(rows)} files)")
    for r in rows:
        _remove_file(r["path"])
    pdir = os.path.join(FILES_DIR, str(pid))
    if os.path.isdir(pdir):
        try:
            os.rmdir(pdir)
        except OSError:
            pass
    return "", 204


# ------------------------------------------------------------------ sheets API
@app.get("/api/patients/<int:pid>/sheets/<sheet>")
def get_sheet(pid, sheet):
    if sheet not in SHEET_NAMES:
        abort(404)
    db = get_db()
    if db.execute("SELECT 1 FROM patients WHERE id = ?", (pid,)).fetchone() is None:
        abort(404)
    row = db.execute(
        "SELECT grid FROM sheets WHERE patient_id = ? AND name = ?", (pid, sheet)
    ).fetchone()
    grid = json.loads(row["grid"]) if row else []
    return jsonify({"grid": grid})


@app.put("/api/patients/<int:pid>/sheets/<sheet>")
def put_sheet(pid, sheet):
    if sheet not in SHEET_NAMES:
        abort(404)
    db = get_db()
    if db.execute("SELECT 1 FROM patients WHERE id = ?", (pid,)).fetchone() is None:
        abort(404)
    grid = normalise_grid(request.get_json(silent=True))
    prev = db.execute(
        "SELECT grid FROM sheets WHERE patient_id = ? AND name = ?", (pid, sheet)
    ).fetchone()
    old_grid = json.loads(prev["grid"]) if prev else []
    db.execute(
        "INSERT INTO sheets (patient_id, name, grid) VALUES (?, ?, ?) "
        "ON CONFLICT(patient_id, name) DO UPDATE SET grid = excluded.grid",
        (pid, sheet, json.dumps(grid)),
    )
    db.commit()
    log_change("edit", patient_name(db, pid), sheet, diff_grid(old_grid, grid))
    return jsonify({"grid": grid})


# ------------------------------------------------------------------- files API
def _remove_file(rel_path):
    full = os.path.join(FILES_DIR, rel_path)
    try:
        os.remove(full)
    except OSError:
        pass


@app.get("/api/patients/<int:pid>/files")
def list_files(pid):
    db = get_db()
    if db.execute("SELECT 1 FROM patients WHERE id = ?", (pid,)).fetchone() is None:
        abort(404)
    rows = db.execute(
        "SELECT id, filename, mime, size FROM files WHERE patient_id = ? ORDER BY filename COLLATE NOCASE",
        (pid,),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/patients/<int:pid>/files")
def upload_file(pid):
    db = get_db()
    if db.execute("SELECT 1 FROM patients WHERE id = ?", (pid,)).fetchone() is None:
        abort(404)
    if "file" not in request.files:
        abort(400, "no file field")
    up = request.files["file"]
    original = os.path.basename(up.filename or "").strip()
    if not original:
        abort(400, "empty filename")
    ext = os.path.splitext(original)[1].lower()
    stored = f"{uuid.uuid4().hex}{ext}"
    rel = os.path.join(str(pid), stored)
    pdir = os.path.join(FILES_DIR, str(pid))
    os.makedirs(pdir, exist_ok=True)
    dest = os.path.join(pdir, stored)
    up.save(dest)
    size = os.path.getsize(dest)
    mime = up.mimetype or mimetypes.guess_type(original)[0] or "application/octet-stream"
    cur = db.execute(
        "INSERT INTO files (patient_id, filename, mime, size, path) VALUES (?, ?, ?, ?, ?)",
        (pid, original, mime, size, rel),
    )
    db.commit()
    log_change("upload-file", patient_name(db, pid), None, f'added file "{original}"')
    return jsonify(
        {"id": cur.lastrowid, "filename": original, "mime": mime, "size": size}
    ), 201


@app.get("/api/files/<int:fid>")
def download_file(fid):
    row = get_db().execute("SELECT * FROM files WHERE id = ?", (fid,)).fetchone()
    if row is None:
        abort(404)
    full = os.path.join(FILES_DIR, row["path"])
    if not os.path.isfile(full):
        abort(404)
    return send_file(
        full, mimetype=row["mime"], as_attachment=False, download_name=row["filename"]
    )


@app.delete("/api/files/<int:fid>")
def delete_file(fid):
    db = get_db()
    row = db.execute("SELECT * FROM files WHERE id = ?", (fid,)).fetchone()
    if row is None:
        abort(404)
    db.execute("DELETE FROM files WHERE id = ?", (fid,))
    db.commit()
    _remove_file(row["path"])
    log_change("delete-file", patient_name(db, row["patient_id"]), None,
               f'removed file "{row["filename"]}"')
    return "", 204


# ---------------------------------------------------------------- error shape
@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(413)
def json_error(err):
    return jsonify({"error": getattr(err, "description", str(err))}), err.code


# ------------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description="EPR local server")
    ap.add_argument("--host", default=os.environ.get("EPR_HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("EPR_PORT", "8080")))
    args = ap.parse_args()

    boot()

    lan = "127.0.0.1"
    try:
        import socket

        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        lan = s.getsockname()[0]
        s.close()
    except OSError:
        pass

    print("EPR server running")
    print(f"  data   :  {DATA_DIR}")
    print(f"  viewer :  http://{lan}:{args.port}/")
    print(f"  admin  :  http://{lan}:{args.port}/admin/")
    print("  (Ctrl+C to stop)")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


# Run migrations at import time too, so a WSGI host (gunicorn server:app) is covered.
boot()


if __name__ == "__main__":
    main()
