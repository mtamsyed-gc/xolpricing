/* GC tool UI kit 0.8.0 — shared behaviour: API calls, polling, table rendering, cards, toasts.
   No framework. A tool page builds its sections with these helpers and renders from /api/state. */
window.GC = (function () {
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const el = (tag, attrs, ...children) => {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "class") e.className = v;
      else if (k === "html") e.innerHTML = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else if (v !== null && v !== undefined) e.setAttribute(k, v);
    }
    for (const c of children.flat()) if (c !== null && c !== undefined) e.append(c.nodeType ? c : document.createTextNode(String(c)));
    return e;
  };

  async function api(path, body) {
    const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
    const j = await r.json().catch(() => ({ error: "bad reply" }));
    if (!r.ok || j.error) throw new Error(j.error || r.statusText);
    return j;
  }

  let toastT;
  function toast(msg, ms) {
    let t = $(".toast") || document.body.appendChild(el("div", { class: "toast" }));
    t.textContent = msg; t.classList.add("show");
    clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove("show"), ms || 2600);
  }

  const SEV_LABEL = { ok: "OK", flag: "Flags", stop: "STOP", info: "Info", running: "Running", none: "Not run", stale: "Earlier run" };
  function pill(status, text) {
    return el("span", { class: "pill " + status }, el("span", { class: "dot" }), text || SEV_LABEL[status] || status);
  }

  const isNum = (s) => s !== "" && s !== null && !isNaN(s) && isFinite(s);
  function fmtNum(s) {
    const n = Number(s);
    if (Number.isInteger(n) && Math.abs(n) < 1e15) return Math.abs(n) < 10000 ? String(n) : n.toLocaleString("en-GB");
    const a = Math.abs(n);
    if (a >= 1000) return n.toLocaleString("en-GB", { maximumFractionDigits: 0 });
    if (a >= 1) return n.toLocaleString("en-GB", { maximumFractionDigits: 4 });
    return n.toLocaleString("en-GB", { maximumSignificantDigits: 4 });
  }

  function table(columns, rows, opts) {
    opts = opts || {};
    const thead = el("thead", {}, el("tr", {}, columns.map((c) => el("th", {}, c))));
    const sevIx = columns.indexOf("severity");
    const tbody = el("tbody", {}, rows.map((r) =>
      el("tr", { class: sevIx >= 0 ? "sev-" + r[sevIx] : null },
        r.map((v, i) => el("td", { class: isNum(v) && !(opts.textCols || []).includes(columns[i]) ? "num" : null },
          isNum(v) && !(opts.textCols || []).includes(columns[i]) ? fmtNum(v) : v)))));
    return el("div", { class: "tbl-wrap" }, el("table", { class: "gc" }, thead, tbody));
  }

  function card(opts) {
    // opts: id, num, title, sub, status, open, always, body (node or fn)
    const head = el("div", { class: "head" + (opts.always ? "" : " toggle") },
      opts.num !== undefined ? el("span", { class: "num" }, opts.num) : null,
      el("h2", {}, opts.title),
      opts.sub ? el("span", { class: "sub" }, opts.sub) : null,
      el("span", { class: "spacer" }),
      el("span", { class: "pillbox" }),
      opts.always ? null : el("span", { class: "chev" }, "›"));
    const body = el("div", { class: "body" });
    const c = el("section", { class: "gc-card " + (opts.always ? "always " : "") + (opts.open ? "open " : "") + "s-" + (opts.status || "none"), id: opts.id }, head, body);
    if (!opts.always) head.addEventListener("click", () => c.classList.toggle("open"));
    c.setStatus = (s, text) => { c.className = c.className.replace(/\bs-\w+/g, "").trim() + " s-" + s; const pb = $(".pillbox", head); pb.replaceChildren(pill(s, text)); };
    c.setBody = (node) => body.replaceChildren(node);
    c.body = body; c.head = head;
    if (opts.status) c.setStatus(opts.status);
    return c;
  }

  function poll(fn, ms) {
    let timer = null, busy = false;
    const tick = async () => { if (busy) return; busy = true; try { await fn(); } catch (e) { console.warn(e); } busy = false; };
    const start = () => { stop(); tick(); timer = setInterval(tick, ms); };
    const stop = () => { if (timer) clearInterval(timer); timer = null; };
    document.addEventListener("visibilitychange", () => (document.hidden ? stop() : start()));
    return { start, stop, tick, get active() { return timer !== null; } };
  }

  function fmtWhen(s) { return s ? s.slice(11, 16) + " · " + s.slice(0, 10) : "—"; }

  return { $, $$, el, api, toast, pill, table, card, poll, fmtNum, fmtWhen, SEV_LABEL };
})();
