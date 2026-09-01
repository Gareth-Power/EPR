#!/usr/bin/env python3
"""One-off migration: an old "Patients/" Excel folder tree -> $EPR_DATA_DIR/epr.db.

Reads <source>/patients.csv, each <source>/<Name>/information.xlsx (sheets
details, flowsheets, results, notes, mar) and each <source>/<Name>/Files/*, and
loads them into the SQLite database used by server.py. This was used once to
migrate the original data; it is kept for reference / re-runs.

Usage:  python3 scripts/import_legacy.py --source /path/to/Patients [--force]

Safe to re-run: refuses to touch a database that already has patients unless
--force is given (which wipes and re-imports).

Pure standard library - no openpyxl. The .xlsx reader below is minimal but
handles shared strings, inline strings, and number formats well enough to
reproduce what the old SheetJS front-end displayed (dates, HH:MM times).
"""
import argparse
import csv
import datetime as dt
import difflib
import json
import mimetypes
import os
import re
import shutil
import sys
import xml.etree.ElementTree as ET
import zipfile

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
import db as store  # noqa: E402  (needs BASE_DIR on the path first)

DEFAULT_SOURCE = os.path.join(BASE_DIR, "Patients")
FILES_DIR = store.FILES_DIR

SHEET_NAMES = ("details", "flowsheets", "results", "notes", "mar")
MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
SKIP_FILES = {".ds_store", "thumbs.db"}

BUILTIN_DATE = {14, 15, 16, 17, 22}
BUILTIN_TIME = {18, 19, 20, 21, 45, 46, 47}


def q(tag):
    return f"{{{MAIN_NS}}}{tag}"


# --------------------------------------------------------------- xlsx reading
def _col_row(ref):
    m = re.match(r"([A-Z]+)(\d+)", ref)
    letters, row = m.group(1), int(m.group(2))
    c = 0
    for ch in letters:
        c = c * 26 + (ord(ch) - 64)
    return c - 1, row - 1


def _excel_serial_to_date(serial):
    # Excel epoch with the 1900 leap-year bug: serial 1 == 1900-01-01.
    return dt.datetime(1899, 12, 30) + dt.timedelta(days=float(serial))


def _fmt_is_time(code):
    c = re.sub(r"\[[^\]]*\]", "", code or "").lower()
    c = re.sub(r'"[^"]*"', "", c)
    return ("h" in c or "s" in c) and ":" in c and "y" not in c and "d" not in c


def _fmt_is_date(code):
    c = re.sub(r"\[[^\]]*\]", "", code or "").lower()
    c = re.sub(r'"[^"]*"', "", c)
    return ("y" in c or "d" in c) and "h" not in c


def _format_number(value, fmt_code):
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)

    is_date = fmt_code is not None and _fmt_is_date(fmt_code)
    is_time = fmt_code is not None and _fmt_is_time(fmt_code)

    if is_time and not is_date:
        secs = int(round((num % 1) * 86400))
        return f"{secs // 3600:02d}:{(secs % 3600) // 60:02d}"
    if is_date:
        d = _excel_serial_to_date(num)
        return d.strftime("%d/%m/%Y")

    # plain number: drop float noise, keep integers clean
    if abs(num - round(num)) < 1e-9:
        return str(int(round(num)))
    return repr(round(num, 6)).rstrip("0").rstrip(".")


def read_workbook(path):
    """Return {sheet_name: [[str, ...], ...]} for every sheet in the workbook."""
    z = zipfile.ZipFile(path)
    names = set(z.namelist())

    shared = []
    if "xl/sharedStrings.xml" in names:
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in root.findall(q("si")):
            shared.append("".join(t.text or "" for t in si.iter(q("t"))))

    # number formats
    custom_fmts = {}
    xf_fmt_ids = []
    if "xl/styles.xml" in names:
        st = ET.fromstring(z.read("xl/styles.xml"))
        nf = st.find(q("numFmts"))
        if nf is not None:
            for f in nf.findall(q("numFmt")):
                custom_fmts[int(f.get("numFmtId"))] = f.get("formatCode")
        cell_xfs = st.find(q("cellXfs"))
        if cell_xfs is not None:
            for xf in cell_xfs.findall(q("xf")):
                xf_fmt_ids.append(int(xf.get("numFmtId", "0")))

    def fmt_for_style(style_idx):
        if style_idx is None:
            return None
        try:
            fmt_id = xf_fmt_ids[int(style_idx)]
        except (IndexError, ValueError):
            return None
        if fmt_id in custom_fmts:
            return custom_fmts[fmt_id]
        if fmt_id in BUILTIN_DATE:
            return "dd/mm/yyyy"
        if fmt_id in BUILTIN_TIME:
            return "h:mm"
        return None

    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    rel_map = {r.get("Id"): r.get("Target") for r in rels}

    result = {}
    for s in wb.find(q("sheets")).findall(q("sheet")):
        name = s.get("name")
        rid = s.get(f"{{{REL_NS}}}id")
        target = rel_map[rid]
        part = target.lstrip("/") if target.startswith("/") else "xl/" + target

        root = ET.fromstring(z.read(part))
        cells = {}
        max_c = max_r = 0
        for row in root.iter(q("row")):
            for c in row.findall(q("c")):
                ref = c.get("r")
                ctype = c.get("t")
                style = c.get("s")
                v = c.find(q("v"))
                is_el = c.find(q("is"))
                if ctype == "s" and v is not None:
                    text = shared[int(v.text)]
                elif ctype in ("inlineStr", "str") and is_el is not None:
                    text = "".join(t.text or "" for t in is_el.iter(q("t")))
                elif ctype == "str" and v is not None:
                    text = v.text or ""
                elif v is not None:
                    text = _format_number(v.text, fmt_for_style(style))
                else:
                    text = ""
                ci, ri = _col_row(ref)
                cells[(ri, ci)] = text
                max_c = max(max_c, ci)
                max_r = max(max_r, ri)

        grid = []
        for ri in range(max_r + 1):
            grid.append([cells.get((ri, ci), "") for ci in range(max_c + 1)])
        # trim fully-empty trailing rows
        while grid and not any(str(x).strip() for x in grid[-1]):
            grid.pop()
        result[name] = grid
    return result


# ------------------------------------------------------------------- importing
def resolve_folder(csv_name, folders_lower):
    key = csv_name.strip().lower()
    if key in folders_lower:
        return folders_lower[key]
    # tolerate small spelling slips (e.g. "Micheal" vs "Michael")
    close = difflib.get_close_matches(key, list(folders_lower), n=1, cutoff=0.9)
    if close:
        return folders_lower[close[0]]
    return None


def main():
    ap = argparse.ArgumentParser(
        description="Import the old Patients/ folder tree into $EPR_DATA_DIR/epr.db"
    )
    ap.add_argument("--source", default=DEFAULT_SOURCE,
                    help=f"path to the legacy Patients folder (default: {DEFAULT_SOURCE})")
    ap.add_argument("--force", action="store_true", help="wipe existing data and re-import")
    args = ap.parse_args()

    legacy_dir = os.path.abspath(args.source)
    if not os.path.isdir(legacy_dir):
        sys.exit(f"No legacy folder found at {legacy_dir}")

    print(f"source : {legacy_dir}")
    print(f"target : {store.DB_PATH}")

    con = store.connect()
    store.migrate(con)

    existing = con.execute("SELECT COUNT(*) FROM patients").fetchone()[0]
    if existing and not args.force:
        sys.exit(f"Database already has {existing} patients. Re-run with --force to replace them.")
    if existing and args.force:
        con.execute("DELETE FROM patients")
        con.commit()
        if os.path.isdir(FILES_DIR):
            shutil.rmtree(FILES_DIR)

    os.makedirs(FILES_DIR, exist_ok=True)

    folders = [d for d in os.listdir(legacy_dir) if os.path.isdir(os.path.join(legacy_dir, d))]
    folders_lower = {d.lower(): d for d in folders}
    used_folders = set()
    warnings = []

    # ---- patients from the CSV (defines list order + columns) ----
    csv_path = os.path.join(legacy_dir, "patients.csv")
    rows = []
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        header = [h.strip() for h in header]
        for r in reader:
            if r and any(cell.strip() for cell in r):
                rows.append(r)

    n_patients = n_sheets = n_files = 0
    order = 0

    def import_patient(name, meta, folder):
        nonlocal n_patients, n_sheets, n_files, order
        order += 1
        cur = con.execute(
            "INSERT INTO patients (name, meta, sort_order) VALUES (?, ?, ?)",
            (name, json.dumps(meta, ensure_ascii=False), order),
        )
        pid = cur.lastrowid
        n_patients += 1

        grids = {}
        if folder:
            xlsx = os.path.join(legacy_dir, folder, "information.xlsx")
            if os.path.isfile(xlsx):
                try:
                    wb = read_workbook(xlsx)
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"{name}: failed to read workbook ({exc})")
                    wb = {}
                first = next(iter(wb.values()), [])
                for sheet in SHEET_NAMES:
                    grids[sheet] = wb.get(sheet, first if sheet == "details" else [])
            else:
                warnings.append(f"{name}: no information.xlsx")

        for sheet in SHEET_NAMES:
            con.execute(
                "INSERT INTO sheets (patient_id, name, grid) VALUES (?, ?, ?)",
                (pid, sheet, json.dumps(grids.get(sheet, []), ensure_ascii=False)),
            )
            n_sheets += 1

        # files
        if folder:
            for src_dir in (os.path.join(legacy_dir, folder, "Files"), os.path.join(legacy_dir, folder)):
                if not os.path.isdir(src_dir):
                    continue
                for fn in sorted(os.listdir(src_dir)):
                    src = os.path.join(src_dir, fn)
                    if not os.path.isfile(src) or fn.lower() in SKIP_FILES:
                        continue
                    if src_dir.endswith(folder) and not fn.lower().endswith(
                        (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".pdf")
                    ):
                        continue  # only pull loose media from the patient folder root
                    ext = os.path.splitext(fn)[1].lower()
                    stored = f"{pid:04d}_{n_files:03d}{ext}"
                    pdir = os.path.join(FILES_DIR, str(pid))
                    os.makedirs(pdir, exist_ok=True)
                    shutil.copy2(src, os.path.join(pdir, stored))
                    size = os.path.getsize(src)
                    mime = mimetypes.guess_type(fn)[0] or "application/octet-stream"
                    con.execute(
                        "INSERT INTO files (patient_id, filename, mime, size, path) VALUES (?, ?, ?, ?, ?)",
                        (pid, fn, mime, size, os.path.join(str(pid), stored)),
                    )
                    n_files += 1
                break  # Files/ preferred; only fall back to folder root if no Files/

    for r in rows:
        name = r[0].strip()
        meta = {}
        for i, col in enumerate(header[1:], start=1):
            meta[col] = (r[i].strip() if i < len(r) else "")
        folder = resolve_folder(name, folders_lower)
        if folder:
            used_folders.add(folder)
            if folder.lower() != name.lower():
                warnings.append(f"CSV '{name}' matched folder '{folder}' (case/spelling differs)")
            if folder != name:
                name = folder  # trust the folder spelling (e.g. Micheal -> Michael)
        else:
            warnings.append(f"CSV '{name}': no matching patient folder - imported with empty data")
        import_patient(name, meta, folder)

    # ---- folders with no CSV row ----
    for folder in sorted(folders):
        if folder in used_folders:
            continue
        if not os.path.isfile(os.path.join(legacy_dir, folder, "information.xlsx")):
            continue
        warnings.append(f"Folder '{folder}' has no CSV row - imported with empty list details")
        import_patient(folder, {}, folder)

    con.commit()
    con.close()

    print(f"Imported {n_patients} patients, {n_sheets} sheets, {n_files} files.")
    if warnings:
        print("\nWarnings:")
        for w in warnings:
            print("  -", w)


if __name__ == "__main__":
    main()
