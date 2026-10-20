"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const state = { lang: "en", mode: "casual", data: null, scenarios: [], selected: null, upload: null,
                  until: 90, playing: false, firstSeen: {}, req: 0 };
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const seenKey = (m) => `${m.pattern}|${m.team}|${Math.round(m.start_min / 10)}`;
  const PATTERN = {
    dominance: "Sustained dominance", high_press: "High press", counterattack: "Counterattacking threat",
    momentum_swing: "Momentum swing", late_comeback: "Late comeback", low_event: "Low-event match",
  };
  const KIND = { observation: "Observation", interpretation: "Hypothesis", limitation: "Limitation", question: "Next question", recap: "Recap" };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (v) => (typeof v === "number" && !Number.isInteger(v) ? v.toFixed(1) : v);

  function teamClass(team) {
    if (!state.data) return "both";
    return team === state.data.meta.home ? "home" : team === state.data.meta.away ? "away" : "both";
  }
  function teamName(team) {
    const m = state.data.meta;
    return team === m.home ? (m.home_short || m.home_name) : team === m.away ? (m.away_short || m.away_name) : "Both teams";
  }

  async function init() {
    const res = await fetch("/api/scenarios");
    const js = await res.json();
    state.scenarios = js.scenarios;
    $("scenario").innerHTML = js.scenarios.map((s) => `<option value="${s.key}">${esc(s.title)}</option>`).join("");
    $("scenario").value = "momentum_swing";
    document.querySelectorAll("[data-lang]").forEach((b) => b.addEventListener("click", () => {
      state.lang = b.dataset.lang;
      document.querySelectorAll("[data-lang]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      run();
    }));
    document.querySelectorAll("[data-mode]").forEach((b) => b.addEventListener("click", () => {
      state.mode = b.dataset.mode;
      document.querySelectorAll("[data-mode]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      renderStory();
    }));
    $("run").addEventListener("click", () => { state.upload = null; stopReplay(); state.firstSeen = {}; run(); });
    $("scenario").addEventListener("change", () => { state.upload = null; stopReplay(); state.firstSeen = {}; setMinute(90); run(); });
    $("seed").addEventListener("change", () => { state.firstSeen = {}; });
    $("minute").addEventListener("input", () => { stopReplay(); setMinute(+$("minute").value); run(); });
    $("play").addEventListener("click", () => (state.playing ? stopReplay() : replay()));
    $("csv").addEventListener("change", onUpload);
    $("dlInsights").addEventListener("click", () => download("matchpulse_insights.json", state.data));
    $("dlOverlay").addEventListener("click", () => {
      const n = state.data?.narratives?.broadcast;
      download("matchpulse_broadcast_overlay.json", { disclaimer: state.data?.disclaimer, overlay: n ? n.overlay : [] });
    });
    loadEval();
    run();
  }

  function setMinute(m) {
    state.until = m;
    $("minute").value = m;
    $("minuteLabel").textContent = m >= 90 ? "Full time" : `Live ${m}'`;
  }

  function stopReplay() {
    state.playing = false;
    $("play").textContent = "Replay live";
    $("play").setAttribute("aria-pressed", "false");
  }

  async function replay() {
    if (state.upload) { banner("Live replay works on the synthetic scenarios.", false); return; }
    state.playing = true;
    $("play").textContent = "Pause";
    $("play").setAttribute("aria-pressed", "true");
    let m = state.until >= 90 ? 5 : state.until + 5;
    if (m === 5) state.firstSeen = {};
    for (; m <= 90 && state.playing; m += 5) {
      setMinute(m);
      await run();
      await sleep(matchMedia("(prefers-reduced-motion: reduce)").matches ? 1600 : 1100);
    }
    stopReplay();
  }

  function download(name, obj) {
    if (!obj) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(obj, null, 2)], { type: "application/json" }));
    const a = Object.assign(document.createElement("a"), { href: url, download: name });
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
  }

  function banner(msg, isError) {
    const b = $("banner");
    if (!msg) { b.hidden = true; return; }
    b.hidden = false; b.textContent = msg; b.classList.toggle("error", !!isError);
  }

  async function onUpload(ev) {
    const file = ev.target.files[0];
    if (!file) return;
    state.upload = await file.text();
    run();
  }

  async function run() {
    const btn = $("run");
    const ticket = ++state.req;
    btn.disabled = true; btn.textContent = "Running agents…";
    banner("");
    try {
      const common = { window_min: +$("window").value, lang: state.lang, writer: $("writer").value };
      if (!state.upload && state.until < 90) common.until_min = state.until;
      const res = state.upload
        ? await fetch("/api/analyze/upload", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...common, csv: state.upload }) })
        : await fetch("/api/analyze", { method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ...common, scenario: $("scenario").value, seed: +$("seed").value || 0 }) });
      const data = await res.json();
      if (ticket !== state.req) return;  // a newer request superseded this one
      if (data.status !== "ok") {
        const v = data.validation;
        const extra = v ? ` (${v.valid_rows ?? 0} valid of ${v.total_rows} rows; ${v.dropped_count} dropped)` : "";
        banner(`Analysis stopped: ${data.error || data.detail || "unknown error"}${extra}. Fix the file or pick a synthetic scenario.`, true);
        if (data.trace) renderTrace(data);
        return;
      }
      state.data = data; state.selected = null;
      const minute = data.analytics.until_min;
      (data.moments || []).forEach((m) => { const k = seenKey(m); if (!(k in state.firstSeen) && minute < 90) state.firstSeen[k] = minute; });
      const w = data.validation?.warnings || [];
      const notes = [];
      if (data.writer_errors && Object.keys(data.writer_errors).length) notes.push(`Model writer unavailable (${Object.values(data.writer_errors)[0]}). Showing verified template narratives.`);
      if (data.fallbacks?.length) notes.push(`Reviewer rejected the model draft for ${data.fallbacks.join(", ")} and used the safe template.`);
      if (w.length) notes.push(w.join(" "));
      if (state.upload && data.validation?.dropped_count) notes.push(`${data.validation.dropped_count} invalid rows were dropped.`);
      if (notes.length) banner(notes.join(" "), false);
      renderAll();
    } catch (e) {
      banner(`Could not reach the analysis service: ${e.message}. Check that the server is running.`, true);
    } finally {
      if (ticket === state.req) { btn.disabled = false; btn.textContent = "Analyse match"; }
    }
  }

  function renderAll() {
    const d = state.data, m = d.meta;
    $("homeName").textContent = m.home_name; $("awayName").textContent = m.away_name;
    $("homeScore").textContent = d.analytics.score[m.home]; $("awayScore").textContent = d.analytics.score[m.away];
    const live = d.analytics.live;
    $("clock").textContent = live ? `LIVE ${Math.round(d.analytics.until_min)}'` : "Full time";
    $("clock").classList.toggle("live", live);
    const sc = state.scenarios.find((s) => s.key === m.scenario);
    $("scenarioDesc").textContent = state.upload ? `Uploaded dataset ${m.match_id}` : `${sc ? sc.description : ""} Seed ${m.seed}.`;
    const q = `scenario=${encodeURIComponent($("scenario").value)}&seed=${+$("seed").value || 0}`;
    $("dlCsv").href = `/api/dataset?${q}&format=csv`; $("dlJson").href = `/api/dataset?${q}&format=json`;
    renderPulse(); renderMoments(); renderStats(); renderStory(); renderTrace(d); renderReview(); renderGT();
    $("evidence").hidden = true;
  }

  // ---------- pulse chart ----------
  function renderPulse() {
    const d = state.data, an = d.analytics, W = 900, H = 230, padL = 28, padR = 10, mid = 112, amp = 82;
    const x = (min) => padL + (min / 90) * (W - padL - padR);
    let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Momentum by window: bars above the line favour ${esc(d.meta.home_short)}, below favour ${esc(d.meta.away_short)}.">`;
    d.moments.forEach((mo) => {
      s += `<rect class="band" data-mid="${mo.id}" x="${x(mo.start_min)}" y="8" width="${Math.max(4, x(mo.end_min) - x(mo.start_min))}" height="${H - 40}" rx="4"><title>${esc(mo.id)} ${esc(PATTERN[mo.pattern])}</title></rect>`;
      s += `<text class="band-label" x="${x(mo.start_min) + 4}" y="22">${mo.id}</text>`;
    });
    if (an.live) {
      s += `<rect class="future" x="${x(an.until_min)}" y="8" width="${x(90) - x(an.until_min)}" height="${H - 40}"><title>Not played yet</title></rect>`;
    }
    s += `<line class="axis" x1="${padL}" x2="${W - padR}" y1="${mid}" y2="${mid}"/>`;
    if (an.live) {
      s += `<line class="now" x1="${x(an.until_min)}" x2="${x(an.until_min)}" y1="8" y2="${H - 32}"/>`;
      s += `<text class="now-label" x="${x(an.until_min) + 4}" y="${H - 36}">${Math.round(an.until_min)}'</text>`;
    }
    const bw = x(an.window_min) - x(0);
    an.momentum.forEach((w, i) => {
      const v = w.momentum, h = Math.abs(v) * amp, bx = x(w.start_min) + 1, width = Math.max(2, bw - 2);
      const y = v >= 0 ? mid - h : mid;
      const col = v >= 0 ? "var(--home)" : "var(--away)";
      if (h > 0.5) s += `<rect class="bar" x="${bx}" y="${y}" width="${width}" height="${h}" rx="3" fill="${col}"/>`;
      s += `<rect class="hit" data-i="${i}" x="${bx}" y="8" width="${width}" height="${H - 40}"/>`;
    });
    d.analytics.goals.forEach((g) => {
      const gx = x(g.minute), home = g.team === d.meta.home, gy = home ? 34 : H - 46;
      s += `<path d="M${gx - 6},${home ? gy - 8 : gy + 8} L${gx + 6},${home ? gy - 8 : gy + 8} L${gx},${gy}Z" fill="var(--ink)" stroke="var(--surface)" stroke-width="2"><title>Goal ${esc(teamName(g.team))} ${Math.round(g.minute)}'</title></path>`;
      s += `<text x="${gx}" y="${home ? gy - 12 : gy + 22}" text-anchor="middle">${Math.round(g.minute)}'</text>`;
    });
    [0, 15, 30, 45, 60, 75, 90].forEach((t) => { s += `<text x="${x(t)}" y="${H - 8}" text-anchor="middle">${t}'</text>`; });
    s += "</svg>";
    const el = $("pulseChart");
    el.innerHTML = `<p class="legend"><span><i class="lg home"></i>Above the line: ${esc(d.meta.home_short)} on top</span><span><i class="lg away"></i>Below: ${esc(d.meta.away_short)} on top</span><span>▲▼ goals</span></p>` + s;
    const tip = $("tooltip");
    el.querySelectorAll(".hit").forEach((r) => {
      r.addEventListener("mousemove", (e) => {
        const w = an.momentum[+r.dataset.i];
        const box = el.parentElement.getBoundingClientRect();
        tip.hidden = false;
        tip.innerHTML = `<strong>${w.start_min}'–${w.end_min}'</strong><br>${esc(d.meta.home_short)} threat units: ${w.atu_home}<br>${esc(d.meta.away_short)} threat units: ${w.atu_away}<br>Momentum: ${w.momentum.toFixed(2)}`;
        tip.style.left = Math.min(e.clientX - box.left + 12, box.width - 250) + "px";
        tip.style.top = (e.clientY - box.top + 12) + "px";
      });
      r.addEventListener("mouseleave", () => { tip.hidden = true; });
    });
    el.querySelectorAll(".band").forEach((b) => b.addEventListener("click", () => selectMoment(b.dataset.mid)));
  }

  // ---------- moments + evidence ----------
  function renderMoments() {
    const d = state.data;
    if (!d.moments.length) {
      $("moments").innerHTML = `<li class="empty">${d.analytics.live ? `No shift has passed the detection thresholds by ${Math.round(d.analytics.until_min)}'.` : "No shift passed the detection thresholds."}</li>`;
      return;
    }
    $("moments").innerHTML = d.moments.map((m) => `
      <li><button type="button" class="moment ${teamClass(m.team)}" data-mid="${m.id}" aria-current="false">
        <span class="m-title">${m.id} · ${esc(PATTERN[m.pattern])}${m.status === "ongoing" ? '<span class="tag-live">ongoing</span>' : ""}</span>
        <span class="m-meta">${esc(teamName(m.team))}, ${Math.round(m.start_min)}'–${Math.round(m.end_min)}' · evidence ${esc(m.strength)}${seenKey(m) in state.firstSeen ? ` · flagged live at ${Math.round(state.firstSeen[seenKey(m)])}'` : ""}
        <span class="meter" aria-hidden="true"><i style="width:${Math.round(m.strength_score * 100)}%"></i></span></span>
      </button></li>`).join("");
    $("moments").querySelectorAll(".moment").forEach((b) => b.addEventListener("click", () => selectMoment(b.dataset.mid)));
  }

  function selectMoment(id) {
    const d = state.data, m = d.moments.find((x) => x.id === id);
    if (!m) return;
    state.selected = id;
    document.querySelectorAll(".moment").forEach((b) => b.setAttribute("aria-current", String(b.dataset.mid === id)));
    document.querySelectorAll(".band").forEach((b) => b.classList.toggle("active", b.dataset.mid === id));
    const rows = m.metrics.map((x) => {
      const cls = x.delta > 0 ? "delta-up" : x.delta < 0 ? "delta-down" : "";
      return `<tr><td>${esc(x.label)}</td><td class="num">${fmt(x.baseline_value)}</td><td class="num">${fmt(x.window_value)}</td><td class="num ${cls}">${x.delta > 0 ? "+" : ""}${fmt(x.delta)}</td></tr>`;
    }).join("");
    const evs = (d.evidence_events[id] || []).slice(0, 14).map((e) => `<tr><td class="num">${Math.floor(e.timestamp_s / 60)}'</td><td>${esc(teamName(e.team))}</td><td>${esc(e.player_id)}</td><td>${esc(e.event_type)}</td><td>${esc(e.outcome)}</td><td class="num">${e.x.toFixed(0)},${e.y.toFixed(0)}</td></tr>`).join("");
    $("evidenceBody").innerHTML = `
      <p><strong>${m.id} · ${esc(PATTERN[m.pattern])}</strong> — ${esc(teamName(m.team))}, ${Math.round(m.start_min)}'–${Math.round(m.end_min)}'. Baseline: ${esc(m.baseline.replace("_", " ").replace("_", " "))}. Evidence strength ${esc(m.strength)} (${m.strength_score.toFixed(2)}).</p>
      <h3>Before and after</h3>
      <table><thead><tr><th>Metric</th><th class="num">Baseline</th><th class="num">Window</th><th class="num">Change</th></tr></thead><tbody>${rows}</tbody></table>
      <h3>Supporting events (${(d.evidence_events[id] || []).length})</h3>
      <table class="events-table"><thead><tr><th class="num">Min</th><th>Team</th><th>Player</th><th>Event</th><th>Outcome</th><th class="num">x,y</th></tr></thead><tbody>${evs || "<tr><td colspan=6>No event-level evidence</td></tr>"}</tbody></table>
      <p class="hint">Synthetic player IDs. Coordinates are in the acting team's frame (attacking towards x = 100).</p>`;
    $("evidence").hidden = false;
    $("evidence").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "nearest" });
  }

  function renderStats() {
    const d = state.data, t = d.analytics.totals, m = d.meta;
    const rows = [["Shots", "shots"], ["Shots on target", "shots_on_target"], ["Possession % (time)", "possession_pct"], ["Pass accuracy %", "pass_accuracy"],
      ["Final-third entries", "final_third_entries"], ["Progressive actions", "progressive_actions"], ["High pressures", "high_pressures"],
      ["High turnovers won", "high_turnovers_forced"], ["Fast breaks", "fast_breaks"]];
    $("stats").innerHTML = `<thead><tr><th></th><th class="num">${esc(m.home_short)}</th><th class="num">${esc(m.away_short)}</th></tr></thead><tbody>` +
      rows.map(([l, k]) => `<tr><td>${l}</td><td class="num">${fmt(t[m.home][k])}</td><td class="num">${fmt(t[m.away][k])}</td></tr>`).join("") + "</tbody>";
  }

  // ---------- story ----------
  function claimHTML(c, reg) {
    const tag = c.kind && c.kind !== "observation" ? `<span class="tag ${c.kind}">${KIND[c.kind] || c.kind}</span>` : "";
    const ev = c.evidence.length ? ` <button type="button" class="ev" data-ev="${esc(c.evidence.join("|"))}" aria-expanded="false">evidence (${c.evidence.length})</button>` : "";
    return `<p class="claim">${tag}${esc(c.text)}${ev}</p>`;
  }

  function wireEvidence(root, reg) {
    root.querySelectorAll(".ev").forEach((b) => b.addEventListener("click", () => {
      const next = b.parentElement.nextElementSibling;
      if (next && next.classList.contains("ev-detail")) { next.remove(); b.setAttribute("aria-expanded", "false"); return; }
      const items = b.dataset.ev.split("|").map((id) => `${esc(id)} = <strong>${esc(JSON.stringify(reg[id]?.value))}</strong>`);
      b.parentElement.insertAdjacentHTML("afterend", `<div class="ev-detail">${items.join("<br>")}</div>`);
      b.setAttribute("aria-expanded", "true");
    }));
  }

  function renderStory() {
    const d = state.data;
    if (!d) return;
    const n = d.narratives[state.mode], reg = d.evidence_registry;
    let h = `<p class="headline">${esc(n.headline.text)}</p>`;
    if (state.mode === "broadcast") {
      h += n.overlay.map((o) => `<div class="lower-third ${teamClass(o.team)}"><span class="bar"></span>
        <div class="lt-main"><div class="lt-head">${esc(o.headline)}</div><div class="lt-text">${esc(o.text)}</div></div>
        <div class="lt-metric"><span class="lt-value">${esc(o.metric_value)}</span><span class="lt-label">${esc(o.metric_label)}</span></div></div>`).join("");
      n.blocks.forEach((b) => { h += `<h3>${esc(b.title)}</h3><ul class="ticker">${b.claims.map((c) => `<li>${claimHTML(c, reg)}</li>`).join("")}</ul>`; });
      h += `<h3>Overlay payload for the graphics pipeline</h3><pre class="json">${esc(JSON.stringify(n.overlay, null, 2))}</pre>`;
    } else {
      n.blocks.forEach((b) => {
        h += `<h3>${esc(b.title)}</h3>`;
        if (b.table && state.mode === "analyst") {
          h += `<table><thead><tr><th>Metric</th><th class="num">Baseline</th><th class="num">Window</th><th class="num">Change</th></tr></thead><tbody>` +
            b.table.map((x) => `<tr><td>${esc(x.label)}</td><td class="num">${fmt(x.baseline_value)}</td><td class="num">${fmt(x.window_value)}</td><td class="num ${x.delta > 0 ? "delta-up" : x.delta < 0 ? "delta-down" : ""}">${x.delta > 0 ? "+" : ""}${fmt(x.delta)}</td></tr>`).join("") + "</tbody></table>";
        }
        h += b.claims.map((c) => claimHTML(c, reg)).join("");
      });
    }
    const rv = d.reviews[state.mode];
    h += `<p class="source">Written by: ${esc(n.source)}. Reviewer: ${esc(rv.status.replaceAll("_", " "))}, ${rv.claims_supported}/${rv.claims_checked} claims verified, evidence coverage ${(rv.evidence_coverage * 100).toFixed(0)}%.</p>`;
    $("storyBody").innerHTML = h;
    wireEvidence($("storyBody"), reg);
  }

  // ---------- trace / review / gt ----------
  function renderTrace(d) {
    const o = d.orchestration;
    $("orch").textContent = o ? `${o.framework} · workflow “${o.workflow}” · writer: ${o.writer} · ${o.total_ms} ms end to end${d.cached ? " (cached)" : ""}` : "";
    $("trace").innerHTML = (d.trace || []).map((t) => `<li><span class="n">${t.step}</span><span><span class="a">${esc(t.agent)}</span><span class="status ${t.status}">${esc(t.status)}</span> <span class="hint">${t.duration_ms} ms${t.handoff_to ? " → " + esc(t.handoff_to) : ""}</span></span><span class="s">${esc(t.summary)}</span></li>`).join("");
  }

  function renderReview() {
    const d = state.data;
    let h = "";
    for (const [mode, r] of Object.entries(d.reviews)) {
      h += `<div class="review-mode"><strong>${mode}</strong> <span class="status ${r.status}">${esc(r.status.replaceAll("_", " "))}</span> — ${r.claims_supported}/${r.claims_checked} claims verified; unsupported rate ${(r.unsupported_rate * 100).toFixed(0)}%.`;
      (r.reasons || []).forEach((x) => { h += `<div class="hint">${esc(x)}</div>`; });
      h += "</div>";
    }
    const rejected = (d.review_history || []).filter((x) => x.status !== "approved");
    if (rejected.length) {
      h += `<p><strong>Rejected drafts</strong> (${rejected.length})</p>`;
      rejected.slice(0, 4).forEach((x) => x.removed.slice(0, 2).forEach((r) => {
        h += `<div class="removed">Attempt ${x.attempt}, ${x.mode}: “${esc(r.text.slice(0, 110))}”<br><em>${esc(r.problems.join("; "))}</em></div>`;
      }));
    } else {
      h += `<p class="hint">No claim was removed in this run. Choose “Simulated faulty model” to watch the reviewer reject invented numbers and causal claims.</p>`;
    }
    $("review").innerHTML = h;
  }

  function renderGT() {
    const d = state.data;
    if (!d.ground_truth) { $("gt").innerHTML = `<p class="hint">Uploaded data has no ground truth.</p>`; return; }
    const chk = d.detection_check, missed = new Set(chk.fn.map((g) => `${g.pattern}|${g.team}|${g.start_min}`));
    let h = "<table><thead><tr><th>Injected pattern</th><th>Window</th><th>Result</th></tr></thead><tbody>";
    d.ground_truth.forEach((g) => {
      const miss = missed.has(`${g.pattern}|${g.team}|${g.start_min}`);
      const live = d.analytics.live;
      let cls = miss ? "missed" : "ok", label = miss ? "missed" : "detected";
      if (live && g.started === false) { cls = "pending"; label = "not started yet"; }
      else if (live && miss) { cls = "pending"; label = "not flagged yet"; }
      h += `<tr><td>${esc(PATTERN[g.pattern])} · ${esc(teamName(g.team))}</td><td>${g.start_min}'–${g.end_min}'</td><td><span class="status ${cls}">${label}</span></td></tr>`;
    });
    h += "</tbody></table>";
    if (chk.fp.length) h += `<p class="hint">Also flagged without an injected label: ${chk.fp.map((f) => `${PATTERN[f.pattern]} (${esc(teamName(f.team))}, ${Math.round(f.start_min)}'–${Math.round(f.end_min)}')`).join("; ")}.</p>`;
    $("gt").innerHTML = h;
  }

  async function loadEval() {
    try {
      const r = await fetch("/api/evaluation");
      if (!r.ok) return;
      const e = await r.json();
      const o = e.detection?.held_out?.overall;
      const lv = e.live_replay?.overall;
      if (o) $("evalSummary").textContent = `Held-out evaluation (${e.detection.held_out.matches} matches): precision ${o.precision}, recall ${o.recall}, F1 ${o.f1}.` +
        (lv ? ` Live replay: ${Math.round(lv.flagged_while_still_running * 100)}% of patterns flagged while still running, median ${lv.median_latency_min} min after they start.` : "");
    } catch (_) { /* optional */ }
  }

  init();
})();
