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
    document.body.classList.toggle("sky-on", tab === "skyline");
    if (tab !== "skyline") skyPause();
    if (tab === "overview") loadOverview();
    if (tab === "figures") loadFigures();
    if (tab === "skyline") loadSkyline().catch(e => { document.getElementById("sky-loading").textContent = "⚠ " + e.message; });
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
    if (document.getElementById("skyline").classList.contains("active")) loadSkyline().catch(() => {});
}

// ---- boot -------------------------------------------------------------------
async function boot() {
    try { const root = await api("/"); setFinder(root.finder); }
    catch (e) { showErr(e.message); }
    loadFacts();
}
boot();

// ---- the City (23 Oct 2026; the tab was "Skyline" until the 24th, when the
// Callosum's sky took that name) ------------------------------------------
// Loci as a city, the user's idea: "TOWERS EACH FACT IS A LEVEL, LEVELS ARE SIZED
// BY SUPERSEDES AND CITATIONS CONNECT TO OTHER TOWER LEVELS" - after the
// Minneapolis skyways he walked between class and the bar all winter. Memory's
// Map is a nebula; this hemisphere is rigid, so it gets steel and glass.
// A tower per project ('*' at the centre, gold), a floor per live fact stacked
// in the order its address was first written, a floor as wide as the times it
// changed and the facts tied to it. A skyway per citation, with lights walking
// from the fact toward the one it rests on; amber where something under a fact
// changed. GET /city has it all. three.js r159 is vendored and served from
// /viewer/vendor/.
const SKY = {
    lib: null, built: false, running: false, last: 0, t: 0,
    renderer: null, scene: null, camera: null, city: null, data: null,
    floors: [], meshes: [], byAddr: new Map(), ways: [], walkers: [], beacons: [], snow: null, extent: 200, height: 100,
    orbit: { x: 0, y: 30, z: 0, r: 600, th: 0.75, ph: 1.08 },
    goal: { x: 0, y: 30, z: 0, r: 600, th: 0.75, ph: 1.08 },
    drift: !matchMedia("(prefers-reduced-motion: reduce)").matches, snowOn: true, waysOn: true,
    selected: null, near: null, lit: null, recheckAt: -1,
};
const SKY_FLOOR = 3.2, SKY_GAP = 0.55, SKY_BLOCK = 66;
const skyAddr = (p, k) => p + "\u0000" + k;

function skyLib() {
    if (window.THREE) return Promise.resolve();
    if (!SKY.lib) {
        SKY.lib = apiBlob("/viewer/vendor/three.min.js").then(url => new Promise((ok, bad) => {
            const s = document.createElement("script");
            s.src = url;
            s.onload = () => ok();
            s.onerror = () => bad(new Error("the 3D library did not load"));
            document.head.appendChild(s);
        }));
        SKY.lib.catch(() => { SKY.lib = null; });
    }
    return SKY.lib;
}

function skyFrame() {
    const bar = document.querySelector(".tabbar") || document.querySelector("header");
    if (bar) document.getElementById("sky-wrap").style.top = Math.round(bar.getBoundingClientRect().bottom) + "px";
}

// the blocks: a square spiral out from the centre
function skyCells(n) {
    const out = [[0, 0]];
    let x = 0, y = 0, dx = 1, dy = 0, seg = 1, steps = 0, turns = 0;
    while (out.length < n) {
        x += dx; y += dy; out.push([x, y]);
        if (++steps === seg) { steps = 0; [dx, dy] = [-dy, dx]; if (++turns % 2 === 0) seg++; }
    }
    return out;
}

function skyWidth(f) {
    return Math.min(30, 11 + 3 * Math.sqrt(f.history) + 1.6 * (f.rests_on + f.rested_on_by));
}

// lit windows on black, a few variants so no two floors read alike
function skyWindows(seed) {
    const c = document.createElement("canvas");
    c.width = 256; c.height = 32;
    const x = c.getContext("2d");
    x.fillStyle = "#000"; x.fillRect(0, 0, 256, 32);
    let s = seed * 9301 + 49297;
    const rnd = () => ((s = (s * 9301 + 49297) % 233280) / 233280);
    for (let i = 0; i < 16; i++) {
        const lit = rnd();
        if (lit < 0.28) continue;
        const v = 0.45 + 0.55 * rnd();
        x.fillStyle = lit < 0.42 ? `rgba(160,200,255,${v})` : `rgba(255,${200 + Math.floor(40 * rnd())},150,${v})`;
        x.fillRect(i * 16 + 4, 9, 9, 15);
    }
    const t = new THREE.CanvasTexture(c);
    t.colorSpace = THREE.SRGBColorSpace;
    t.wrapS = THREE.RepeatWrapping;
    return t;
}

function skyLabel(text, hue) {
    const c = document.createElement("canvas");
    c.width = 512; c.height = 96;
    const x = c.getContext("2d");
    x.font = "600 44px ui-sans-serif, system-ui, sans-serif";
    x.textAlign = "center"; x.textBaseline = "middle";
    x.shadowColor = "rgba(0,0,0,0.9)"; x.shadowBlur = 12;
    x.fillStyle = `hsl(${hue}, 85%, 78%)`;
    x.fillText(text, 256, 48);
    const t = new THREE.CanvasTexture(c);
    t.colorSpace = THREE.SRGBColorSpace;
    const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: t, transparent: true, depthWrite: false }));
    s.scale.set(44, 8.25, 1);
    return s;
}

function skyBuild() {
    const wrap = document.getElementById("sky-canvas");
    const r = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    r.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    r.setClearColor(0x000000, 0);
    wrap.appendChild(r.domElement);
    const scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(0x0b1022, 0.0011);
    const cam = new THREE.PerspectiveCamera(45, 1, 1, 6000);
    scene.add(new THREE.HemisphereLight(0x8fa8ff, 0x0b0d18, 0.55));
    const moon = new THREE.DirectionalLight(0xcfe0ff, 0.9);
    moon.position.set(-300, 500, 200);
    scene.add(moon);
    scene.add(new THREE.AmbientLight(0x404a70, 0.35));
    Object.assign(SKY, { renderer: r, scene, camera: cam, built: true });
    skyWire(r.domElement);
    new ResizeObserver(() => {
        const w = wrap.clientWidth, h = wrap.clientHeight;
        if (!w || !h) return;
        r.setSize(w, h);
        cam.aspect = w / h;
        cam.updateProjectionMatrix();
    }).observe(wrap);
}

function skyCity(data) {
    if (SKY.city) { SKY.scene.remove(SKY.city); SKY.city.traverse(o => { o.geometry && o.geometry.dispose(); }); }
    const city = new THREE.Group();
    SKY.city = city; SKY.floors = []; SKY.meshes = []; SKY.byAddr = new Map(); SKY.ways = []; SKY.walkers = []; SKY.beacons = [];
    const order = [...data.towers].sort((a, b) => {
        const rank = p => p.project === "*" ? 0 : p.project === "seren" ? 1 : p.project === "wren" ? 2 : 3;
        return rank(a) - rank(b) || b.floors.length - a.floors.length || a.project.localeCompare(b.project);
    });
    // where each tower stands: the place a hand moved it to (skyGrab, kept
    // in this browser), else the next free cell of the spiral
    const placed = SKY.place || {};
    const taken = new Set(Object.values(placed).map(c => c.join(",")));
    const free = skyCells(order.length + taken.size).filter(c => !taken.has(c.join(",")));
    const cells = order.map(tw => placed[tw.project] || free.shift());
    SKY.cellOf = {};
    const wins = [0, 1, 2, 3, 4, 5].map(skyWindows);
    let maxH = 0, ext = 0, k = 0;
    order.forEach((tw, i) => {
        const hue = tw.project === "*" ? 44 : tw.project === "wren" ? 345 : (190 + (k++) * 137.508) % 360;
        const [cx, cz] = cells[i].map(v => v * SKY_BLOCK);
        SKY.cellOf[tw.project] = cells[i];
        ext = Math.max(ext, Math.abs(cx), Math.abs(cz));
        const tower = { project: tw.project, hue, x: cx, z: cz, floors: [] };
        tw.floors.forEach((f, lvl) => {
            const w = skyWidth(f);
            const y = lvl * (SKY_FLOOR + SKY_GAP) + SKY_FLOOR / 2;
            const tex = wins[(lvl * 7 + i * 3) % wins.length].clone();
            tex.repeat.set(Math.max(2, Math.round(w / 1.9)) / 16, 1);
            tex.needsUpdate = true;
            const base = new THREE.Color().setHSL(hue / 360, 0.32, f.recheck ? 0.2 : 0.17);
            const glow = new THREE.Color().setHSL(hue / 360, 0.75, 0.68);
            const side = new THREE.MeshStandardMaterial({ color: base, emissive: glow, emissiveMap: tex,
                emissiveIntensity: 1.1, roughness: 0.5, metalness: 0.35 });
            const cap = new THREE.MeshStandardMaterial({ color: base, roughness: 0.7, metalness: 0.2 });
            const m = new THREE.Mesh(new THREE.BoxGeometry(w, SKY_FLOOR, w), [side, side, cap, cap, side, side]);
            m.position.set(cx, y, cz);
            const floor = { ...f, project: tw.project, level: lvl, w, y, x: cx, z: cz, hue, mesh: m, side, glow,
                            ways: [], baseI: 1.1 };
            m.userData.floor = floor;
            city.add(m);
            SKY.meshes.push(m);
            SKY.floors.push(floor);
            SKY.byAddr.set(skyAddr(tw.project, f.key), floor);
            tower.floors.push(floor);
        });
        const top = tw.floors.length * (SKY_FLOOR + SKY_GAP);
        maxH = Math.max(maxH, top);
        const mast = new THREE.Mesh(new THREE.CylinderGeometry(0.35, 0.5, 9, 6),
                                    new THREE.MeshStandardMaterial({ color: 0x2a3048, roughness: 0.8 }));
        mast.position.set(cx, top + 4.5, cz);
        city.add(mast);
        const warn = tw.floors.some(f => f.recheck);
        const beacon = new THREE.Mesh(new THREE.SphereGeometry(1.1, 10, 8),
                                      new THREE.MeshBasicMaterial({ color: warn ? 0xffb347 : 0xff3b3b }));
        beacon.position.set(cx, top + 9.5, cz);
        city.add(beacon);
        SKY.beacons.push({ mesh: beacon, phase: Math.random() * 6, warn });
        const label = skyLabel(tw.project === "*" ? "* fundamentals" : tw.project, hue);
        label.position.set(cx, top + 17, cz);
        city.add(label);
    });
    // the streets between the blocks, and no ground under them: a solid plane
    // hid half the night, and the city reads better standing on its grid in
    // the dark (24 Oct 2026: "drop the ground plane")
    const size = (Math.ceil(ext / SKY_BLOCK) * 2 + 3) * SKY_BLOCK;
    const grid = new THREE.GridHelper(size, size / SKY_BLOCK, 0x2a3358, 0x1a2140);
    grid.position.set(SKY_BLOCK / 2, 0.05, SKY_BLOCK / 2);
    city.add(grid);
    skyWays(data.links, city);
    skySnow(city, size, maxH);
    SKY.scene.add(city);
    SKY.extent = ext + SKY_BLOCK / 2;
    SKY.height = maxH;
}

function skyWays(links, city) {
    const glass = () => new THREE.MeshStandardMaterial({ color: 0x1c2232, emissive: 0xffcf7a, emissiveIntensity: 0.45,
                                                         roughness: 0.3, metalness: 0.4, transparent: true, opacity: 0.7 });
    const walkerGeo = new THREE.SphereGeometry(0.45, 8, 6);
    for (const lk of links) {
        const a = SKY.byAddr.get(skyAddr(lk.project, lk.key)), b = SKY.byAddr.get(skyAddr(lk.on_project, lk.on_key));
        if (!a || !b) continue;
        let curve, mesh;
        const mat = glass();
        if (a.project === b.project) {
            // the same tower: a bridge on the outside, from one floor to the other
            const out = Math.max(a.w, b.w) / 2 + 3 + (SKY.ways.filter(w => w.a.project === a.project).length % 4) * 1.6;
            const ang = (a.level * 0.9) % (Math.PI * 2);
            const ox = Math.cos(ang), oz = Math.sin(ang);
            const p0 = new THREE.Vector3(a.x + ox * a.w / 2, a.y, a.z + oz * a.w / 2);
            const p2 = new THREE.Vector3(b.x + ox * b.w / 2, b.y, b.z + oz * b.w / 2);
            const p1 = new THREE.Vector3(a.x + ox * out * 1.6, (a.y + b.y) / 2, a.z + oz * out * 1.6);
            curve = new THREE.QuadraticBezierCurve3(p0, p1, p2);
            mesh = new THREE.Mesh(new THREE.TubeGeometry(curve, 28, 0.5, 6, false), mat);
        } else {
            const d = new THREE.Vector3(b.x - a.x, 0, b.z - a.z).normalize();
            const p0 = new THREE.Vector3(a.x + d.x * a.w / 2, a.y, a.z + d.z * a.w / 2);
            const p1 = new THREE.Vector3(b.x - d.x * b.w / 2, b.y, b.z - d.z * b.w / 2);
            curve = new THREE.LineCurve3(p0, p1);
            const len = p0.distanceTo(p1);
            mesh = new THREE.Mesh(new THREE.BoxGeometry(1.25, 1.05, len), mat);
            mesh.position.copy(p0).add(p1).multiplyScalar(0.5);
            mesh.lookAt(p1);
        }
        city.add(mesh);
        const way = { a, b, curve, mesh, mat, len: curve.getLength(), why: lk.why };
        a.ways.push(way); b.ways.push(way);
        SKY.ways.push(way);
        for (let i = 0; i < 2; i++) {
            const w = new THREE.Mesh(walkerGeo, new THREE.MeshBasicMaterial({ color: 0xfff1c9 }));
            city.add(w);
            SKY.walkers.push({ mesh: w, way, t: i / 2 + Math.random() * 0.2 });
        }
    }
}

function skySnow(city, size, h) {
    const n = 2600, pos = new Float32Array(n * 3), speed = new Float32Array(n);
    for (let i = 0; i < n; i++) {
        pos[i * 3] = (Math.random() - 0.5) * size * 1.4;
        pos[i * 3 + 1] = Math.random() * (h + 120);
        pos[i * 3 + 2] = (Math.random() - 0.5) * size * 1.4;
        speed[i] = 4 + Math.random() * 7;
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    const c = document.createElement("canvas");
    c.width = c.height = 32;
    const x = c.getContext("2d"), grd = x.createRadialGradient(16, 16, 0, 16, 16, 16);
    grd.addColorStop(0, "rgba(255,255,255,1)"); grd.addColorStop(1, "rgba(255,255,255,0)");
    x.fillStyle = grd; x.fillRect(0, 0, 32, 32);
    const flakes = new THREE.Points(g, new THREE.PointsMaterial({ size: 1.6, map: new THREE.CanvasTexture(c),
        transparent: true, opacity: 0.85, depthWrite: false, color: 0xe8f0ff }));
    flakes.visible = SKY.snowOn;
    city.add(flakes);
    SKY.snow = { points: flakes, speed, top: h + 120 };
}

// ---- looking around: the family's three, as on Memory's Stars (24 Oct 2026) -----
// Left-drag turns around the middle of the view, right-drag (or shift-drag)
// slides it across the screen, the wheel or a pinch zooms; the middle button
// is for moving a tower (skyGrab). A hand's move is followed at once - only the camera's own moves
// (fit, a chosen thing) ease there. Easing the hand too is what made a drag
// feel like the camera rumbled instead of turning.
function skyWire(el) {
    let down = null, moved = 0;
    const pts = new Map();
    const now = (...keys) => { for (const k of keys) SKY.orbit[k] = SKY.goal[k]; };
    el.addEventListener("contextmenu", e => e.preventDefault());
    el.addEventListener("mousedown", e => { if (e.button === 1) e.preventDefault(); });   // no autoscroll
    el.addEventListener("pointerdown", e => {
        if (e.button === 1) { skyGrab(e); return; }
        el.setPointerCapture(e.pointerId);
        pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
        down = { pan: e.button === 2 || e.shiftKey };
        moved = 0;
        if (SKY.drift) { SKY.drift = false; document.getElementById("sky-drift").checked = false; }
    });
    el.addEventListener("pointermove", e => {
        const prev = pts.get(e.pointerId);
        if (!prev) { skyHover(e); return; }
        const dx = e.clientX - prev.x, dy = e.clientY - prev.y;
        moved += Math.abs(dx) + Math.abs(dy);
        const g = SKY.goal;
        if (pts.size === 2) {
            const [p, q] = [...pts.entries()];
            const other = p[0] === e.pointerId ? q[1] : p[1];
            const before = Math.hypot(prev.x - other.x, prev.y - other.y);
            const after = Math.hypot(e.clientX - other.x, e.clientY - other.y);
            if (before > 0) { g.r = Math.min(2400, Math.max(30, g.r * before / after)); now("r"); }
        } else if (down && down.pan) {
            // slide along the screen: what is under the cursor stays under it
            const s = 2 * g.r * Math.tan(SKY.camera.fov * Math.PI / 360) / Math.max(1, el.clientHeight);
            const ct = Math.cos(g.th), st = Math.sin(g.th), cp = Math.cos(g.ph), sp = Math.sin(g.ph);
            g.x += (-ct * dx - cp * st * dy) * s;
            g.y += sp * dy * s;
            g.z += (st * dx - cp * ct * dy) * s;
            now("x", "y", "z");
        } else {
            g.th -= dx * 0.005;
            g.ph = Math.min(1.48, Math.max(0.12, g.ph - dy * 0.004));
            now("th", "ph");
        }
        pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
    });
    const up = e => {
        const was = pts.get(e.pointerId);
        pts.delete(e.pointerId);
        if (was && moved < 6 && pts.size === 0) skyPick(e);
        if (!pts.size) down = null;
    };
    el.addEventListener("pointerup", up);
    el.addEventListener("pointercancel", e => { pts.delete(e.pointerId); down = null; });
    el.addEventListener("pointerleave", () => { document.getElementById("sky-tip").hidden = true; });
    el.addEventListener("wheel", e => {
        e.preventDefault();
        SKY.goal.r = Math.min(2400, Math.max(30, SKY.goal.r * Math.exp(e.deltaY * 0.0011)));
        now("r");
    }, { passive: false });
}

// Moving a tower: the middle button held on any of its floors, a footprint
// follows the cursor across the grid, and letting go stands it there; a tower
// already in that cell trades places with it. Kept in this browser; ⟲ puts
// them all back. 24 Oct 2026: "MMB down to move a floor (will be especially
// useful when more skyways exist)".
const PLACE_KEY = "seren-loci-city-places";
function skyPlacesLoad() {
    try { SKY.place = JSON.parse(localStorage.getItem(PLACE_KEY) || "{}") || {}; } catch (_) { SKY.place = {}; }
}
function skyPlacesSave() {
    try { localStorage.setItem(PLACE_KEY, JSON.stringify(SKY.place)); } catch (_) { /* this visit only, then */ }
    document.getElementById("sky-unplace").hidden = !Object.keys(SKY.place).length;
}
function skyGround(e) {
    const rect = SKY.renderer.domElement.getBoundingClientRect();
    const v = new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
    const ray = new THREE.Raycaster();
    ray.setFromCamera(v, SKY.camera);
    return ray.ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0, 1, 0), 0), new THREE.Vector3());
}
function skyGrab(e) {
    const f = skyRay(e);
    if (!f || !SKY.data) return;
    e.preventDefault();
    const el = SKY.renderer.domElement;
    el.setPointerCapture(e.pointerId);
    const ghost = SKY.ghost || (SKY.ghost = new THREE.Mesh(new THREE.BoxGeometry(SKY_BLOCK * 0.62, 1.2, SKY_BLOCK * 0.62),
        new THREE.MeshBasicMaterial({ color: 0xffcf7a, transparent: true, opacity: 0.4, depthWrite: false })));
    SKY.scene.add(ghost);
    let cell = null;
    const move = ev => {
        const p = skyGround(ev);
        if (!p) return;
        cell = [Math.round(p.x / SKY_BLOCK), Math.round(p.z / SKY_BLOCK)];
        ghost.position.set(cell[0] * SKY_BLOCK, 0.6, cell[1] * SKY_BLOCK);
    };
    const done = () => {
        el.removeEventListener("pointermove", move);
        el.removeEventListener("pointerup", done);
        el.removeEventListener("pointercancel", done);
        SKY.scene.remove(ghost);
        if (cell) skyMoveTower(f.project, cell);
    };
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", done);
    el.addEventListener("pointercancel", done);
    move(e);
}
function skyMoveTower(project, cell) {
    const from = SKY.cellOf[project];
    if (!from || (from[0] === cell[0] && from[1] === cell[1])) return;
    const other = Object.keys(SKY.cellOf).find(p => p !== project && SKY.cellOf[p][0] === cell[0] && SKY.cellOf[p][1] === cell[1]);
    SKY.place[project] = cell;
    if (other) SKY.place[other] = from;
    skyPlacesSave();
    const keep = SKY.selected && skyAddr(SKY.selected.project, SKY.selected.key);
    skyCity(SKY.data);
    SKY.selected = null; SKY.near = null;
    skyLight(document.getElementById("sky-search").value);
    if (keep && SKY.byAddr.has(keep)) skySelect(SKY.byAddr.get(keep));
}

function skyAmberSay() {
    const n = SKY.floors.filter(f => f.recheck).length;
    document.getElementById("lg-amber-n").textContent = n ? `${n} to read again` : "none right now";
}

function skyRay(e) {
    const rect = SKY.renderer.domElement.getBoundingClientRect();
    const v = new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
    const ray = new THREE.Raycaster();
    ray.setFromCamera(v, SKY.camera);
    const hit = ray.intersectObjects(SKY.meshes, false)[0];
    return hit ? hit.object.userData.floor : null;
}

function skyHover(e) {
    const tip = document.getElementById("sky-tip");
    const f = SKY.built && SKY.floors.length ? skyRay(e) : null;
    SKY.renderer.domElement.style.cursor = f ? "pointer" : "";
    if (!f) { tip.hidden = true; return; }
    const v = (f.value || "").length > 160 ? f.value.slice(0, 160) + "…" : f.value;
    tip.innerHTML = `<div class="st-addr">${escapeHtml(f.project)} / <b>${escapeHtml(f.key)}</b></div>` +
        `<div class="st-v">${escapeHtml(v || "")}</div>` +
        (f.recheck ? `<div class="st-warn">something under it changed</div>` : "");
    const rect = document.getElementById("sky-wrap").getBoundingClientRect();
    tip.style.left = Math.min(e.clientX - rect.left + 14, rect.width - 320) + "px";
    tip.style.top = (e.clientY - rect.top + 14) + "px";
    tip.hidden = false;
}

function skyPick(e) { skySelect(skyRay(e)); }

function skyLook() {
    const near = SKY.near, lit = SKY.lit;
    for (const f of SKY.floors) {
        let i = f.baseI, col = f.glow;
        if (f.recheck) col = new THREE.Color(0xffb347);
        if (lit) { i = lit.has(f) ? 1.8 : 0.18; if (lit.has(f)) col = new THREE.Color(0xffe08a); }
        if (near) i = near.has(f) ? (f === SKY.selected ? 2.2 : 1.6) : 0.15;
        f.side.emissive.copy(col);
        f.side.emissiveIntensity = i;
        f.lit = i;
    }
    for (const w of SKY.ways) {
        const on = !near || near.has(w.a) && near.has(w.b) && (w.a === SKY.selected || w.b === SKY.selected);
        w.mesh.visible = SKY.waysOn;
        w.mat.opacity = on ? (near ? 0.9 : 0.7) : 0.08;
        w.mat.emissiveIntensity = on ? (near ? 1.2 : 0.35) : 0.05;
        w.on = on;
    }
}

function skySelect(f) {
    SKY.selected = f;
    const info = document.getElementById("sky-info");
    if (!f) { SKY.near = null; info.hidden = true; skyLook(); return; }
    SKY.near = new Set([f, ...f.ways.map(w => w.a), ...f.ways.map(w => w.b)]);
    skyLook();
    Object.assign(SKY.goal, { x: f.x, y: f.y, z: f.z, r: Math.max(70, 40 + f.w * 3.2) });
    const rests = f.ways.filter(w => w.a === f), by = f.ways.filter(w => w.b === f);
    const ref = (g, why) => `<li><button class="si-ref" data-p="${escapeHtml(g.project)}" data-k="${escapeHtml(g.key)}">` +
        `<i style="background:hsl(${g.hue},80%,66%)"></i><span><b>${escapeHtml(g.project)}</b> / ${escapeHtml(g.key)}` +
        (why ? `<em>${escapeHtml(why)}</em>` : "") + `</span></button></li>`;
    info.innerHTML = `
        <button class="si-close" aria-label="Close" data-close>×</button>
        <div class="si-addr"><span class="si-proj" style="color:hsl(${f.hue},85%,72%)">${escapeHtml(f.project)}</span> · floor ${f.level + 1}</div>
        <div class="si-key">${escapeHtml(f.key)}</div>
        <div class="si-v">${escapeHtml(f.value || "")}</div>
        ${f.why ? `<div class="si-why"><span>why</span>${escapeHtml(f.why)}</div>` : ""}
        <div class="si-meta">first written ${escapeHtml(fmtTsSky(f.built_at))} · changed ${f.history} time${f.history === 1 ? "" : "s"} · ${escapeHtml(f.source || "")}
            <button class="si-hist" data-hist>history ↗</button></div>
        <div id="si-recheck"></div>
        ${rests.length ? `<h4>rests on</h4><ul>${rests.map(w => ref(w.b, w.why)).join("")}</ul>` : ""}
        ${by.length ? `<h4>what rests on it</h4><ul>${by.map(w => ref(w.a, w.why)).join("")}</ul>` : ""}`;
    info.hidden = false;
    info.scrollTop = 0;
    if (f.recheck) {
        api(`/fact/citations?project=${encodeURIComponent(f.project)}&key=${encodeURIComponent(f.key)}`).then(c => {
            if (SKY.selected !== f) return;
            document.getElementById("si-recheck").innerHTML = (c.recheck || []).map(r =>
                `<div class="si-warn"><b>${escapeHtml(r.on_project)} / ${escapeHtml(r.on_key)}</b> was ${escapeHtml(r.what)}` +
                (r.why_changed ? `: ${escapeHtml(r.why_changed)}` : "") + `</div>`).join("");
        }).catch(() => {});
    }
}

function fmtTsSky(ts) {
    const d = new Date((ts || 0) * 1000);
    return isNaN(d) ? "-" : d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

function skyFit() {
    const r = Math.max(SKY.extent * 2.3, SKY.height * 2.2, 160);
    Object.assign(SKY.goal, { x: SKY_BLOCK * 0.25, y: SKY.height * 0.32, z: SKY_BLOCK * 0.25, r });
}

function skyTick(now) {
    if (!SKY.running) return;
    requestAnimationFrame(skyTick);
    const dt = Math.min(0.05, (now - (SKY.last || now)) / 1000);
    SKY.last = now;
    SKY.t += dt;
    const o = SKY.orbit, g = SKY.goal, a = 1 - Math.exp(-dt * 6);
    if (SKY.drift) g.th += dt * 0.06;
    for (const k of ["x", "y", "z", "r", "th", "ph"]) o[k] += (g[k] - o[k]) * a;
    const c = SKY.camera;
    c.position.set(o.x + o.r * Math.sin(o.ph) * Math.sin(o.th), o.y + o.r * Math.cos(o.ph), o.z + o.r * Math.sin(o.ph) * Math.cos(o.th));
    c.lookAt(o.x, o.y, o.z);
    for (const w of SKY.walkers) {
        const show = SKY.waysOn && w.way.on;
        w.mesh.visible = show;
        if (!show) continue;
        w.t = (w.t + dt * 9 / Math.max(10, w.way.len)) % 1;
        w.mesh.position.copy(w.way.curve.getPoint(w.t));
    }
    for (const b of SKY.beacons) b.mesh.visible = Math.sin(SKY.t * (b.warn ? 5 : 2.2) + b.phase) > -0.2;
    const pulse = 0.75 + 0.45 * Math.sin(SKY.t * 3);
    if (!SKY.near && !SKY.lit) for (const f of SKY.floors) if (f.recheck) f.side.emissiveIntensity = 1.2 * pulse + 0.4;
    if (SKY.snow && SKY.snowOn) {
        const p = SKY.snow.points.geometry.attributes.position, s = SKY.snow.speed;
        for (let i = 0; i < s.length; i++) {
            let y = p.array[i * 3 + 1] - s[i] * dt;
            if (y < 0) y += SKY.snow.top;
            p.array[i * 3 + 1] = y;
            p.array[i * 3] += Math.sin(SKY.t * 0.7 + i) * dt * 0.8;
        }
        p.needsUpdate = true;
    }
    SKY.renderer.render(SKY.scene, c);
}

function skyStats() {
    const c = SKY.data.counts;
    document.getElementById("sky-stats").textContent =
        `${c.towers} towers · ${c.floors} floors · ${c.skyways} skyways` + (SKY.lit ? ` · ${SKY.lit.size} lit` : "");
    const btn = document.getElementById("sky-recheck");
    btn.hidden = !c.recheck;
    btn.textContent = `${c.recheck} to recheck`;
}

function skyLight(q) {
    q = (q || "").trim().toLowerCase();
    SKY.lit = q ? new Set(SKY.floors.filter(f => [f.key, f.value, f.why, f.project].some(s => (s || "").toLowerCase().includes(q)))) : null;
    skyLook();
    skyStats();
}

let skyWired = false;
function skyControls() {
    if (skyWired) return;
    skyWired = true;
    document.getElementById("sky-drift").addEventListener("change", e => { SKY.drift = e.target.checked; });
    document.getElementById("sky-snow").addEventListener("change", e => {
        SKY.snowOn = e.target.checked;
        if (SKY.snow) SKY.snow.points.visible = SKY.snowOn;
    });
    document.getElementById("sky-ways").addEventListener("change", e => { SKY.waysOn = e.target.checked; skyLook(); });
    document.getElementById("sky-search").addEventListener("input", e => skyLight(e.target.value));
    document.getElementById("sky-fit").addEventListener("click", () => { skySelect(null); skyFit(); });
    document.getElementById("sky-unplace").addEventListener("click", () => {
        SKY.place = {};
        skyPlacesSave();
        if (SKY.data) { skyCity(SKY.data); skyLight(document.getElementById("sky-search").value); }
    });
    document.getElementById("sky-recheck").addEventListener("click", () => {
        const due = SKY.floors.filter(f => f.recheck);
        if (!due.length) return;
        SKY.recheckAt = (SKY.recheckAt + 1) % due.length;
        skySelect(due[SKY.recheckAt]);
    });
    document.getElementById("sky-info").addEventListener("click", e => {
        if (e.target.closest("[data-close]")) return skySelect(null);
        if (e.target.closest("[data-hist]") && SKY.selected) return gotoHistory(SKY.selected.project, SKY.selected.key);
        const ref = e.target.closest(".si-ref");
        if (ref) skySelect(SKY.byAddr.get(skyAddr(ref.dataset.p, ref.dataset.k)) || null);
    });
    document.addEventListener("keydown", e => {
        if (e.key === "Escape" && document.body.classList.contains("sky-on")) skySelect(null);
    });
    window.addEventListener("resize", () => { if (document.body.classList.contains("sky-on")) skyFrame(); });
}

async function loadSkyline() {
    window.scrollTo(0, 0);
    skyFrame();
    const loading = document.getElementById("sky-loading");
    loading.hidden = false;
    loading.textContent = "raising the city…";
    const [, data] = await Promise.all([skyLib(), api("/city")]);
    if (!SKY.built) skyBuild();
    if (!SKY.place) skyPlacesLoad();
    skyControls();
    document.getElementById("sky-unplace").hidden = !Object.keys(SKY.place).length;
    document.getElementById("sky-drift").checked = SKY.drift;
    const keep = SKY.selected && skyAddr(SKY.selected.project, SKY.selected.key);
    SKY.data = data;
    skyCity(data);
    skyAmberSay();
    SKY.selected = null; SKY.near = null;
    skyLight(document.getElementById("sky-search").value);
    if (keep && SKY.byAddr.has(keep)) skySelect(SKY.byAddr.get(keep));
    else if (!SKY.fitted) { SKY.fitted = true; skyFit(); Object.assign(SKY.orbit, SKY.goal, { r: SKY.goal.r * 1.6 }); }
    loading.hidden = true;
    if (!SKY.running) { SKY.running = true; SKY.last = 0; requestAnimationFrame(skyTick); }
}

function skyPause() { SKY.running = false; }
