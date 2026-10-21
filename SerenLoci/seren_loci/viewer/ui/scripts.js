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

// ---- figures: the exhibits a fact cites ------------------------------------
// A picture can't ride in an <img src> here: the browser sends no bearer with
// it. So each thumbnail is fetched through the token (apiBlob) and shown from
// an object URL. Only still pictures are shown; a clip, a sound or a PDF is a
// chip saying what it is and how big. 15 Oct 2026.
const STILL = new Set(["image/png", "image/jpeg", "image/webp", "image/gif"]);
async function apiBlob(path) {
    const headers = {};
    const tok = getToken();
    if (tok) headers["Authorization"] = "Bearer " + tok;
    const res = await fetch(path, { headers });
    if (!res.ok) throw new Error(res.status + " " + res.statusText);
    return URL.createObjectURL(await res.blob());
}
function human(n) {
    n = Number(n || 0);
    for (const u of ["B", "KB", "MB", "GB"]) { if (n < 1024 || u === "GB") return (u === "B" ? n : n.toFixed(1)) + " " + u; n /= 1024; }
}
// Every figure in a strip is a button to the figure modal ("figures viewable
// in a loci key", 20 Oct 2026): data-open-fig carries the digest and
// data-fact the fact's id, so a delegated click can build the modal from the
// strip when the gallery has not been loaded yet.
function figPath(project, key, digest) {
    return `/fact/figures/${encodeURIComponent(digest)}?project=${encodeURIComponent(project)}&key=${encodeURIComponent(key)}`;
}
function figuresStrip(f) {
    const figs = f.figures || [];
    if (!figs.length) return "";
    const at = `data-fact="${escapeHtml(f.id)}" data-project="${escapeHtml(f.project)}" data-key="${escapeHtml(f.key)}"`;
    return `<div class="figs">` + figs.map(g => {
        const title = escapeHtml(g.caption || g.file || "");
        const open = `data-open-fig="${escapeHtml(g.digest)}" ${at}`;
        if (STILL.has(g.mime)) {
            return `<figure class="fig" title="${title}" ${open}><img alt="${title}" data-fig="${figPath(f.project, f.key, g.digest)}"><figcaption>${title}</figcaption></figure>`;
        }
        return `<span class="fig chip" title="${title}" ${open}>${escapeHtml((g.mime || "file").split("/")[0])} · ${human(g.bytes)}</span>`;
    }).join("") + `</div>`;
}
function hydrateFigures(root) {
    // img, video and audio take the object URL as src; a link (a PDF) as href.
    root.querySelectorAll("[data-fig]").forEach(async el => {
        const path = el.dataset.fig;
        el.removeAttribute("data-fig");
        try {
            const url = await apiBlob(path);
            if (el.tagName === "A") el.href = url; else el.src = url;
            el.classList.add("ready");
        }
        catch (e) { el.replaceWith(Object.assign(document.createElement("span"), { className: "fig chip", textContent: "picture missing" })); }
    });
}

// ---- the Figures hall: figure first ----------------------------------------
// Every figure once, the newest kept first; open one and it shows the facts
// that cite it. A figure rides on more than one row when a new value cites
// the old one again, so the gallery is keyed by the digest and "what cites
// this" is a list. Mimics Memory's Eidetic hall (20 Oct 2026).
let _figGallery = [];
let _histCache = [];                 // the last trace, so a strip click there finds its fact
function figKind(p) { return (p.mime || "file").split("/")[0]; }
function figGlyph(p) {
    const k = figKind(p);
    return k === "audio" ? "♪ sound" : k === "video" ? "▶ clip" : p.mime === "application/pdf" ? "PDF" : k;
}
async function loadFigures() {
    const el = document.getElementById("figureGallery");
    const hint = document.getElementById("figuresHint");
    try {
        const data = await api("/figures?limit=1000");
        _figGallery = data.figures || [];
        if (!_figGallery.length) {
            el.innerHTML = `<div class="empty">${data.enabled === false ? "this store keeps no figures (figures.enabled is off)" : "no figures cited yet"}</div>`;
            return;
        }
        hint.textContent = `${data.count} figure${data.count === 1 ? "" : "s"} · ${human(data.bytes)} · newest first · open one to see what cites it`;
        el.innerHTML = `<div class="gallery">${_figGallery.map(figureTile).join("")}</div>`;
        hydrateFigures(el);
    } catch (e) { el.innerHTML = ""; showErr(e.message); }
}
function figureTile(p, i) {
    const n = (p.facts || []).length;
    const title = escapeHtml(p.caption || p.file || p.digest.slice(0, 12));
    const on = `<span class="on">on ${n} fact${n === 1 ? "" : "s"}</span>`;
    const face = p.missing ? `<span class="what">figure missing</span>`
               : !p.via ? `<span class="what"><span class="glyph">${escapeHtml(figGlyph(p))}</span><br>history only</span>`
               : STILL.has(p.mime) ? `<img alt="${title}" data-fig="${figPath(p.via.project, p.via.key, p.digest)}">`
               : `<span class="what"><span class="glyph">${escapeHtml(figGlyph(p))}</span><br>${human(p.bytes)}</span>`;
    return `<button class="tile${p.missing ? " missing" : ""}" data-pic="${i}" title="${title}">${face}${on}</button>`;
}

// The modal. One for the gallery and for a strip: the same card, the same
// header row with the title and a square close (the family's shape, like
// Memory's eidetic card after 20 Oct 2026).
function citingCard(c) {
    const tag = c.live ? `<span class="tag" style="color:var(--accent);border-color:var(--accent-dim)">live</span>`
                       : `<span class="tag superseded">superseded</span>`;
    const why = c.why ? `<div class="why"><b>why</b> · ${escapeHtml(c.why)}</div>` : "";
    return `<div class="card${c.live ? "" : " dim"}">
        <div class="k">${escapeHtml(c.key)}</div>
        <div class="v">${escapeHtml(c.value)}</div>
        ${why}
        <div class="foot">${tag}<span>${escapeHtml(c.project)}</span><span>${relAge(c.created_at)}</span>
          <button class="linkbtn" data-hk="${escapeHtml(c.key)}" data-hp="${escapeHtml(c.project)}">history ↗</button></div>
      </div>`;
}
function openFigure(p) {
    if (!p) return;
    let o = document.getElementById("pic-overlay");
    if (!o) {
        o = document.createElement("div");
        o.id = "pic-overlay";
        o.className = "pic-overlay";
        document.body.appendChild(o);
        o.addEventListener("click", (ev) => { if (ev.target === o || ev.target.closest(".pic-close")) closeFigure(); });
        document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") closeFigure(); });
    }
    const title = escapeHtml(p.caption || p.file || p.digest.slice(0, 12));
    const kind = figKind(p);
    const at = p.via ? figPath(p.via.project, p.via.key, p.digest) : "";
    // the bytes come through a live fact only: history alone cannot show them
    const face = p.missing ? `<div class="empty">the bytes are gone from the folder; the facts still cite it</div>`
               : !p.via ? `<div class="empty">only history cites this figure; the bytes are served through a live fact</div>`
               : STILL.has(p.mime) ? `<img alt="${title}" data-fig="${at}">`
               : kind === "video" ? `<video controls data-fig="${at}"></video>`
               : kind === "audio" ? `<audio controls data-fig="${at}"></audio>`
               : `<a class="btn" target="_blank" rel="noopener" data-fig="${at}">open the ${escapeHtml(kind)}</a>`;
    const from = p.source ? `<span title="${escapeHtml(p.source)}">from ${escapeHtml(p.source.split(/[\\/]/).pop())}</span>` : "";
    const facts = [p.mime, human(p.bytes), p.kept_at ? `kept ${relAge(p.kept_at)}` : "", `${p.digest.slice(0, 12)}…`]
                  .filter(Boolean).map(f => `<span>${escapeHtml(f)}</span>`).join("") + from;
    const n = (p.facts || []).length;
    o.innerHTML = `
        <div class="pic-card" role="dialog" aria-label="${title || "figure"}">
            <div class="pic-head">
                <span class="pic-title">${title}</span>
                <button class="pic-close" aria-label="close">✕</button>
            </div>
            <div class="face">${face}</div>
            <div class="facts">${facts}</div>
            <h3 class="group-h">cited by ${n} fact${n === 1 ? "" : "s"}</h3>
            <div class="stack">${(p.facts || []).map(citingCard).join("")}</div>
        </div>`;
    o.classList.add("show");
    o.querySelectorAll("[data-hk]").forEach(b => b.onclick = () => { closeFigure(); gotoHistory(b.dataset.hp, b.dataset.hk); });
    hydrateFigures(o);
}
function closeFigure() { const o = document.getElementById("pic-overlay"); if (o) o.classList.remove("show"); }

// A click on a tile opens the gallery's entry; a click on a strip's figure
// opens the gallery's entry when it is loaded, else one built from the strip
// and the fact it hangs on.
document.addEventListener("click", (ev) => {
    const t = ev.target.closest(".tile[data-pic]");
    if (t) { openFigure(_figGallery[parseInt(t.dataset.pic, 10)]); return; }
    const s = ev.target.closest("[data-open-fig]");
    if (!s) return;
    const digest = s.dataset.openFig;
    const known = _figGallery.find(p => p.digest === digest);
    if (known) { openFigure(known); return; }
    const fact = [...factsCache, ..._histCache].find(f => f.id === s.dataset.fact);
    const g = fact ? (fact.figures || []).find(x => x.digest === digest) : null;
    const live = !fact || !fact.superseded_at;
    openFigure({ digest, mime: g?.mime, bytes: g?.bytes, caption: g?.caption, source: g?.source, kept_at: g?.kept_at,
                 file: g?.file, missing: false,
                 via: live ? { project: s.dataset.project, key: s.dataset.key } : null,
                 facts: fact ? [{ id: fact.id, project: fact.project, key: fact.key, value: fact.value, why: fact.why,
                                  live, created_at: fact.created_at }] : [] });
});

// ---- backups: the same panel on every Seren viewer --------------------------
// Reads /stores (seren_sinew.stores): what is kept, the snapshots, when a
// Lodestar last pulled, how the last rehearsal went. Counts and times only.
// Kept parallel with Memory's and Margin's copy on purpose: fix one, fix all.
function renderBackups(el, d) {
    if (!d || !d.ok) { el.innerHTML = `<div class="empty">this service keeps no snapshots (backup.enabled is off)</div>`; return; }
    const s = d.snapshots || {};
    const latest = s.latest ? `${escapeHtml(s.latest.id)} · ${relAge(s.latest.created_at)} · ${escapeHtml(s.latest.reason || "")}` : "none yet";
    const pull = s.last_pull ? `${relAge(s.last_pull.created_at)} (${escapeHtml(s.last_pull.id)})` : "never from this box";
    const r = d.last_rehearsal;
    const reh = r ? `<span class="${r.ok ? "ok" : "bad"}">${r.ok ? "ok" : "FAILED"}</span> · ${relAge(r.at)} · ${escapeHtml(r.snapshot)} (${escapeHtml(r.source)}${r.problems ? `, ${r.problems} problem${r.problems === 1 ? "" : "s"}` : ""})`
                  : "none since this process started";
    const stores = (d.stores || []).map(st =>
        `<div class="row"><span class="k">${escapeHtml(st.name)}</span><span class="v">${escapeHtml(st.kind)} · ${human(st.bytes)}${st.backed_up ? "" : " · not copied"}${st.exists ? "" : " · nothing on disk yet"}</span></div>`).join("");
    el.innerHTML = `
      <div class="row"><span class="k">snapshots</span><span class="v">${s.count ?? 0} kept · newest ${s.keep_daily} daily, then one a week for ${s.keep_weekly} weeks</span></div>
      <div class="row"><span class="k">newest</span><span class="v">${latest}</span></div>
      <div class="row"><span class="k">last pull</span><span class="v">${pull}</span></div>
      <div class="row"><span class="k">last rehearsal</span><span class="v">${reh}</span></div>
      ${stores}`;
}
async function loadBackups() {
    const el = document.getElementById("backups");
    if (!el) return;
    try { renderBackups(el, await api("/stores")); }
    catch (e) { el.innerHTML = `<div class="empty">${escapeHtml(e.message)}</div>`; }
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
    hydrateFigures(el);
}

// why a retired value stopped being the answer, when whoever retired it said
function changedLine(f) {
    return f.why_changed ? `<div class="why"><b>why it changed</b> · ${escapeHtml(f.why_changed)}</div>` : "";
}

function factCard(f) {
    const dim = f.superseded_at ? " dim" : "";
    const why = (f.why ? `<div class="why"><b>why</b> · ${escapeHtml(f.why)}</div>` : "") + changedLine(f);
    const supTag = f.superseded_at ? `<span class="tag superseded">superseded</span>` : "";
    // a superseded row's figures are history: the live value's are served, the rest are counted
    const figs = f.superseded_at ? (f.figures && f.figures.length ? `<div class="figs"><span class="fig chip">${f.figures.length} figure${f.figures.length === 1 ? "" : "s"} in history</span></div>` : "")
                                 : figuresStrip(f);
    return `<div class="card${dim}">
        <div class="k">${escapeHtml(f.key)}</div>
        <div class="v">${escapeHtml(f.value)}</div>
        ${why}
        ${figs}
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
    const why = (h.why ? `<div class="why"><b>why</b> · ${escapeHtml(h.why)}</div>` : "") + changedLine(h);
    const dist = (h.raw_distance != null) ? ` · d=${h.raw_distance}` : "";
    const figs = h.figures ? ` · ${h.figures} figure${h.figures === 1 ? "" : "s"}` : "";
    return `<div class="card"><div class="hit">
        <div class="score">
          <span class="num">${(h.score ?? 0).toFixed(2)}</span>
          <span class="badge ${escapeHtml(h.match_kind)}">${escapeHtml(h.match_kind)}</span>
        </div>
        <div>
          <div class="k">${escapeHtml(h.key)}</div>
          <div class="v">${escapeHtml(h.value)}</div>
          ${why}
          <div class="foot"><span>${escapeHtml(h.project)}</span><span>${pct}%${dist}${figs}</span></div>
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
        _histCache = data.history;
        el.innerHTML = data.history.map((f, i) => {
            const live = !f.superseded_at;
            const tag = live ? `<span class="tag" style="color:var(--accent);border-color:var(--accent-dim)">live</span>`
                : `<span class="tag superseded">superseded</span>`;
            const why = (f.why ? `<div class="why"><b>why</b> · ${escapeHtml(f.why)}</div>` : "") + changedLine(f);
            const figs = live ? figuresStrip(f)
                : (f.figures && f.figures.length ? `<div class="figs"><span class="fig chip">${f.figures.length} figure${f.figures.length === 1 ? "" : "s"} kept with this value</span></div>` : "");
            // a row from before a move keeps the address it was written under
            const where = (f.project !== data.project || f.key !== data.key)
                ? `<span class="tag">was ${escapeHtml(f.project)} / ${escapeHtml(f.key)}</span>` : "";
            return `<div class="card${live ? "" : " dim"}">
            <div class="foot" style="margin:0 0 8px"><span>#${data.count - i}</span>${tag}${where}<span>${relAge(f.created_at)}</span></div>
            <div class="v">${escapeHtml(f.value)}</div>
            ${why}
            ${figs}
          </div>`;
        }).join("");
        hydrateFigures(el);
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
          ${stat(c.projects, "chambers", false)}
          ${stat(c.figures ?? 0, "figures cited", false)}`;
        setFinder(root.finder);
        loadBackups();
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
        // The server says "hybrid" (vector + FTS5 fused); "vector" is the name an
        // older build used. The pill went cold on every real deployment because
        // this compared against the old word only.
        document.getElementById("finderNote").innerHTML = (root.finder === "hybrid" || root.finder === "vector")
            ? `Finder: <b style="color:var(--accent)">hybrid</b> - a sqlite-vec index is live beside FTS5, so the associative jump (“that CUDA thing”) works. Exact-key still leads at score 1.0.`
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
    b.className = "head-pill" + ((finder === "hybrid" || finder === "vector") ? " hot" : "");
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
    if (tab === "figures") loadFigures();
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
    if (document.getElementById("figures").classList.contains("active")) loadFigures();
}

// ---- boot -------------------------------------------------------------------
async function boot() {
    try { const root = await api("/"); setFinder(root.finder); }
    catch (e) { showErr(e.message); }
    loadFacts();
}
boot();
