// ── SerenLoci viewer - leaf logic ────────────────────────────────────────────
// Snaps onto the SerenMeninges shell. The shell ALREADY provides api(),
// escapeHtml(), showTab(), and the bearer-token modal - this file CALLS them,
// it never redefines them. (The old carve shipped its own api()/escapeHtml()
// and wired to a header that no longer exists; that's what was breaking.)
//
// What the shell gives us, that we rely on here:
//   api(path, opts)  - fetch with the saved bearer token auto-attached,
//                      returns parsed JSON for our endpoints, throws on !ok.
//   escapeHtml(s)    - the same escaper the monolith had.
//   showTab(id)      - toggles `.tabbar .tab` + `.view` where id matches.
//   the 🔑 token modal (openToken/saveToken/clearToken) + #tokenBtn.

const FUND = "*";
let factsCache = [];                   // last /facts payload (live, by current toggle)
let projectsCache = [];                // distinct projects seen
let activeChamber = "__all__";

// ---- errors -----------------------------------------------------------------
function showErr(msg) {
    document.getElementById("globalErr").innerHTML = `<div class="err">${escapeHtml(msg)}</div>`;
}
function clearErr() { document.getElementById("globalErr").innerHTML = ""; }

// ---- small helpers ----------------------------------------------------------
function chamberLabel(p) { return p === FUND ? "★ Fundamentals" : p; }
function relAge(ts) {
    if (!ts) return "";
    const s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 90) return Math.round(s) + "s ago";
    if (s < 5400) return Math.round(s / 60) + "m ago";
    if (s < 129600) return Math.round(s / 3600) + "h ago";
    return Math.round(s / 86400) + "d ago";
}

// ---- facts hall -------------------------------------------------------------
async function loadFacts() {
    clearErr();
    const sup = document.getElementById("showSuperseded").checked;
    try {
        const data = await api(`/facts?include_superseded=${sup ? "true" : "false"}`);
        factsCache = data.facts || [];
        // distinct projects (live + any in payload), Fundamentals first then alpha
        const set = new Set(factsCache.map(f => f.project));
        projectsCache = [...set].sort((a, b) => (a === FUND ? -1 : b === FUND ? 1 : a.localeCompare(b)));
        renderChambers();
        renderFacts();
        populateScopeSelects();
    } catch (e) { showErr(e.message); }
}

function renderChambers() {
    const el = document.getElementById("chambers");
    const liveByProj = {};
    factsCache.forEach(f => { if (!f.superseded_at) liveByProj[f.project] = (liveByProj[f.project] || 0) + 1; });
    const total = Object.values(liveByProj).reduce((a, b) => a + b, 0);
    let html = chamberBtn("__all__", "All chambers", total);
    projectsCache.forEach(p => { html += chamberBtn(p, chamberLabel(p), liveByProj[p] || 0); });
    el.innerHTML = html;
    el.querySelectorAll(".ch").forEach(b => b.onclick = () => { activeChamber = b.dataset.p; renderChambers(); renderFacts(); });
}
function chamberBtn(p, label, n) {
    const active = p === activeChamber ? " active" : "";
    return `<button class="ch${active}" data-p="${escapeHtml(p)}"><span>${escapeHtml(label)}</span><span class="n">${n}</span></button>`;
}

function renderFacts() {
    const el = document.getElementById("factStack");
    let rows = factsCache.slice();
    if (activeChamber !== "__all__") rows = rows.filter(f => f.project === activeChamber);
    if (!rows.length) { el.innerHTML = `<div class="empty">No facts in this chamber yet.</div>`; return; }
    // group by project, live before superseded, key order (server already sorts project,key)
    const groups = {};
    rows.forEach(f => { (groups[f.project] = groups[f.project] || []).push(f); });
    const order = Object.keys(groups).sort((a, b) => (a === FUND ? -1 : b === FUND ? 1 : a.localeCompare(b)));
    let html = "";
    order.forEach(p => {
        if (activeChamber === "__all__") html += `<div class="group-h">${escapeHtml(chamberLabel(p))}</div>`;
        groups[p].sort((a, b) => (!!a.superseded_at - !!b.superseded_at) || a.key.localeCompare(b.key));
        groups[p].forEach(f => html += factCard(f));
    });
    el.innerHTML = html;
    el.querySelectorAll("[data-hk]").forEach(b => b.onclick = () => gotoHistory(b.dataset.hp, b.dataset.hk));
}

function factCard(f) {
    const dim = f.superseded_at ? " dim" : "";
    const why = f.why ? `<div class="why"><b>why</b> · ${escapeHtml(f.why)}</div>` : "";
    const supTag = f.superseded_at ? `<span class="tag superseded">superseded</span>` : "";
    return `<div class="card${dim}">
        <div class="k">${escapeHtml(f.key)}</div>
        <div class="v">${escapeHtml(f.value)}</div>
        ${why}
        <div class="foot">
          <span class="tag">${escapeHtml(f.source || "user")}</span>
          ${supTag}
          <span>${escapeHtml(f.project)}</span>
          <span>${relAge(f.created_at)}</span>
          <button class="linkbtn" data-hk="${escapeHtml(f.key)}" data-hp="${escapeHtml(f.project)}">history ↗</button>
        </div>
      </div>`;
}

// ---- search -----------------------------------------------------------------
async function runSearch() {
    const q = document.getElementById("searchQ").value.trim();
    if (!q) return;
    const scopeVal = document.getElementById("searchScope").value;  // "" = all
    const body = {
        query: q,
        project: scopeVal === "" ? null : scopeVal,
        n_results: parseInt(document.getElementById("nRes").value || "10", 10),
        include_fundamentals: document.getElementById("incFund").checked,
        include_superseded: document.getElementById("incSup").checked,
    };
    const el = document.getElementById("searchStack");
    el.innerHTML = `<div class="empty">Searching…</div>`;
    document.getElementById("served").textContent = "";
    try {
        const data = await api("/search", { method: "POST", body: JSON.stringify(body) });
        document.getElementById("served").textContent =
            `served by: ${data.finder}  ·  ${data.hits.length} hit${data.hits.length === 1 ? "" : "s"}  ·  scope: ${data.project ?? "all"}`;
        if (!data.hits.length) { el.innerHTML = `<div class="empty">No hits. Try fewer / different words, or widen the scope.</div>`; return; }
        el.innerHTML = data.hits.map(hitRow).join("");
    } catch (e) { el.innerHTML = ""; showErr(e.message); }
}
function hitRow(h) {
    const pct = Math.round((h.score || 0) * 100);
    const why = h.why ? `<div class="why"><b>why</b> · ${escapeHtml(h.why)}</div>` : "";
    const dist = (h.raw_distance != null) ? ` · d=${h.raw_distance}` : "";
    return `<div class="card"><div class="hit">
        <div class="score">
          <span class="num">${(h.score ?? 0).toFixed(2)}</span>
          <span class="badge ${escapeHtml(h.match_kind)}">${escapeHtml(h.match_kind)}</span>
        </div>
        <div>
          <div class="k">${escapeHtml(h.key)}</div>
          <div class="v">${escapeHtml(h.value)}</div>
          ${why}
          <div class="foot"><span>${escapeHtml(h.project)}</span><span>${pct}%${dist}</span></div>
        </div>
      </div></div>`;
}

// ---- history ----------------------------------------------------------------
function gotoHistory(project, key) {
    switchTab("history");
    document.getElementById("histProject").value = project;
    document.getElementById("histKey").value = key;
    runHistory();
}
async function runHistory() {
    const project = document.getElementById("histProject").value || FUND;
    const key = document.getElementById("histKey").value.trim();
    const el = document.getElementById("histStack");
    if (!key) { el.innerHTML = `<div class="empty">Enter a key to trace.</div>`; return; }
    el.innerHTML = `<div class="empty">Tracing…</div>`;
    try {
        const qs = `?project=${encodeURIComponent(project)}&key=${encodeURIComponent(key)}`;
        const data = await api(`/fact/history${qs}`);
        if (!data.count) { el.innerHTML = `<div class="empty">No history for <b>${escapeHtml(project)}</b> / <b>${escapeHtml(key)}</b>.</div>`; return; }
        el.innerHTML = data.history.map((f, i) => {
            const live = !f.superseded_at;
            const tag = live ? `<span class="tag" style="color:var(--accent);border-color:var(--accent-dim)">live</span>`
                : `<span class="tag superseded">superseded</span>`;
            const why = f.why ? `<div class="why"><b>why</b> · ${escapeHtml(f.why)}</div>` : "";
            return `<div class="card${live ? "" : " dim"}">
            <div class="foot" style="margin:0 0 8px"><span>#${data.count - i}</span>${tag}<span>${relAge(f.created_at)}</span></div>
            <div class="v">${escapeHtml(f.value)}</div>
            ${why}
          </div>`;
        }).join("");
    } catch (e) { el.innerHTML = ""; showErr(e.message); }
}

// ---- overview ---------------------------------------------------------------
async function loadOverview() {
    try {
        const root = await api("/");
        const c = root.counts || { live: 0, history: 0, projects: 0 };
        document.getElementById("stats").innerHTML = `
          ${stat(c.live, "live facts", true)}
          ${stat(c.history, "history rows", false)}
          ${stat(c.projects, "chambers", false)}`;
        setFinder(root.finder);
        // per-chamber bars from the live facts we already have (or fetch)
        if (!factsCache.length) { try { const d = await api("/facts"); factsCache = d.facts || []; } catch (_) { } }
        const live = factsCache.filter(f => !f.superseded_at);
        const byProj = {};
        live.forEach(f => byProj[f.project] = (byProj[f.project] || 0) + 1);
        const max = Math.max(1, ...Object.values(byProj));
        const order = Object.keys(byProj).sort((a, b) => (a === FUND ? -1 : b === FUND ? 1 : (byProj[b] - byProj[a])));
        document.getElementById("overviewBars").innerHTML = order.map(p => `
          <div class="bar-row">
            <span class="pn">${escapeHtml(chamberLabel(p))}</span>
            <span class="bar"><i style="width:${Math.round(byProj[p] / max * 100)}%"></i></span>
            <span class="bn">${byProj[p]}</span>
          </div>`).join("") || `<div class="empty">No facts yet - seed the fundamentals.</div>`;
        document.getElementById("finderNote").innerHTML = root.finder === "vector"
            ? `Finder: <b style="color:var(--accent)">vector</b> - a sqlite-vec index is live, so the associative jump (“that CUDA thing”) works. Exact-key still leads at score 1.0.`
            : `Finder: <b>lexical</b> - embedding-free floor (FTS5 over key/value/why). Set <code>storage.embedding_model</code> to light up the vector finder. Exact-key always leads at 1.0.`;
    } catch (e) { showErr(e.message); }
}
function stat(n, lbl, accent) {
    return `<div class="stat${accent ? " accent" : ""}"><div class="big">${n}</div><div class="lbl">${lbl}</div></div>`;
}
// The finder badge lives in the header_aside brick (a shared .head-pill). Guard
// the lookup so a leaf without that brick is a no-op, never a null-deref.
function setFinder(finder) {
    const b = document.getElementById("finderBadge");
    if (!b) return;
    b.textContent = `finder: ${finder}`;
    b.className = "head-pill" + (finder === "vector" ? " hot" : "");
}

// ---- scope selects ----------------------------------------------------------
function populateScopeSelects() {
    const scope = document.getElementById("searchScope");
    const hist = document.getElementById("histProject");
    const cur = scope.value;
    scope.innerHTML = `<option value="">All scopes</option>` +
        projectsCache.map(p => `<option value="${escapeHtml(p)}">${escapeHtml(chamberLabel(p))}</option>`).join("");
    scope.value = cur;
    const hcur = hist.value;
    hist.innerHTML = projectsCache.map(p => `<option value="${escapeHtml(p)}">${escapeHtml(chamberLabel(p))}</option>`).join("")
        || `<option value="${FUND}">★ Fundamentals</option>`;
    if (hcur) hist.value = hcur;
}

// ---- tabs -------------------------------------------------------------------
// Thin wrapper over the shell's showTab(): do the display toggle the shell way,
// then lazy-load the Overview tab's data the first/every time it's opened.
// (gotoHistory + the tab buttons in tabs.html both call this.)
function switchTab(tab) {
    showTab(tab);
    if (tab === "overview") loadOverview();
}

// ---- wiring -----------------------------------------------------------------
// Only the controls that actually exist in body.html. The tab buttons wire
// themselves via onclick in tabs.html; the 🔑 token modal is the shell's.
document.getElementById("showSuperseded").onchange = loadFacts;
document.getElementById("searchBtn").onclick = runSearch;
document.getElementById("searchQ").addEventListener("keydown", e => { if (e.key === "Enter") runSearch(); });
document.getElementById("histBtn").onclick = runHistory;
document.getElementById("histKey").addEventListener("keydown", e => { if (e.key === "Enter") runHistory(); });

// ---- header refresh ---------------------------------------------------------
// Wired from the ⟳ button in header_aside.html. Reloads the active tab's data.
function reload() {
    boot();
    if (document.getElementById("overview").classList.contains("active")) loadOverview();
}

// ---- boot -------------------------------------------------------------------
async function boot() {
    try { const root = await api("/"); setFinder(root.finder); }
    catch (e) { showErr(e.message); }
    loadFacts();
}
boot();
