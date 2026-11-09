/* GC UI kit 1.0.0 — shared behaviour for GC tool pages (UI_STANDARD §3–§10). No framework.
   A tool page describes its sections; the kit builds the shell (header, index, status bar), polls /api/state, and
   provides the standard components: cards, pills, tables, log viewer, command cards, action buttons, the workbook
   editor (D-UI6), LenAI prompt cards (D-UI4), file lists, banners, dialogs, toasts, search (Ctrl+K), the help panel,
   glossary and A–Z index, the theme toggle (D-UI3) and the offline fallback. */
window.GC = (function () {
  "use strict";
  const KIT = "1.1.1";
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const el = (tag, attrs, ...children) => {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") e.className = v;
      else if (k === "html") e.innerHTML = v;
      else if (k === "text") e.textContent = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else if (k === "dataset") Object.assign(e.dataset, v);
      else e.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat(Infinity)) if (c !== null && c !== undefined && c !== false) e.append(c.nodeType ? c : document.createTextNode(String(c)));
    return e;
  };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (m) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]));

  // ---- storage (per-browser conveniences only; UI_STANDARD §12)
  const store = {
    get(k, d) { try { const v = localStorage.getItem("gc." + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem("gc." + k, JSON.stringify(v)); } catch (e) { /* storage unavailable */ } },
  };

  // ---- API
  let offline = false;
  async function api(path, body, opts) {
    const r = await fetch(path, body !== undefined ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
    const j = await r.json().catch(() => ({ error: "bad reply" }));
    if (!r.ok || j.error) { const e = new Error(j.error || r.statusText); e.status = r.status; throw e; }
    return j;
  }

  // ---- toast and live region
  let toastT;
  function toast(msg, ms) {
    let t = $(".toast") || document.body.appendChild(el("div", { class: "toast", role: "status" }));
    t.textContent = msg; t.classList.add("show");
    clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove("show"), ms || 3000);
    announce(msg);
  }
  function announce(msg) {
    let live = $("#gc-live") || document.body.appendChild(el("div", { id: "gc-live", class: "sr-only", "aria-live": "polite" }));
    live.textContent = ""; setTimeout(() => (live.textContent = msg), 30);
  }

  // ---- status vocabulary (UI_STANDARD §6)
  const STATUS = {
    none: { label: "Not run", ic: "○" }, running: { label: "Running", ic: "◐" }, ok: { label: "OK", ic: "✓" }, info: { label: "Info", ic: "i" },
    flag: { label: "Flag", ic: "!" }, stop: { label: "STOP", ic: "✕" }, stale: { label: "Stale", ic: "↻" },
  };
  function pill(status, text) {
    const s = STATUS[status] ? status : "none";
    return el("span", { class: "pill " + s, role: "status" }, el("span", { class: "ic", "aria-hidden": "true" }, STATUS[s].ic), text || STATUS[s].label);
  }
  const worst = (list) => ["stop", "running", "flag", "stale", "info", "ok", "none"].find((s) => list.includes(s)) || "none";

  // ---- numbers, times
  const isNum = (s) => s !== "" && s !== null && s !== undefined && typeof s !== "boolean" && !isNaN(s) && isFinite(s);
  function fmtNum(s) {
    const n = Number(s);
    if (Number.isInteger(n) && Math.abs(n) < 1e15) return Math.abs(n) < 10000 ? String(n) : n.toLocaleString("en-GB");
    const a = Math.abs(n);
    if (a >= 1000) return n.toLocaleString("en-GB", { maximumFractionDigits: 0 });
    if (a >= 1) return n.toLocaleString("en-GB", { maximumFractionDigits: 4 });
    return n.toLocaleString("en-GB", { maximumSignificantDigits: 4 });
  }
  const fmtWhen = (s) => (s ? s.slice(0, 16) : "—");
  function ago(iso) {
    if (!iso) return "";
    const d = (Date.now() - new Date(iso.replace(" ", "T")).getTime()) / 1000;
    if (!isFinite(d)) return "";
    if (d < 90) return "just now"; if (d < 3600) return Math.round(d / 60) + " min ago"; if (d < 86400) return Math.round(d / 3600) + " h ago";
    return Math.round(d / 86400) + " d ago";
  }
  const fmtDur = (a, b) => { const s = Math.max(0, Math.round((new Date((b || nowIso()).replace(" ", "T")) - new Date(a.replace(" ", "T"))) / 1000)); return s < 60 ? s + " s" : Math.floor(s / 60) + " min " + (s % 60) + " s"; };
  const nowIso = () => new Date().toISOString().slice(0, 19).replace("T", " ");

  // ---- table (sortable, numbers right-aligned, severity rows)
  function table(columns, rows, opts) {
    opts = opts || {};
    const textCols = new Set(opts.textCols || []);
    let sortIx = -1, dir = 1;
    const wrap = el("div", { class: "tbl-wrap" });
    const render = () => {
      const data = rows.slice();
      if (sortIx >= 0) data.sort((a, b) => { const x = a[sortIx], y = b[sortIx]; const nx = isNum(x) && isNum(y); return (nx ? Number(x) - Number(y) : String(x ?? "").localeCompare(String(y ?? ""))) * dir; });
      const thead = el("thead", {}, el("tr", {}, columns.map((c, i) => el("th", { scope: "col", onclick: () => { dir = sortIx === i ? -dir : 1; sortIx = i; render(); }, title: "Sort" }, c, sortIx === i ? el("span", { class: "sort" }, dir > 0 ? "▲" : "▼") : null))));
      const sevIx = columns.findIndex((c) => /^(severity|level)$/i.test(c));
      const tbody = el("tbody", {}, data.map((r) => el("tr", { class: sevIx >= 0 ? "sev-" + String(r[sevIx]).toUpperCase() : null },
        r.map((v, i) => { const num = isNum(v) && !textCols.has(columns[i]); return el("td", { class: num ? "num" : null, title: num ? null : String(v ?? "") }, num ? fmtNum(v) : (v ?? "")); }))));
      wrap.replaceChildren(el("table", { class: "gc" }, thead, tbody));
    };
    render();
    return wrap;
  }
  function tableHead(title, n, extra) { return el("div", { class: "tbl-head" }, el("b", {}, title), n !== undefined ? el("span", { class: "n" }, n + " rows") : null, extra); }

  // ---- cards (UI_STANDARD §4)
  function card(opts) {
    // opts: id, num, title, sub, status, open, always, body
    const titleEl = el("h3", {}, opts.title);
    const headInner = [opts.num !== undefined ? el("span", { class: "num" }, opts.num) : null, titleEl, opts.sub ? el("span", { class: "sub" }, opts.sub) : null];
    const pillbox = el("span", { class: "pillbox" });
    const c = el("section", { class: "gc-card " + (opts.always ? "always " : "") + (opts.open ? "open " : "") + "s-" + (opts.status || "none"), id: opts.id, "aria-labelledby": opts.id ? opts.id + "-t" : null });
    titleEl.id = opts.id ? opts.id + "-t" : null;
    let head;
    if (opts.always) head = el("div", { class: "head" }, headInner, el("span", { class: "spacer" }), pillbox);
    else {
      const tog = el("button", { class: "toggle", type: "button", "aria-expanded": String(!!opts.open), onclick: () => { c.classList.toggle("open"); tog.setAttribute("aria-expanded", String(c.classList.contains("open"))); store.set("card." + opts.id, c.classList.contains("open")); } }, headInner, el("span", { class: "spacer" }), el("span", { class: "chev", "aria-hidden": "true" }, "›"));
      head = el("div", { class: "head" }, tog, pillbox);
      if (opts.id && store.get("card." + opts.id) === true) { c.classList.add("open"); tog.setAttribute("aria-expanded", "true"); }
    }
    const body = el("div", { class: "body" }, opts.body || []);
    c.append(head, body);
    c.setStatus = (s, text) => { c.className = c.className.replace(/\bs-\w+/g, "").trim() + " s-" + (s || "none"); pillbox.replaceChildren(pill(s, text)); if ((s === "stop" || s === "flag") && !opts.always) c.classList.add("open"); };
    c.setBody = (...nodes) => body.replaceChildren(...nodes.flat(Infinity).filter(Boolean));
    c.body = body; c.head = head; c.open = () => c.classList.add("open");
    if (opts.status) c.setStatus(opts.status);
    return c;
  }
  const part = (title, ...nodes) => el("div", { class: "part" }, el("div", { class: "ttl" }, title), nodes);
  const note = (text) => el("p", { class: "note" }, text);
  function banner(kind, text, extra) { const ic = { stop: "✕", flag: "!", info: "i", ok: "✓" }[kind] || "i"; return el("div", { class: "banner " + kind, role: kind === "stop" ? "alert" : "status" }, el("span", { class: "ic", "aria-hidden": "true" }, ic), el("div", {}, text, extra)); }

  // ---- buttons, commands, runs
  let state = null;                  // the last /api/state
  const runListeners = new Set();
  const running = () => !!(state && state.run && state.run.running);
  function copyBtn(text, label) {
    return el("button", { class: "btn sm", type: "button", onclick: async () => { try { await navigator.clipboard.writeText(typeof text === "function" ? text() : text); toast("Copied"); } catch (e) { toast("Copy failed: select the text and copy it"); } } }, label || "Copy");
  }
  function cmdCard(command, opts) {
    opts = opts || {};
    const code = el("code", {}, command);
    const btns = el("div", { class: "gc-row" }, copyBtn(command, "Copy command"), opts.run ? opts.run : null);
    return el("div", { class: "cmd" }, code, btns);
  }
  function runButton(actionId, opts) {
    // opts: label, args (object | fn), confirm {title, body}, primary, onStarted
    opts = opts || {};
    const b = el("button", { class: "btn " + (opts.primary ? "primary" : ""), type: "button" }, opts.label || "Run");
    const refresh = () => { b.disabled = offline || running(); b.title = offline ? "Server not running" : running() ? "Another run is in progress" : ""; };
    runListeners.add(refresh); refresh();
    b.addEventListener("click", async () => {
      const args = typeof opts.args === "function" ? opts.args() : (opts.args || {});
      if (args === null) return;
      if (opts.confirm) { const ok = await confirm(opts.confirm); if (!ok) return; }
      try {
        const r = await api("/api/run", { action: actionId, args });
        toast((opts.label || actionId) + " started");
        if (opts.onStarted) opts.onStarted(r.run);
        poller && poller.tick();
      } catch (e) { toast(e.message); }
    });
    return b;
  }
  function stopButton() {
    const b = el("button", { class: "btn danger sm", type: "button", onclick: async () => { if (await confirm({ title: "Stop the run?", body: "The launcher is terminated; files it was writing may be incomplete. You can run it again." })) { try { await api("/api/stop", {}); toast("Stop requested"); } catch (e) { toast(e.message); } } } }, "Stop");
    const refresh = () => { b.style.display = running() ? "" : "none"; };
    runListeners.add(refresh); refresh();
    return b;
  }
  function openBtn(path, label, opts) {
    opts = opts || {};
    return el("button", { class: "btn sm", type: "button", onclick: async () => { try { await api("/api/open", opts.what ? { what: opts.what, id: opts.id } : { path }); toast("Opened " + (label || path)); } catch (e) { toast(e.message); } } }, label || "Open");
  }

  // ---- log viewer
  function logView(opts) {
    opts = opts || {};
    const pre = el("div", { class: "log", role: "log", "aria-live": "off", tabindex: "0" }, "(no run yet)");
    let auto = true, lastRun = null;
    const autoBtn = el("button", { class: "btn sm", type: "button", "aria-pressed": "true", onclick: () => { auto = !auto; autoBtn.setAttribute("aria-pressed", String(auto)); autoBtn.textContent = auto ? "Auto-scroll on" : "Auto-scroll off"; } }, "Auto-scroll on");
    const bar = el("div", { class: "gc-row" }, autoBtn, copyBtn(() => pre.textContent, "Copy log"), el("span", { class: "muted small", id: "log-meta" }));
    const box = el("div", {}, bar, pre);
    box.load = async (runId) => {
      try {
        const j = await api("/api/log" + (runId ? "?run=" + encodeURIComponent(runId) : ""));
        lastRun = j.run;
        pre.replaceChildren(...j.lines.map((l) => el("div", { class: /STOP|ERROR|STOPPED|Traceback|exit code [1-9]/.test(l) ? "bad" : /exit code 0|DONE|OK\b|PASS/.test(l) ? "ok" : /^\[/.test(l) ? "dim" : null }, l)));
        if (auto) pre.scrollTop = pre.scrollHeight;
        const m = $("#log-meta", box);
        if (j.run) m.textContent = (j.run.title || j.run.action) + " · " + (j.run.running ? "running " + fmtDur(j.run.started) : "finished " + fmtWhen(j.run.finished) + " · exit " + j.run.rc + " · " + fmtDur(j.run.started, j.run.finished));
      } catch (e) { /* keep the last tail */ }
    };
    return box;
  }

  // ---- file lists, tiles
  function fileList(files) {
    // files: [{file|name, path, exists, mtime, size, open:bool}]
    return el("ul", { class: "files" }, files.map((f) => el("li", {},
      el("span", { class: f.exists ? "ok" : "miss", "aria-label": f.exists ? "present" : "missing" }, f.exists ? "✓" : "✗"),
      el("span", { class: "path", title: f.path }, f.name || f.file || f.path),
      f.exists && f.mtime ? el("span", { class: "when" }, fmtWhen(f.mtime)) : f.exists ? null : el("span", { class: "when" }, "missing"),
      f.exists && f.open !== false ? openBtn(f.path, "Open") : null)));
  }

  // ---- dialogs (never window.alert / confirm)
  function confirm(opts) {
    return new Promise((resolve) => {
      const d = el("dialog", { class: "gc" });
      const cancel = el("button", { class: "btn", type: "button", onclick: () => { d.close(); resolve(false); } }, opts.cancel || "Cancel");
      const ok = el("button", { class: "btn " + (opts.danger ? "danger" : "primary"), type: "button", onclick: () => { d.close(); resolve(true); } }, opts.ok || "Continue");
      d.append(el("div", { class: "hd" }, opts.title || "Please confirm"), el("div", { class: "bd" }, typeof opts.body === "string" ? el("p", { style: "margin:0" }, opts.body) : opts.body), el("div", { class: "ft" }, cancel, ok));
      d.addEventListener("close", () => { d.remove(); resolve(false); });
      document.body.append(d); d.showModal(); cancel.focus();
    });
  }
  function prompt(opts) {
    // opts: title, body, fields:[{name,label,help,required,options,value,placeholder}] -> Promise<object|null>
    return new Promise((resolve) => {
      const d = el("dialog", { class: "gc" });
      const inputs = {};
      const form = el("div", { style: "display:grid;gap:10px" }, (opts.fields || []).map((f) => {
        const inp = f.options ? el("select", { id: "f-" + f.name }, [el("option", { value: "" }, f.required ? "— choose —" : "— none —"), ...f.options.map((o) => el("option", { value: typeof o === "string" ? o : o.value, selected: (typeof o === "string" ? o : o.value) === f.value }, typeof o === "string" ? o : o.label))])
          : el("input", { type: "text", id: "f-" + f.name, value: f.value || "", placeholder: f.placeholder || "" });
        inputs[f.name] = inp;
        return el("label", { class: "fld " + (f.required ? "req" : "opt"), for: "f-" + f.name }, el("span", { class: "lb" }, f.label), inp, f.help ? el("span", { class: "help" }, f.help) : null);
      }));
      const err = el("div", { class: "field-err" });
      const cancel = el("button", { class: "btn", type: "button", onclick: () => { d.close(); } }, "Cancel");
      const ok = el("button", { class: "btn primary", type: "button", onclick: () => {
        const out = {}; const missing = [];
        for (const f of opts.fields || []) { const v = inputs[f.name].value.trim(); if (f.required && !v) missing.push(f.label); if (v) out[f.name] = v; }
        if (missing.length) { err.textContent = "Required: " + missing.join(", "); return; }
        d.removeEventListener("close", onClose); d.close(); d.remove(); resolve(out);
      } }, opts.ok || "Run");
      const onClose = () => { d.remove(); resolve(null); };
      d.append(el("div", { class: "hd" }, opts.title), el("div", { class: "bd" }, opts.body ? el("p", { style: "margin:0" }, opts.body) : null, form, err), el("div", { class: "ft" }, cancel, ok));
      d.addEventListener("close", onClose);
      document.body.append(d); d.showModal();
      const first = Object.values(inputs)[0]; if (first) first.focus(); else ok.focus();
    });
  }

  // ---- workbook editor (D-UI6)
  function editor(wid, opts) {
    opts = opts || {};
    const box = el("div", { class: "editor" });
    const tabs = el("div", { class: "tabs", role: "tablist" });
    const bar = el("div", { class: "bar" });
    const grid = el("div", {});
    const msg = el("div", {});
    let info = null, cur = null, sheetData = null, edits = new Map(), appends = [];
    const dirtyLbl = el("span", { class: "dirty" });
    const q = el("input", { type: "search", placeholder: "Filter rows", "aria-label": "Filter rows", oninput: () => { clearTimeout(q._t); q._t = setTimeout(() => loadSheet(cur, 0), 300); } });
    const saveBtn = el("button", { class: "btn primary sm", type: "button", onclick: save }, "Save");
    const discardBtn = el("button", { class: "btn sm", type: "button", onclick: () => { edits.clear(); appends = []; loadSheet(cur, sheetData ? sheetData.offset : 0); } }, "Discard");
    const addBtn = el("button", { class: "btn sm", type: "button", onclick: () => { appends.push(new Array(sheetData.columns.length).fill("")); render(); } }, "Add row");
    const excelBtn = openBtn(null, "Open in Excel", { what: "workbook", id: wid });
    const reloadBtn = el("button", { class: "btn sm", type: "button", onclick: () => load() }, "Reload");
    const lockBanner = el("div", {});
    const legend = el("div", { class: "legend" }, el("span", {}, el("span", { class: "sw input" }), "required input"), el("span", {}, el("span", { class: "sw optional" }), "optional input"), el("span", {}, el("span", { class: "sw check" }), "check / computed (read-only)"), el("span", {}, el("span", { class: "sw changed" }), "changed, not saved"));
    const pager = el("div", { class: "gc-row small muted" });
    bar.append(q, saveBtn, discardBtn, addBtn, excelBtn, reloadBtn, dirtyLbl);
    box.append(lockBanner, tabs, bar, msg, grid, pager, legend);
    const dirty = () => edits.size + appends.length;
    const refreshDirty = () => { dirtyLbl.textContent = dirty() ? dirty() + " unsaved change" + (dirty() > 1 ? "s" : "") : ""; saveBtn.disabled = !dirty() || (sheetData && sheetData.readonly); discardBtn.disabled = !dirty(); };
    async function load() {
      try { info = await api("/api/workbook?id=" + encodeURIComponent(wid)); }
      catch (e) { box.replaceChildren(banner("stop", e.message)); return; }
      if (!info.exists) { box.replaceChildren(banner("flag", info.path + " does not exist yet.")); return; }
      tabs.replaceChildren(...info.sheets.map((s) => el("button", { type: "button", role: "tab", class: (s.name === cur ? "on " : "") + (s.readonly ? "ro" : ""), "aria-selected": String(s.name === cur), onclick: () => { if (dirty() && s.name !== cur) { confirm({ title: "Unsaved changes", body: "Switching sheets discards " + dirty() + " unsaved change(s). Save first?", ok: "Discard and switch" }).then((ok) => { if (ok) { edits.clear(); appends = []; loadSheet(s.name, 0); } }); } else loadSheet(s.name, 0); }, title: s.note || "" }, s.name, el("span", { class: "n" }, s.rows))));
      lockBanner.replaceChildren(info.in_use ? banner("flag", info.path.split("/").pop() + " is open in Excel: close it there before saving from the page.") : el("span"));
      if (!cur) cur = (info.sheets.find((s) => !s.readonly) || info.sheets[0] || {}).name;
      if (cur) await loadSheet(cur, 0);
    }
    async function loadSheet(name, offset) {
      cur = name; $$("button", tabs).forEach((b) => { const on = b.firstChild.textContent === name; b.classList.toggle("on", on); b.setAttribute("aria-selected", String(on)); });
      grid.replaceChildren(el("p", { class: "muted" }, "Loading " + name + " …"));
      try { sheetData = await api("/api/sheet?id=" + encodeURIComponent(wid) + "&sheet=" + encodeURIComponent(name) + "&offset=" + (offset || 0) + "&limit=" + (opts.limit || 200) + "&q=" + encodeURIComponent(q.value || "")); }
      catch (e) { grid.replaceChildren(banner("stop", e.message)); return; }
      if (!(offset > 0)) { /* keep edits across pages */ }
      render();
    }
    function render() {
      const d = sheetData; if (!d) return;
      const cols = d.columns;
      msg.replaceChildren(d.readonly ? banner("info", "This sheet is read-only in the page (reference data). Open the workbook in Excel to change it.") : d.note ? note(d.note) : el("span"));
      addBtn.disabled = d.readonly; q.disabled = false;
      const colTip = (c) => [c.description || c.note || "", c.required ? "Required." : c.kind === "optional" ? "Optional." : "", c.options && c.options.length ? "Options: " + c.options.join(", ") : "", c.example ? "Example: " + c.example : ""].filter(Boolean).join("\n");
      const th = el("tr", {}, el("th", { scope: "col" }, "#"), cols.map((c) => el("th", { scope: "col", title: colTip(c) }, c.name, c.kind !== "text" ? el("span", { class: "kind" }, c.kind === "input" ? (c.required === false ? "input" : "input") : c.kind === "optional" ? "optional" : "check") : null, colTip(c) ? el("button", { class: "q", type: "button", title: colTip(c), "aria-label": "Help: " + c.name, onclick: () => panel.open(c.name, el("p", { style: "white-space:pre-wrap" }, colTip(c))) }, "?") : null)));
      const RH = d.rows_help || null, keyIx = RH ? cols.findIndex((c) => c.col === d.key_col) : -1, valIx = RH ? cols.findIndex((c) => c.col === d.value_col) : -1;
      const cell = (r, ci, c, v, isNew, rowVals) => {
        const key = r + ":" + c.col;
        const cur = edits.has(key) ? edits.get(key) : v;
        const rh = RH && ci === valIx && keyIx >= 0 ? RH[String(rowVals[keyIx] ?? "")] : null;
        const options = rh && rh.options && rh.options.length ? rh.options : c.options;
        if (c.locked || d.readonly) return el("td", { class: "cell " + (c.kind === "check" ? "check" : ""), title: String(v ?? "") }, isNum(v) ? fmtNum(v) : (v ?? ""));
        const onchange = (ev) => { const nv = ev.target.value; if (isNew) { appends[-r - 1][ci] = nv; } else if (String(nv) === String(v ?? "")) edits.delete(key); else edits.set(key, nv); ev.target.closest("td").classList.toggle("changed", isNew ? nv !== "" : edits.has(key)); if (rh) ev.target.closest("tr").classList.toggle("need", !!rh.required && nv === ""); refreshDirty(); };
        const inp = options.length ? el("select", { "aria-label": c.name + " row " + r, onchange }, [el("option", { value: "" }, "—"), ...options.map((o) => el("option", { value: o, selected: String(o) === String(cur ?? "") }, o)), ...(cur !== null && cur !== "" && !options.map(String).includes(String(cur)) ? [el("option", { value: String(cur), selected: true }, String(cur) + " (not in list)")] : [])])
          : el("input", { type: "text", value: cur ?? "", "aria-label": c.name + " row " + r, onchange, placeholder: rh && rh.example ? "e.g. " + rh.example : c.example ? "e.g. " + c.example : "" });
        const tipTxt = rh ? [rh.description || "", rh.required ? "Required." : "Optional.", rh.options && rh.options.length ? "Options: " + rh.options.join(", ") : "", rh.unit ? "Unit: " + rh.unit : "", rh.example ? "Example: " + rh.example : "", rh.default !== undefined && rh.default !== "" ? "Default when blank: " + rh.default : ""].filter(Boolean).join("\n") : "";
        return el("td", { class: "cell " + (c.kind === "input" ? "input " : c.kind === "optional" ? "optional " : "") + (edits.has(key) || (isNew && cur !== "") ? "changed" : ""), title: tipTxt }, inp, rh ? el("button", { class: "rowq", type: "button", title: tipTxt, "aria-label": "Help: " + rowVals[keyIx], onclick: () => panel.open(String(rowVals[keyIx]), el("p", { style: "white-space:pre-wrap" }, tipTxt)) }, "?") : null);
      };
      const rows = d.rows.map((row) => { const rh = RH && keyIx >= 0 ? RH[String(row.v[keyIx] ?? "")] : null; const need = rh && rh.required && (row.v[valIx] === null || row.v[valIx] === "") && !edits.has(row.r + ":" + d.value_col); return el("tr", { class: need ? "need" : null }, el("td", { class: "num muted" }, row.r), cols.map((c, ci) => cell(row.r, ci, c, row.v[ci], false, row.v))); });
      const news = appends.map((row, k) => el("tr", {}, el("td", { class: "num muted" }, "new"), cols.map((c, ci) => cell(-k - 1, ci, c, row[ci], true, row))));
      grid.replaceChildren(el("div", { class: "tbl-wrap", style: "max-height:520px" }, el("table", { class: "gc" }, el("thead", {}, th), el("tbody", {}, rows, news))));
      const from = d.offset + 1, to = d.offset + d.rows.length;
      pager.replaceChildren(el("span", {}, d.n_rows ? "rows " + from + "–" + to + " of " + d.n_rows : "no rows"), d.offset > 0 ? el("button", { class: "btn sm", type: "button", onclick: () => loadSheet(cur, Math.max(0, d.offset - (opts.limit || 200))) }, "← Previous") : null, d.truncated ? el("button", { class: "btn sm", type: "button", onclick: () => loadSheet(cur, d.offset + d.rows.length) }, "Next →") : null, el("span", { class: "spacer" }), el("span", {}, "saved " + fmtWhen(d.mtime)));
      refreshDirty();
    }
    async function save() {
      if (!dirty()) return;
      const body = { id: wid, sheet: cur, mtime: sheetData.mtime_ts, edits: Array.from(edits, ([k, value]) => { const [row, col] = k.split(":").map(Number); return { row, col, value }; }), appends: appends.filter((r) => r.some((v) => v !== "")) };
      saveBtn.disabled = true; saveBtn.replaceChildren(el("span", { class: "spin" }), " Saving");
      try {
        const r = await api("/api/sheet/save", body);
        toast("Saved " + r.changed + " change(s); backup kept");
        edits.clear(); appends = [];
        if (opts.onSaved) opts.onSaved(r);
        await loadSheet(cur, sheetData.offset);
      } catch (e) {
        if (e.status === 409 && /changed on disk/.test(e.message)) {
          if (await confirm({ title: "Workbook changed on disk", body: e.message + " Overwrite those cells with the page's values?", ok: "Overwrite", danger: true })) { body.force = true; try { const r = await api("/api/sheet/save", body); toast("Saved " + r.changed + " change(s)"); edits.clear(); appends = []; await loadSheet(cur, sheetData.offset); } catch (e2) { msg.replaceChildren(banner("stop", e2.message)); } }
        } else msg.replaceChildren(banner("stop", e.message));
      } finally { saveBtn.replaceChildren("Save"); refreshDirty(); }
    }
    box.load = load; box.isDirty = dirty;
    return box;
  }

  // ---- LenAI prompt card (D-UI4)
  function promptCard(id, a, promptText) {
    const pre = el("div", { class: "prompt" }, promptText || "(prompt text not found: " + (a.prompt_file || "") + ")");
    const prep = el("button", { class: "btn primary sm", type: "button", onclick: async () => { prep.disabled = true; try { const r = await api("/api/lenai", { id }); toast("Folder ready: " + r.folder); out.replaceChildren(banner("ok", "Prepared " + r.folder + (r.missing.length ? " — missing: " + r.missing.join(", ") : ""), el("div", { class: "gc-row", style: "margin-top:6px" }, openBtn(r.folder, "Open folder")))); } catch (e) { toast(e.message); } finally { prep.disabled = false; } } }, "Prepare LenAI folder");
    const out = el("div", {});
    return [
      a.when ? part("When to use", el("p", { class: "note" }, a.when)) : null,
      a.attach && a.attach.length ? part("What to attach", el("ul", { class: "steps" }, a.attach.map((f) => el("li", {}, el("code", {}, f))))) : null,
      part("Prompt" + (a.version ? " · " + a.version : ""), pre, el("div", { class: "gc-row", style: "margin-top:6px" }, copyBtn(() => pre.textContent, "Copy prompt"), prep)),
      a.expected_return ? part("Expected return", el("p", { class: "note" }, a.expected_return)) : null,
      a.return_to ? part("Where the return goes", el("p", { class: "note" }, a.return_to)) : null,
      out,
    ];
  }

  // ---- markdown (small: headings, lists, code, emphasis, links, tables)
  function md(src) {
    const lines = String(src || "").replace(/\r\n/g, "\n").split("\n");
    let html = "", inCode = false, list = null, inTable = false;
    const inline = (s) => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/(^|\W)\*([^*]+)\*(?=\W|$)/g, "$1<i>$2</i>").replace(/\[([^\]]+)\]\(([^)]+)\)/g, (m, t, u) => `<a href="${/^https?:/.test(u) ? esc(u) : "#"}" ${/^https?:/.test(u) ? 'target="_blank" rel="noopener"' : 'data-doc="' + esc(u) + '"'}>${t}</a>`);
    const closeList = () => { if (list) { html += `</${list}>`; list = null; } };
    const closeTable = () => { if (inTable) { html += "</tbody></table></div>"; inTable = false; } };
    for (let i = 0; i < lines.length; i++) {
      const ln = lines[i];
      if (/^```/.test(ln)) { closeList(); closeTable(); inCode = !inCode; html += inCode ? "<pre class='prompt'>" : "</pre>"; continue; }
      if (inCode) { html += esc(ln) + "\n"; continue; }
      const h = ln.match(/^(#{1,6})\s+(.*)$/);
      if (h) { closeList(); closeTable(); const n = Math.min(h[1].length + 2, 6); html += `<h${n} id="d-${h[2].toLowerCase().replace(/[^a-z0-9]+/g, "-")}">${inline(h[2])}</h${n}>`; continue; }
      if (/^\s*\|.*\|\s*$/.test(ln)) {
        const cells = ln.trim().slice(1, -1).split("|").map((c) => c.trim());
        if (cells.every((c) => /^:?-{2,}:?$/.test(c))) continue;
        if (!inTable) { closeList(); inTable = true; html += `<div class="tbl-wrap"><table class="gc"><thead><tr>${cells.map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead><tbody>`; continue; }
        html += `<tr>${cells.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`; continue;
      }
      closeTable();
      const li = ln.match(/^\s*([-*]|\d+[.)])\s+(.*)$/);
      if (li) { const kind = /^\d/.test(li[1]) ? "ol" : "ul"; if (list !== kind) { closeList(); list = kind; html += `<${kind} class="steps">`; } html += `<li>${inline(li[2])}</li>`; continue; }
      if (!ln.trim()) { closeList(); continue; }
      closeList(); html += `<p>${inline(ln)}</p>`;
    }
    closeList(); closeTable(); if (inCode) html += "</pre>";
    return html;
  }

  // ---- help panel, glossary, terms
  let glossary = [], glossMap = new Map();
  async function loadGlossary() {
    try { glossary = (await api("/api/glossary")).entries; } catch (e) { glossary = []; }
    glossMap = new Map(); for (const g of glossary) { glossMap.set(g.term.toLowerCase(), g); for (const a of g.aliases || []) glossMap.set(String(a).toLowerCase(), g); }
    return glossary;
  }
  const panel = (() => {
    let p;
    const ensure = () => { if (p) return p; p = el("aside", { class: "panel", role: "complementary", "aria-label": "Help", tabindex: "-1" }, el("div", { class: "hd" }, el("span", { id: "panel-title" }, "Help"), el("span", { class: "spacer" }), el("button", { class: "btn sm", type: "button", onclick: () => close(), "aria-label": "Close help" }, "✕")), el("div", { class: "bd", id: "panel-body" })); document.body.append(p); return p; };
    const open = (title, ...nodes) => { ensure(); $("#panel-title", p).textContent = title; $("#panel-body", p).replaceChildren(...nodes.flat(Infinity).filter(Boolean)); p.classList.add("open"); p.focus(); };
    const close = () => { if (p) p.classList.remove("open"); };
    return { open, close };
  })();
  function showTerm(t) {
    const g = glossMap.get(String(t).toLowerCase());
    if (!g) return panel.open(t, el("p", {}, "No glossary entry for “" + t + "”."));
    panel.open(g.term, el("dl", {}, el("dt", {}, "Definition"), el("dd", {}, g.definition), g.aliases && g.aliases.length ? [el("dt", {}, "Also called"), el("dd", {}, g.aliases.join(", "))] : null, g.category ? [el("dt", {}, "Category"), el("dd", {}, g.category)] : null, g.see_also && g.see_also.length ? [el("dt", {}, "See also"), el("dd", {}, g.see_also.map((s, i) => [i ? ", " : null, term(s)]))] : null, g.source ? [el("dt", {}, "Source"), el("dd", {}, g.source)] : null, g.used_in && g.used_in.length ? [el("dt", {}, "Used in"), el("dd", {}, g.used_in.map((u, i) => [i ? ", " : null, el("a", { href: "#" + u, onclick: () => panel.close() }, u)]))] : null));
  }
  const term = (text, shown) => el("a", { href: "#", class: "term", onclick: (e) => { e.preventDefault(); showTerm(text); }, title: "Glossary: " + text }, shown || text);
  const tip = (text, title) => el("button", { class: "q-tip", type: "button", "aria-label": "Help: " + (title || ""), title: text, onclick: () => panel.open(title || "Help", el("p", {}, text)) }, "?");
  async function showDoc(rel, title) {
    try { const r = await fetch("/doc/" + rel.split("/").map(encodeURIComponent).join("/")); if (!r.ok) throw new Error("not found"); const t = await r.text(); const body = el("div", { html: /\.html?$/i.test(rel) ? t : md(t) }); $$("a[data-doc]", body).forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); showDoc(rel.replace(/[^/]*$/, "") + a.dataset.doc, a.textContent); })); panel.open(title || rel, body); }
    catch (e) { panel.open(title || rel, banner("flag", "Document not found: " + rel)); }
  }
  function glossarySection() {
    const dl = el("dl", { class: "gloss-list" });
    const cats = {};
    for (const g of glossary) (cats[g.category || "general"] = cats[g.category || "general"] || []).push(g);
    const box = el("div", {});
    const q = el("input", { type: "search", placeholder: "Filter glossary", "aria-label": "Filter glossary", oninput: () => render(q.value) });
    const render = (f) => { const fl = (f || "").toLowerCase(); dl.replaceChildren(...glossary.filter((g) => !fl || (g.term + " " + g.definition + " " + (g.aliases || []).join(" ")).toLowerCase().includes(fl)).flatMap((g) => [el("dt", { id: "glossary-" + slug(g.term) }, g.term, g.category ? el("span", { class: "muted small" }, " · " + g.category) : null), el("dd", {}, g.definition, g.aliases && g.aliases.length ? el("span", { class: "muted" }, " (also: " + g.aliases.join(", ") + ")") : null)])); };
    render("");
    box.append(el("div", { class: "gc-row" }, q, el("span", { class: "muted small" }, glossary.length + " terms")), dl);
    return box;
  }
  const slug = (s) => String(s).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");

  // ---- search palette and index (UI_STANDARD §10)
  let indexEntries = [];
  function buildIndex(extra) {
    const out = [];
    $$(".gc-main section.sec").forEach((s) => { const h = $("h2.sec-title", s); if (h) out.push({ type: "section", label: h.textContent.trim(), anchor: s.id }); });
    $$(".gc-card[id]").forEach((c) => { const h = $("h3", c); if (h) out.push({ type: "card", label: h.textContent.trim(), anchor: c.id }); });
    for (const g of glossary) out.push({ type: "glossary", label: g.term, keywords: (g.aliases || []).join(" ") + " " + g.definition, term: g.term });
    for (const e of extra || []) out.push(e);
    indexEntries = out;
    return out;
  }
  function indexSection() {
    const ul = el("ul", { class: "index-list" });
    const items = indexEntries.filter((e) => e.type !== "glossary").concat(indexEntries.filter((e) => e.type === "glossary")).sort((a, b) => a.label.localeCompare(b.label));
    ul.replaceChildren(...items.map((e) => el("li", {}, e.anchor ? el("a", { href: "#" + e.anchor }, e.label) : el("a", { href: "#", onclick: (ev) => { ev.preventDefault(); showTerm(e.term); } }, e.label), el("span", { class: "k" }, e.type))));
    return ul;
  }
  const palette = (() => {
    let p, inp, res, sel = 0, hits = [];
    const ensure = () => {
      if (p) return p;
      inp = el("input", { type: "search", placeholder: "Search sections, inputs, checks, files, glossary …", "aria-label": "Search", oninput: () => run(inp.value), onkeydown: (e) => { if (e.key === "ArrowDown") { sel = Math.min(sel + 1, hits.length - 1); paint(); e.preventDefault(); } if (e.key === "ArrowUp") { sel = Math.max(sel - 1, 0); paint(); e.preventDefault(); } if (e.key === "Enter" && hits[sel]) { go(hits[sel]); } if (e.key === "Escape") close(); } });
      res = el("div", { class: "res", role: "listbox" });
      p = el("div", { class: "palette", role: "dialog", "aria-label": "Search", onclick: (e) => { if (e.target === p) close(); } }, el("div", { class: "box" }, inp, res));
      document.body.append(p); return p;
    };
    const run = (q) => { const ql = q.trim().toLowerCase(); hits = !ql ? [] : indexEntries.filter((e) => (e.label + " " + (e.keywords || "")).toLowerCase().includes(ql)).slice(0, 40); sel = 0; paint(); };
    const paint = () => { const groups = {}; hits.forEach((h, i) => (groups[h.type] = groups[h.type] || []).push([h, i])); res.replaceChildren(...Object.entries(groups).flatMap(([t, hs]) => [el("div", { class: "grp" }, t), ...hs.map(([h, i]) => el("a", { href: "#", class: i === sel ? "on" : "", role: "option", "aria-selected": String(i === sel), onclick: (e) => { e.preventDefault(); go(h); } }, h.label, el("span", { class: "k" }, h.anchor ? "#" + h.anchor : t)))])); if (!hits.length && inp.value.trim()) res.replaceChildren(el("div", { class: "grp" }, "no match")); };
    const go = (h) => { close(); if (h.anchor) { const t = document.getElementById(h.anchor); if (t) { t.scrollIntoView({ block: "start" }); if (t.classList.contains("gc-card")) t.open && t.open(); const f = $("button.toggle, a, input, button", t); if (f) f.focus(); } } else if (h.term) showTerm(h.term); else if (h.doc) showDoc(h.doc, h.label); };
    const open = () => { ensure(); p.classList.add("open"); inp.value = ""; run(""); inp.focus(); };
    const close = () => { if (p) p.classList.remove("open"); };
    return { open, close };
  })();
  document.addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); palette.open(); } if (e.key === "Escape") { palette.close(); panel.close(); } });

  // ---- theme (D-UI3)
  function applyTheme(t) { document.documentElement.setAttribute("data-theme", t); store.set("theme", t); $$(".theme-btn").forEach((b) => { b.textContent = t === "dark" ? "Light theme" : "Dark theme"; b.setAttribute("aria-pressed", String(t === "dark")); }); }
  function themeButton() { const b = el("button", { class: "btn sm theme-btn", type: "button", onclick: () => applyTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark") }, "Dark theme"); return b; }
  applyTheme(store.get("theme", window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));

  // ---- shell (UI_STANDARD §3)
  let poller = null;
  function shell(opts) {
    // opts: title, version, sections:[{id, num, title, sub, group, hidden}], help:[{label, doc|href}], meta
    document.body.replaceChildren();
    document.body.append(el("a", { class: "skip", href: "#main" }, "Skip to main content"));
    const meta = el("div", { class: "meta" });
    const overall = el("span", { id: "overall" });
    const header = el("header", { class: "gc-header", role: "banner" },
      el("h1", {}, opts.title, el("small", { id: "ver" }, opts.version || "")), meta, el("span", { class: "spacer" }),
      el("button", { class: "search", type: "button", onclick: () => palette.open(), "aria-label": "Search (Ctrl+K)" }, "Search …", el("kbd", {}, "Ctrl K")),
      themeButton(), opts.helpDoc ? el("button", { class: "btn sm", type: "button", onclick: () => showDoc(opts.helpDoc, "How to use") }, "How to use") : null, overall);
    const nav = el("nav", { class: "gc-nav", "aria-label": "Index" });
    const main = el("main", { class: "gc-main", id: "main", tabindex: "-1" });
    const secs = {};
    let grp = null;
    for (const s of opts.sections) {
      if (s.hidden) continue;
      if (s.group && s.group !== grp) { grp = s.group; nav.append(el("div", { class: "grp" }, grp)); }
      const a = el("a", { href: "#" + s.id, id: "nav-" + s.id }, el("span", { class: "num" }, s.num || ""), el("span", { class: "lbl" }, s.title), el("span", { class: "navpill" }));
      nav.append(a);
      const sec = el("section", { class: "sec", id: s.id, "aria-labelledby": s.id + "-h" }, el("h2", { class: "sec-title", id: s.id + "-h" }, s.num ? el("span", { class: "num" }, s.num) : null, s.title, s.sub ? el("span", { class: "sub" }, s.sub) : null));
      main.append(sec); secs[s.id] = sec;
    }
    const status = el("footer", { class: "gc-status", role: "contentinfo" }, el("span", { id: "st-server" }, "server …"), el("span", { id: "st-run" }), el("span", { class: "spacer" }), el("span", { id: "st-kit" }, "kit " + KIT));
    const offlineBar = el("div", { class: "offline", id: "offline", style: "display:none" });
    document.body.append(header, offlineBar, el("div", { class: "gc-body" }, nav, main), status);
    // active section in the index
    const io = new IntersectionObserver((ents) => { for (const e of ents) if (e.isIntersecting) { $$("a", nav).forEach((a) => a.classList.toggle("on", a.getAttribute("href") === "#" + e.target.id)); } }, { rootMargin: "-20% 0px -70% 0px" });
    Object.values(secs).forEach((s) => io.observe(s));
    return {
      sections: secs, main, nav, header,
      setMeta: (...nodes) => meta.replaceChildren(...nodes.flat(Infinity)),
      setOverall: (s, text) => overall.replaceChildren(pill(s, text)),
      setNavStatus: (id, s) => { const a = $("#nav-" + id); if (a) $(".navpill", a).replaceChildren(s ? pill(s) : ""); },
      setServer: (txt) => ($("#st-server").textContent = txt), setRun: (txt) => ($("#st-run").textContent = txt),
      offline: (on, launcher) => { offline = on; $("#offline").style.display = on ? "" : "none"; $("#offline").replaceChildren(on ? ["Server not running — double-click ", el("code", {}, launcher || "the launcher"), " to start it. The guide, glossary, prompts and commands still work here; Run, Open and Save need the server."] : ""); runListeners.forEach((f) => f()); },
    };
  }

  function poll(fn, ms) {
    let timer = null, busy = false;
    const tick = async () => { if (busy) return; busy = true; try { state = await fn(); runListeners.forEach((f) => f()); } catch (e) { console.warn(e); } busy = false; };
    const start = () => { stop(); tick(); timer = setInterval(tick, ms); };
    const stop = () => { if (timer) clearInterval(timer); timer = null; };
    document.addEventListener("visibilitychange", () => (document.hidden ? stop() : start()));
    poller = { start, stop, tick, get active() { return timer !== null; } };
    return poller;
  }

  return { KIT, $, $$, el, esc, api, toast, announce, pill, worst, STATUS, table, tableHead, card, part, note, banner, copyBtn, cmdCard, runButton, stopButton, openBtn,
    logView, fileList, confirm, prompt, editor, promptCard, md, loadGlossary, glossarySection, indexSection, buildIndex, showTerm, term, tip, showDoc, panel, palette,
    shell, poll, fmtNum, fmtWhen, fmtDur, ago, slug, store, get state() { return state; }, get running() { return running(); }, get glossary() { return glossary; }, get offline() { return offline; } };
})();
