/* EPRSim shared front-end helpers: API client, mode detection, grid editor.
   Loaded by every page. Exposes window.EPR. */
(function () {
  "use strict";

  var ADMIN = location.pathname === "/admin" || location.pathname.indexOf("/admin/") === 0;
  var params = new URLSearchParams(location.search);

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  async function j(url, opts) {
    var r = await fetch(url, opts);
    if (!r.ok) {
      var msg = "HTTP " + r.status;
      try {
        var body = await r.json();
        if (body && body.error) msg = body.error;
      } catch (e) {}
      throw new Error(msg);
    }
    if (r.status === 204) return null;
    return r.json();
  }

  function editorName() {
    try { return localStorage.getItem("epr_editor") || ""; } catch (e) { return ""; }
  }
  function setEditorName(n) {
    try { localStorage.setItem("epr_editor", String(n || "").trim()); } catch (e) {}
  }
  function writeHeaders(extra) {
    var h = extra || {};
    h["X-Editor"] = editorName();
    return h;
  }

  function jsonReq(method, url, data) {
    return j(url, {
      method: method,
      headers: writeHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(data),
    });
  }

  var api = {
    patients: function () { return j("/api/patients"); },
    patient: function (id) { return j("/api/patients/" + id); },
    createPatient: function (d) { return jsonReq("POST", "/api/patients", d); },
    updatePatient: function (id, d) { return jsonReq("PUT", "/api/patients/" + id, d); },
    deletePatient: function (id) {
      return j("/api/patients/" + id, { method: "DELETE", headers: writeHeaders() });
    },
    sheet: function (id, name) { return j("/api/patients/" + id + "/sheets/" + name); },
    putSheet: function (id, name, grid) {
      return jsonReq("PUT", "/api/patients/" + id + "/sheets/" + name, { grid: grid });
    },
    files: function (id) { return j("/api/patients/" + id + "/files"); },
    uploadFile: function (id, file) {
      var fd = new FormData();
      fd.append("file", file);
      return j("/api/patients/" + id + "/files", { method: "POST", body: fd, headers: writeHeaders() });
    },
    deleteFile: function (fid) {
      return j("/api/files/" + fid, { method: "DELETE", headers: writeHeaders() });
    },
    fileUrl: function (fid) { return "/api/files/" + fid; },
    meta: function () { return j("/api/meta"); },
    changes: function (limit) { return j("/api/changes?limit=" + (limit || 400)); },
  };

  function base() { return ADMIN ? "/admin/" : "/"; }

  function linkTo(page, id) {
    return base() + page + "?id=" + encodeURIComponent(id);
  }

  function ensureEditorCss() {
    if (document.getElementById("epr-grid-css")) return;
    // The admin editor is mounted into the viewer tab page, whose own <style>
    // rules (large fonts, centred inputs, coloured focus, sticky headers) would
    // otherwise leak into the grid. Drop them; the editor ships its own CSS.
    var styles = document.querySelectorAll('style, link[rel="stylesheet"]');
    for (var i = 0; i < styles.length; i++) styles[i].parentNode.removeChild(styles[i]);
    if (document.documentElement) document.documentElement.style.cssText = "font-size:14px";
    if (document.body) document.body.style.cssText = "margin:0";

    var FONT = "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif";
    var st = document.createElement("style");
    st.id = "epr-grid-css";
    st.textContent =
      "*{box-sizing:border-box}" +
      "body{margin:0;" + FONT + ";font-size:14px;color:#1f2430}" +
      ".epr-grid-editor{" + FONT + ";font-size:14px;height:100vh;display:flex;flex-direction:column;background:#fff}" +
      ".epr-toolbar{display:flex;gap:8px;align-items:center;padding:8px 12px;border-bottom:1px solid #e0e0e0;background:#f8f9fa;flex-shrink:0;flex-wrap:wrap}" +
      ".epr-toolbar .epr-spacer{flex:1}" +
      ".epr-sheet-label{font-size:13px;font-weight:700;letter-spacing:.06em;color:#55607a;margin-right:4px}" +
      ".epr-toolbar button{font:inherit;font-size:13px;line-height:1.2;padding:7px 14px;border:1px solid #b9c6d6;background:#fff;border-radius:6px;cursor:pointer;color:#1f2430}" +
      ".epr-toolbar button:hover{background:#eef4fc}" +
      ".epr-toolbar button.epr-primary{background:#a9d1fc;border-color:#a9d1fc;font-weight:600}" +
      ".epr-status{font-size:12px;color:#666}" +
      ".epr-status.err{color:#c0392b}" +
      ".epr-grid-scroll{overflow:auto;flex:1}" +
      ".epr-grid{border-collapse:collapse;background:#fff;font-size:13px}" +
      ".epr-grid th,.epr-grid td{border:1px solid #dcdcdc;padding:0;min-width:120px;font-size:13px;font-weight:400;text-align:left}" +
      ".epr-grid th:first-child,.epr-grid td:first-child{position:sticky;left:0;background:#f0f0f0;z-index:1}" +
      ".epr-grid thead th{position:sticky;top:0;background:#eef1f4;z-index:2;font-weight:600;font-size:12px;padding:2px}" +
      ".epr-grid thead th:first-child{z-index:3}" +
      ".epr-grid thead th.locked{padding:6px 8px;color:#55607a}" +
      ".epr-grid input{border:0;width:100%;margin:0;padding:6px 8px;" + FONT + ";font-size:13px;line-height:1.3;text-align:left;color:#1f2430;background:transparent}" +
      ".epr-grid input:focus{outline:2px solid #4a90e2;outline-offset:-2px;background:#fff}" +
      ".epr-grid input[readonly]{background:#f0f0f0;color:#333;font-weight:600;cursor:default}" +
      ".epr-grid td.locked,.epr-grid th.locked{background:#f0f0f0}" +
      ".epr-grid tr.block-start td{border-top:2px solid #b9c6d6}" +
      ".epr-rowdel,.epr-coldel{font:inherit;border:0;background:transparent;color:#999;cursor:pointer;font-size:13px;padding:2px 6px}" +
      ".epr-rowdel:hover,.epr-coldel:hover{color:#c0392b}" +
      ".epr-corner{min-width:32px!important;text-align:center}";
    document.head.appendChild(st);
  }

  function saveBtnHandler(btn, patientId, sheetName, grid, setStatus) {
    btn.disabled = true;
    setStatus("Saving…");
    api.putSheet(patientId, sheetName, grid).then(
      function () { btn.disabled = false; setStatus("Saved"); },
      function (err) { btn.disabled = false; setStatus("Save failed: " + err.message, true); }
    );
  }

  /* ---------------------------------------------------------------- grid editor
     Mounts an Excel-like editable table for one sheet into `host`.
     Sticky toolbar (Save / + Row / + Column) with per row/column delete.
     opts.lockedRowLabels : column-0 values that lock that row's label and
                            prevent its deletion (matched via normLabel()).
     opts.fixedCols       : if set, no column add/delete and the grid is
                            clamped to this many columns. */
  function mountGridEditor(host, patientId, sheetName, opts) {
    opts = opts || {};
    var grid = [];
    var lockedLabels = (opts.lockedRowLabels || []).map(normLabel);
    var fixedCols = opts.fixedCols || 0;

    var wrap = document.createElement("div");
    wrap.className = "epr-grid-editor";
    wrap.innerHTML =
      '<div class="epr-toolbar">' +
      '  <strong class="epr-sheet-label"></strong>' +
      '  <button type="button" data-act="addrow">+ ' + (opts.addRowLabel || "Row") + "</button>" +
      (fixedCols ? "" : '  <button type="button" data-act="addcol">+ Column</button>') +
      '  <span class="epr-spacer"></span>' +
      '  <span class="epr-status" aria-live="polite"></span>' +
      '  <button type="button" class="epr-primary" data-act="save">Save</button>' +
      "</div>" +
      '<div class="epr-grid-scroll"><table class="epr-grid"><thead></thead><tbody></tbody></table></div>';
    host.appendChild(wrap);
    ensureEditorCss();

    wrap.querySelector(".epr-sheet-label").textContent = (opts.label || sheetName).toUpperCase();
    var thead = wrap.querySelector("thead");
    var tbody = wrap.querySelector("tbody");
    var status = wrap.querySelector(".epr-status");

    function cols() {
      if (fixedCols) return fixedCols;
      return grid.reduce(function (m, r) { return Math.max(m, r.length); }, 1);
    }
    function pad() {
      var c = cols();
      grid.forEach(function (r) {
        while (r.length < c) r.push("");
        if (fixedCols) r.length = c;
      });
      if (grid.length === 0) grid.push(new Array(c).fill(""));
    }
    function rowLocked(ri) { return lockedLabels.indexOf(normLabel(grid[ri][0])) !== -1; }

    function render() {
      pad();
      var c = cols();
      var htr = document.createElement("tr");
      var corner = document.createElement("th");
      corner.className = "epr-corner";
      htr.appendChild(corner);
      for (var ci = 0; ci < c; ci++) {
        var th = document.createElement("th");
        if (!fixedCols) {
          var del = document.createElement("button");
          del.className = "epr-coldel";
          del.type = "button";
          del.textContent = "✕ col " + (ci + 1);
          del.dataset.col = ci;
          th.appendChild(del);
        }
        htr.appendChild(th);
      }
      thead.innerHTML = "";
      thead.appendChild(htr);

      tbody.innerHTML = "";
      for (var ri = 0; ri < grid.length; ri++) {
        var locked = rowLocked(ri);
        var tr = document.createElement("tr");
        var rh = document.createElement("td");
        rh.className = "epr-corner";
        if (!locked) {
          var rdel = document.createElement("button");
          rdel.className = "epr-rowdel";
          rdel.type = "button";
          rdel.textContent = "✕";
          rdel.title = "Delete row " + (ri + 1);
          rdel.dataset.row = ri;
          rh.appendChild(rdel);
        }
        tr.appendChild(rh);
        for (var cj = 0; cj < c; cj++) {
          var td = document.createElement("td");
          var inp = document.createElement("input");
          inp.type = "text";
          inp.value = grid[ri][cj] != null ? grid[ri][cj] : "";
          if (cj === 0 && locked) { inp.readOnly = true; td.className = "locked"; }
          inp.dataset.row = ri;
          inp.dataset.col = cj;
          td.appendChild(inp);
          tr.appendChild(td);
        }
        tbody.appendChild(tr);
      }
    }

    function setStatus(msg, isErr) {
      status.textContent = msg || "";
      status.className = "epr-status" + (isErr ? " err" : "");
    }

    wrap.addEventListener("input", function (e) {
      var t = e.target;
      if (t.tagName === "INPUT" && t.dataset.row !== undefined) {
        grid[+t.dataset.row][+t.dataset.col] = t.value;
        setStatus("Unsaved changes");
      }
    });

    wrap.addEventListener("click", function (e) {
      var t = e.target;
      var act = t.dataset.act;
      if (act === "addrow") {
        grid.push(new Array(cols()).fill(""));
        render();
        setStatus("Unsaved changes");
      } else if (act === "addcol") {
        grid.forEach(function (r) { r.push(""); });
        render();
        setStatus("Unsaved changes");
      } else if (act === "save") {
        saveBtnHandler(t, patientId, sheetName, grid, setStatus);
      } else if (t.classList.contains("epr-rowdel")) {
        grid.splice(+t.dataset.row, 1);
        render();
        setStatus("Unsaved changes");
      } else if (t.classList.contains("epr-coldel")) {
        grid.forEach(function (r) { r.splice(+t.dataset.col, 1); });
        render();
        setStatus("Unsaved changes");
      }
    });

    setStatus("Loading…");
    api.sheet(patientId, sheetName).then(
      function (res) {
        grid = (res && res.grid) || [];
        if (!grid.length) grid = [new Array(cols()).fill("")];
        render();
        setStatus("");
      },
      function (err) { setStatus("Load failed: " + err.message, true); }
    );
  }

  function normLabel(s) {
    return String(s || "").trim().toLowerCase().replace(/respitory/g, "respiratory");
  }

  /* ------------------------------------------------------------ flowsheet editor
     Row 0 = header (blank corner + editable observation-time columns).
     Rows 1+ = observations. Standard observation labels are locked (read-only,
     not deletable); extra rows have an editable label and can be removed. */
  function mountFlowsheetEditor(host, patientId) {
    var grid = [];
    var standard = [];
    var wrap = document.createElement("div");
    wrap.className = "epr-grid-editor";
    wrap.innerHTML =
      '<div class="epr-toolbar">' +
      '  <strong class="epr-sheet-label">FLOWSHEETS</strong>' +
      '  <button type="button" data-act="addrow">+ Observation</button>' +
      '  <button type="button" data-act="addcol">+ Time column</button>' +
      '  <button type="button" data-act="delcol">- Time column</button>' +
      '  <span class="epr-spacer"></span>' +
      '  <span class="epr-status" aria-live="polite"></span>' +
      '  <button type="button" class="epr-primary" data-act="save">Save</button>' +
      "</div>" +
      '<div class="epr-grid-scroll"><table class="epr-grid"><thead></thead><tbody></tbody></table></div>';
    host.appendChild(wrap);
    ensureEditorCss();

    var thead = wrap.querySelector("thead");
    var tbody = wrap.querySelector("tbody");
    var status = wrap.querySelector(".epr-status");
    function setStatus(m, e) { status.textContent = m || ""; status.className = "epr-status" + (e ? " err" : ""); }
    function width() { return grid.reduce(function (m, r) { return Math.max(m, r.length); }, 2); }
    function pad() { var w = width(); grid.forEach(function (r) { while (r.length < w) r.push(""); }); }
    function isStandard(label) { return standard.indexOf(normLabel(label)) !== -1; }

    function render() {
      pad();
      var w = width();
      if (!grid.length) grid.push(new Array(w).fill(""));
      var htr = document.createElement("tr");
      htr.innerHTML = '<th class="epr-corner"></th>';
      for (var c = 0; c < w; c++) {
        var th = document.createElement("th");
        if (c === 0) {
          th.className = "locked";
          th.textContent = "Observation";
        } else {
          var hi = document.createElement("input");
          hi.type = "text";
          hi.placeholder = "time";
          hi.value = grid[0][c] || "";
          hi.dataset.row = 0;
          hi.dataset.col = c;
          th.appendChild(hi);
        }
        htr.appendChild(th);
      }
      thead.innerHTML = "";
      thead.appendChild(htr);

      tbody.innerHTML = "";
      for (var ri = 1; ri < grid.length; ri++) {
        var locked = isStandard(grid[ri][0]);
        var tr = document.createElement("tr");
        var rh = document.createElement("td");
        rh.className = "epr-corner";
        if (!locked) {
          var del = document.createElement("button");
          del.className = "epr-rowdel";
          del.type = "button";
          del.textContent = "✕";
          del.title = "Remove this observation";
          del.dataset.delrow = ri;
          rh.appendChild(del);
        }
        tr.appendChild(rh);
        for (var cj = 0; cj < w; cj++) {
          var td = document.createElement("td");
          if (cj === 0 && locked) td.className = "locked";
          var inp = document.createElement("input");
          inp.type = "text";
          inp.value = grid[ri][cj] != null ? grid[ri][cj] : "";
          if (cj === 0 && locked) inp.readOnly = true;
          if (cj === 0 && !locked) inp.placeholder = "Observation name";
          inp.dataset.row = ri;
          inp.dataset.col = cj;
          td.appendChild(inp);
          tr.appendChild(td);
        }
        tbody.appendChild(tr);
      }
    }

    wrap.addEventListener("input", function (e) {
      if (e.target.tagName === "INPUT" && e.target.dataset.row !== undefined) {
        grid[+e.target.dataset.row][+e.target.dataset.col] = e.target.value;
        setStatus("Unsaved changes");
      }
    });
    wrap.addEventListener("click", function (e) {
      var act = e.target.dataset.act;
      if (act === "addrow") {
        grid.push(new Array(width()).fill(""));
        render();
        setStatus("Unsaved changes");
      } else if (act === "addcol") {
        grid.forEach(function (r) { r.push(""); });
        render();
        setStatus("Unsaved changes");
      } else if (act === "delcol") {
        if (width() <= 2) return;
        var last = width() - 1;
        var hasData = grid.some(function (r, i) { return i > 0 && String(r[last] || "").trim(); });
        if (hasData && !confirm("The last time column has values. Remove it?")) return;
        grid.forEach(function (r) { r.splice(last, 1); });
        render();
        setStatus("Unsaved changes");
      } else if (act === "save") {
        saveBtnHandler(e.target, patientId, "flowsheets", grid, setStatus);
      } else if (e.target.dataset.delrow !== undefined) {
        grid.splice(+e.target.dataset.delrow, 1);
        render();
        setStatus("Unsaved changes");
      }
    });

    setStatus("Loading…");
    Promise.all([api.meta(), api.sheet(patientId, "flowsheets")]).then(function (r) {
      standard = (r[0].flowsheet_standard_rows || []).map(normLabel);
      grid = (r[1] && r[1].grid) || [];
      if (!grid.length) grid = [["", "", "", ""]];
      render();
      setStatus("");
    }, function (err) { setStatus("Load failed: " + err.message, true); });
  }

  /* ------------------------------------------------------------------ MAR editor
     Row 0 = header: 3 locked cells + editable day columns.
     Body = repeating 7-row drug blocks; the label column is fixed. Whole
     blocks are added / removed, never individual rows. */
  function mountMarEditor(host, patientId) {
    var grid = [];
    var blockLabels = ["Drug", "Start Date", "Duration", "Prescriber", "Dose", "Route", "Freq"];
    var LOCK = 3; // first three columns of the header are blank/locked
    var wrap = document.createElement("div");
    wrap.className = "epr-grid-editor";
    wrap.innerHTML =
      '<div class="epr-toolbar">' +
      '  <strong class="epr-sheet-label">MAR</strong>' +
      '  <button type="button" data-act="adddrug">+ Add drug</button>' +
      '  <button type="button" data-act="addcol">+ Day column</button>' +
      '  <button type="button" data-act="delcol">- Day column</button>' +
      '  <span class="epr-spacer"></span>' +
      '  <span class="epr-status" aria-live="polite"></span>' +
      '  <button type="button" class="epr-primary" data-act="save">Save</button>' +
      "</div>" +
      '<div class="epr-grid-scroll"><table class="epr-grid"><thead></thead><tbody></tbody></table></div>';
    host.appendChild(wrap);
    ensureEditorCss();

    var thead = wrap.querySelector("thead");
    var tbody = wrap.querySelector("tbody");
    var status = wrap.querySelector(".epr-status");
    function setStatus(m, e) { status.textContent = m || ""; status.className = "epr-status" + (e ? " err" : ""); }
    function width() { return Math.max(grid.reduce(function (m, r) { return Math.max(m, r.length); }, LOCK + 1), LOCK + 1); }
    function pad() {
      var w = width();
      grid.forEach(function (r) { while (r.length < w) r.push(""); });
    }
    function normaliseBlocks() {
      // ensure body row count is a multiple of 7 and labels are correct
      var body = grid.slice(1);
      while (body.length % 7 !== 0) body.push(new Array(width()).fill(""));
      for (var i = 0; i < body.length; i++) body[i][0] = blockLabels[i % 7];
      grid = [grid[0] || new Array(width()).fill("")].concat(body);
    }

    function render() {
      pad();
      normaliseBlocks();
      var w = width();
      var htr = document.createElement("tr");
      htr.innerHTML = '<th class="epr-corner"></th>';
      for (var c = 0; c < w; c++) {
        var th = document.createElement("th");
        if (c < LOCK) {
          th.className = "locked";
        } else {
          var hi = document.createElement("input");
          hi.type = "text";
          hi.placeholder = "day";
          hi.value = grid[0][c] || "";
          hi.dataset.row = 0;
          hi.dataset.col = c;
          th.appendChild(hi);
        }
        htr.appendChild(th);
      }
      thead.innerHTML = "";
      thead.appendChild(htr);

      tbody.innerHTML = "";
      var blocks = (grid.length - 1) / 7;
      for (var ri = 1; ri < grid.length; ri++) {
        var inBlock = (ri - 1) % 7;
        var tr = document.createElement("tr");
        if (inBlock === 0) tr.className = "block-start";
        var rh = document.createElement("td");
        rh.className = "epr-corner";
        if (inBlock === 0 && blocks > 1) {
          var del = document.createElement("button");
          del.className = "epr-rowdel";
          del.type = "button";
          del.textContent = "✕";
          del.title = "Remove this drug";
          del.dataset.delblock = (ri - 1) / 7;
          rh.appendChild(del);
        }
        tr.appendChild(rh);
        for (var cj = 0; cj < w; cj++) {
          var td = document.createElement("td");
          var inp = document.createElement("input");
          inp.type = "text";
          inp.value = grid[ri][cj] != null ? grid[ri][cj] : "";
          if (cj === 0) { inp.readOnly = true; td.className = "locked"; }
          inp.dataset.row = ri;
          inp.dataset.col = cj;
          td.appendChild(inp);
          tr.appendChild(td);
        }
        tbody.appendChild(tr);
      }
    }

    wrap.addEventListener("input", function (e) {
      if (e.target.tagName === "INPUT" && e.target.dataset.row !== undefined) {
        grid[+e.target.dataset.row][+e.target.dataset.col] = e.target.value;
        setStatus("Unsaved changes");
      }
    });
    wrap.addEventListener("click", function (e) {
      var act = e.target.dataset.act;
      if (act === "adddrug") {
        for (var k = 0; k < 7; k++) grid.push(new Array(width()).fill(""));
        render();
        setStatus("Unsaved changes");
      } else if (act === "addcol") {
        grid.forEach(function (r) { r.push(""); });
        render();
        setStatus("Unsaved changes");
      } else if (act === "delcol") {
        if (width() <= LOCK + 1) return;
        var last = width() - 1;
        var hasData = grid.some(function (r, i) { return i > 0 && String(r[last] || "").trim(); });
        if (hasData && !confirm("The last day column has values. Remove it?")) return;
        grid.forEach(function (r) { r.splice(last, 1); });
        render();
        setStatus("Unsaved changes");
      } else if (act === "save") {
        saveBtnHandler(e.target, patientId, "mar", grid, setStatus);
      } else if (e.target.dataset.delblock !== undefined) {
        if (!confirm("Remove this drug and its rows?")) return;
        var b = +e.target.dataset.delblock;
        grid.splice(1 + b * 7, 7);
        render();
        setStatus("Unsaved changes");
      }
    });

    setStatus("Loading…");
    Promise.all([api.meta(), api.sheet(patientId, "mar")]).then(function (r) {
      if (r[0].mar_block_labels && r[0].mar_block_labels.length === 7) blockLabels = r[0].mar_block_labels;
      grid = (r[1] && r[1].grid) || [];
      if (!grid.length) grid = [["", "", ""]];
      render();
      setStatus("");
    }, function (err) { setStatus("Load failed: " + err.message, true); });
  }

  /* --------------------------------------------------------- editor-name gate
     On a top-level admin page, block the UI until a name is entered. Iframed
     tab pages (inside admin/folder.html) inherit the name and are skipped. */
  function requireEditor() {
    if (!ADMIN) return;
    try { if (window.top !== window.self) return; } catch (e) { return; }

    function show() {
      if (editorName() || document.getElementById("epr-editor-gate")) return;
      var ov = document.createElement("div");
      ov.id = "epr-editor-gate";
      ov.setAttribute(
        "style",
        "position:fixed;inset:0;z-index:2147483647;background:rgba(15,20,30,.6);" +
          "display:flex;align-items:center;justify-content:center;padding:20px;" +
          "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif"
      );
      ov.innerHTML =
        '<div style="background:#fff;border-radius:12px;padding:26px 26px 22px;width:410px;' +
        'max-width:92vw;box-shadow:0 24px 60px rgba(0,0,0,.32)">' +
        '<h2 style="margin:0 0 6px;font-size:19px;color:#1f2430">Enter your name</h2>' +
        '<p style="margin:0 0 16px;font-size:14px;line-height:1.5;color:#555">' +
        "Changes made in the admin interface are recorded against whoever made " +
        "them. Enter your name to continue.</p>" +
        '<input id="epr-editor-gate-input" type="text" placeholder="e.g. A. Smith" ' +
        'autocomplete="name" style="width:100%;padding:10px;border:1px solid #ccc;' +
        'border-radius:7px;font-size:15px;box-sizing:border-box">' +
        '<div id="epr-editor-gate-err" style="color:#c0392b;font-size:13px;' +
        'min-height:18px;margin:6px 0 4px"></div>' +
        '<button id="epr-editor-gate-go" style="width:100%;padding:11px;border:0;' +
        'border-radius:7px;background:#a9d1fc;font-weight:600;font-size:15px;cursor:pointer">' +
        "Continue</button></div>";
      document.body.appendChild(ov);
      var input = ov.querySelector("#epr-editor-gate-input");
      var errEl = ov.querySelector("#epr-editor-gate-err");
      input.focus();
      function submit() {
        var v = input.value.trim().replace(/\s+/g, " ");
        if (v.length < 2) {
          errEl.textContent = "Please enter your name to continue.";
          input.focus();
          return;
        }
        setEditorName(v);
        ov.parentNode.removeChild(ov);
        try {
          document.dispatchEvent(new CustomEvent("epr:editor-set", { detail: v }));
        } catch (e) {}
      }
      ov.querySelector("#epr-editor-gate-go").addEventListener("click", submit);
      input.addEventListener("keydown", function (e) {
        if (e.key === "Enter") submit();
      });
    }

    if (document.body) show();
    else document.addEventListener("DOMContentLoaded", show);
  }

  window.EPR = {
    ADMIN: ADMIN,
    api: api,
    patientId: params.get("id"),
    query: params,
    base: base,
    linkTo: linkTo,
    escapeHtml: escapeHtml,
    editor: editorName,
    setEditor: setEditorName,
    requireEditor: requireEditor,
    mountGridEditor: mountGridEditor,
    mountFlowsheetEditor: mountFlowsheetEditor,
    mountMarEditor: mountMarEditor,
  };

  requireEditor();
})();
