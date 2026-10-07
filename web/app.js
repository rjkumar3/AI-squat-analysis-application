"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const RESULT_TEXT = { meets: "Meets standard", does_not_meet: "Does not meet", cannot_assess: "Cannot assess" };
const RESULT_MARK = { meets: "✓", does_not_meet: "✕", cannot_assess: "?" };
const OV = { body: "#F4F1E8", est: "#F4F1E8", bar: "#F5C518", foot: "#6EE7A0" };

let CONFIG = null, SKILL = null, file = null, markers = {}, tool = "bar", job = null, layers = { body: 1, bar: 1, mid: 1, angles: 1 };

function show(id) { $$(".view").forEach(v => v.hidden = v.id !== id); window.scrollTo(0, 0); }
function fmtT(t) { if (t == null) return "?"; const m = Math.floor(t / 60), s = t - m * 60; return `${m}:${s.toFixed(1).padStart(4, "0")}`; }
function esc(s) { return String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

// ---------- content-rect mapping (video uses object-fit: contain) ----------
function contentRect(video, canvas) {
  const cw = canvas.clientWidth, ch = canvas.clientHeight, vw = video.videoWidth || 1, vh = video.videoHeight || 1;
  const s = Math.min(cw / vw, ch / vh), w = vw * s, h = vh * s;
  return { x: (cw - w) / 2, y: (ch - h) / 2, w, h };
}
function fitCanvas(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const w = Math.round(canvas.clientWidth * dpr), h = Math.round(canvas.clientHeight * dpr);
  if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
  const ctx = canvas.getContext("2d"); ctx.setTransform(dpr, 0, 0, dpr, 0, 0); return ctx;
}

// ---------- init ----------
async function init() {
  CONFIG = await (await fetch("api/config")).json();
  $("#limits").innerHTML = `<li>${CONFIG.formats.join(", ")}</li><li>Up to ${CONFIG.max_duration_s} seconds and ${CONFIG.max_upload_mb} MB</li><li>Side view; barbell back squat</li>`;
  const m = location.hash.match(/job=([a-f0-9]+)/);
  if (m) { job = m[1]; show("view-progress"); $("#upload-bar").style.width = "100%"; $("#upload-text").textContent = "Uploaded"; poll(); }
}

// ---------- step 1: choose & mark ----------
const drop = $("#drop"), fileInput = $("#file");
drop.addEventListener("click", () => fileInput.click());
drop.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } });
["dragenter", "dragover"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", e => e.dataTransfer.files[0] && choose(e.dataTransfer.files[0]));
fileInput.addEventListener("change", () => fileInput.files[0] && choose(fileInput.files[0]));

const prev = $("#prev"), pc = $("#prev-canvas"), scrub = $("#prev-scrub");
function choose(f) {
  file = f; markers = {};
  if (f.size > CONFIG.max_upload_mb * 1048576) { alert(`This file is ${(f.size / 1048576).toFixed(0)} MB. The limit is ${CONFIG.max_upload_mb} MB.`); return; }
  drop.hidden = true; $("#marker-step").hidden = false;
  prev.src = URL.createObjectURL(f);
  prev.onloadedmetadata = () => { prev.currentTime = Math.min(0.5, prev.duration / 3); };
  prev.onerror = () => { $(".mark-help p").textContent = "Your browser can't preview this format, so markers can't be placed. You can still analyse it; bar and foot checks will show 'cannot assess'."; };
  setTool("bar"); drawPrev();
}
prev.addEventListener("seeked", drawPrev);
prev.addEventListener("loadeddata", drawPrev);
scrub.addEventListener("input", () => { if (prev.duration) prev.currentTime = prev.duration * scrub.value / 1000; });
window.addEventListener("resize", () => { drawPrev(); });

function setTool(t) {
  tool = t;
  $$(".tool").forEach(b => { b.setAttribute("aria-checked", b.dataset.tool === t); b.classList.toggle("done", !!markers[b.dataset.tool]); });
}
$$(".tool").forEach(b => b.addEventListener("click", () => setTool(b.dataset.tool)));
pc.addEventListener("click", e => {
  const r = contentRect(prev, pc), b = pc.getBoundingClientRect();
  const x = (e.clientX - b.left - r.x) / r.w, y = (e.clientY - b.top - r.y) / r.h;
  if (x < 0 || x > 1 || y < 0 || y > 1) return;
  markers[tool] = { x, y }; markers.t = prev.currentTime;
  const order = ["bar", "heel", "toe"], next = order.find(k => !markers[k]);
  setTool(next || tool); drawPrev();
});
function drawPrev() {
  const ctx = fitCanvas(pc); ctx.clearRect(0, 0, pc.clientWidth, pc.clientHeight);
  if (!prev.videoWidth) return;
  const r = contentRect(prev, pc);
  const P = m => [r.x + m.x * r.w, r.y + m.y * r.h];
  if (markers.heel && markers.toe) {
    const [hx, hy] = P(markers.heel), [tx, ty] = P(markers.toe);
    ctx.strokeStyle = OV.foot; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(hx, hy); ctx.lineTo(tx, ty); ctx.stroke();
    const mx = (hx + tx) / 2; ctx.setLineDash([6, 5]); ctx.beginPath(); ctx.moveTo(mx, r.y); ctx.lineTo(mx, r.y + r.h); ctx.stroke(); ctx.setLineDash([]);
  }
  for (const k of ["bar", "heel", "toe"]) {
    if (!markers[k]) continue;
    const [x, y] = P(markers[k]);
    ctx.fillStyle = k === "bar" ? OV.bar : OV.foot; ctx.strokeStyle = "#111"; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(x, y, k === "bar" ? 9 : 6, 0, 7); ctx.fill(); ctx.stroke();
  }
}
$("#reset").addEventListener("click", resetUpload);
function resetUpload() {
  file = null; markers = {}; fileInput.value = ""; drop.hidden = false; $("#marker-step").hidden = true;
  history.replaceState(null, "", location.pathname); show("view-upload");
}

// ---------- step 2: upload & progress ----------
$("#analyze").addEventListener("click", () => {
  if (!file) return;
  const mk = (markers.bar || markers.heel || markers.toe) ? markers : null;
  if (mk && ((mk.heel && !mk.toe) || (!mk.heel && mk.toe)) && !confirm("Only one of heel/toe is marked, so midfoot can't be found. Analyse anyway?")) return;
  const fd = new FormData(); fd.append("file", file); if (mk) fd.append("markers", JSON.stringify(mk));
  show("view-progress"); $("#fail").hidden = true; $("#progress-title").textContent = "Uploading your video"; renderStages(null);
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "api/jobs");
  xhr.upload.onprogress = e => { if (e.lengthComputable) { const p = e.loaded / e.total; $("#upload-bar").style.width = (p * 100) + "%"; $("#upload-text").textContent = `${Math.round(p * 100)}% of ${(e.total / 1048576).toFixed(1)} MB uploaded`; } };
  xhr.onload = () => {
    let d = {}; try { d = JSON.parse(xhr.responseText); } catch { }
    if (xhr.status >= 300) return fail(d.detail || `Upload failed (${xhr.status}).`);
    job = d.id; location.hash = "job=" + job; $("#upload-text").textContent = "Uploaded"; poll();
  };
  xhr.onerror = () => fail("The upload was interrupted. Check your connection and try again.");
  xhr.send(fd);
});
function fail(msg) { $("#fail").hidden = false; $("#fail-msg").textContent = msg; $("#progress-title").textContent = "Analysis stopped"; }
$("#fail-retry").addEventListener("click", resetUpload);

function renderStages(cur, prog) {
  const ids = CONFIG.stages.map(s => s.id), ci = ids.indexOf(cur);
  $("#stages").innerHTML = CONFIG.stages.map((s, i) => {
    const st = cur === "done" || (ci >= 0 && i < ci) ? "done" : i === ci ? "active" : "";
    const pct = st === "active" && prog > 0 && prog < 1 ? `<span class="pct">${Math.round(prog * 100)}%</span>` : "";
    return `<li class="${st}">${esc(s.label)}${pct}</li>`;
  }).join("");
}
async function poll() {
  let d;
  try { const r = await fetch(`api/jobs/${job}`); d = await r.json(); if (!r.ok) return fail(d.detail || "This analysis no longer exists."); }
  catch { setTimeout(poll, 2000); return; }
  if (d.state === "failed") { renderStages(d.stage, 0); return fail(d.error); }
  if (d.state === "done") { renderStages("done"); return results(d); }
  $("#progress-title").textContent = d.state === "queued" ? "Waiting for a free analyser" : "Analysing your squat";
  renderStages(d.stage, d.stage_progress);
  setTimeout(poll, 900);
}

// ---------- step 3: results ----------
const vid = $("#vid"), ov = $("#ov");
let M = null, A = null, filterRep = "all";

function results(d) {
  M = d.measurements; A = d.assessment;
  show("view-results");
  vid.src = `api/jobs/${job}/video`;
  $("#download").href = `api/jobs/${job}/annotated.mp4`;
  renderReport();
  requestAnimationFrame(loop);
}
function renderReport() {
  const ev = A.evaluation, co = A.coaching, q = M.quality;
  $("#issues").innerHTML = q.issues.filter(i => i.level !== "info").map(i => `<div class="issue ${i.level}">${esc(i.message)}</div>`).join("");
  $("#headline").textContent = co.headline;
  $("#summary").textContent = co.summary;
  const modeTxt = co.mode === "agent" ? `Feedback written by the AI coach (${co.model}) and checked against the skill.` : "Feedback text comes directly from the skill file.";
  $("#agent-mode").innerHTML = esc(modeTxt) + ` Standard version ${esc(ev.skill_version)} (${esc(ev.skill_hash)}).` +
    (co.validation?.length ? `<br>Checks on the AI output: ${co.validation.map(esc).join(" ")}` : "");

  // rep strip
  $("#rep-strip").innerHTML = M.reps.length ? M.reps.map(r => `<button data-t="${r.bottom_t}">Rep ${r.rep} bottom · ${fmtT(r.bottom_t)}</button>`).join("") : `<span class="fine">No complete reps found.</span>`;
  $$("#rep-strip button").forEach(b => b.onclick = () => seek(+b.dataset.t));

  // scoreboard
  const crits = ev.criteria, reps = M.reps;
  const board = $("#board");
  board.style.gridTemplateColumns = `minmax(9rem, 1.6fr) repeat(${Math.max(1, reps.length)}, minmax(2.6rem, auto))`;
  let html = `<div class="h"></div>` + reps.map(r => `<div class="h">Rep ${r.rep}</div>`).join("");
  for (const c of crits) {
    html += `<div class="rowname">${esc(c.name.split(":")[0])}${c.assessable === "partial" ? '<small>(proxy)</small>' : ""}</div>`;
    for (const r of reps) {
      const f = ev.findings.find(x => x.rep === r.rep && x.criterion_id === c.id);
      html += `<button class="cell ${f.result}" data-rep="${r.rep}" data-c="${c.id}" aria-label="Rep ${r.rep}, ${esc(c.name)}: ${RESULT_TEXT[f.result]}">${RESULT_MARK[f.result]}</button>`;
    }
  }
  board.innerHTML = reps.length ? html : `<p class="fine">Nothing to score: no complete repetitions were detected. Make sure the whole squat (standing, down, back up) is in the video.</p>`;
  $$("#board .cell").forEach(b => b.onclick = () => openFinding(+b.dataset.rep, b.dataset.c));

  // filter
  $("#filter").innerHTML = [`<button data-r="all">All reps</button>`, `<button data-r="fail">Only problems</button>`, ...reps.map(r => `<button data-r="${r.rep}">Rep ${r.rep}</button>`)].join("");
  $$("#filter button").forEach(b => { b.setAttribute("aria-pressed", b.dataset.r === String(filterRep)); b.onclick = () => { filterRep = b.dataset.r; renderReport(); }; });

  renderFindings();
  $("#not-assessable").innerHTML = ev.not_assessable.map(n => `<li>${esc(n.topic)} <span>(ref p${n.page}) ${esc(n.why)}</span></li>`).join("");
}
function renderFindings() {
  const ev = A.evaluation, co = A.coaching;
  const byKey = Object.fromEntries(co.findings.map(f => [`${f.rep}|${f.criterion_id}`, f]));
  const rank = { does_not_meet: 0, meets: 1, cannot_assess: 2 };
  const list = ev.findings.slice().sort((a, b) => rank[a.result] - rank[b.result] || a.rep - b.rep).filter(f => filterRep === "all" ? true : filterRep === "fail" ? f.result === "does_not_meet" : f.rep === +filterRep);
  $("#findings").innerHTML = list.map(f => {
    const c = byKey[`${f.rep}|${f.criterion_id}`] || {};
    const vals = f.values.map(v => {
      const val = v.value == null ? "not measured" : `${(+v.value).toFixed(Math.abs(v.value) < 10 ? 2 : 1)}`;
      const origin = v.tolerance_origin === "document" ? "These numbers come from the document" : "Band set by the app: the document gives the rule, not a numeric tolerance";
      return `<div class="measure"><span class="v">${esc(v.metric.replaceAll("_", " "))}: ${esc(val)}</span> ${esc(v.unit || "")}<br><span class="origin">${esc(v.band.charAt(0).toUpperCase() + v.band.slice(1))}. ${esc(origin)}.</span></div>`;
    }).join("");
    const pages = [...new Set(f.sources.map(s => s.page))].sort((a, b) => a - b).join(", ");
    if (f.result === "cannot_assess") {
      return `<article class="finding compact cannot_assess" id="f-${f.rep}-${f.criterion_id}"><details>
      <summary><span class="c-name">Rep ${f.rep}: ${esc(f.criterion)}</span><span class="pill cannot_assess">Cannot assess</span></summary>
      <p class="why">${esc(f.reason || "")}</p>
      <div class="f-meta"><button class="seek" data-t="${f.t}">Show at ${fmtT(f.t)}</button> <span>Reference p${esc(pages)}</span></div>
      ${vals}<p class="fb">${esc(c.feedback || f.template_feedback)}</p>
      <div class="src"><details><summary>What the document says</summary><ul>${f.sources.map(s => `<li>p${s.page}, ${esc(s.ref)}: ${esc(s.says)}</li>`).join("")}</ul></details></div>
      </details></article>`;
    }
    return `<article class="finding ${f.result}" id="f-${f.rep}-${f.criterion_id}">
      <div class="f-head"><h3>${esc(f.criterion)}</h3><span class="pill ${f.result}">${RESULT_TEXT[f.result]}</span></div>
      <div class="f-meta">Rep ${f.rep} <button class="seek" data-t="${f.t}">Show at ${fmtT(f.t)}</button> <span>Reference p${esc(pages)}</span></div>
      ${f.result === "cannot_assess" && f.reason ? `<p><b>Why not:</b> ${esc(f.reason)}</p>` : ""}
      ${vals}
      ${c.explanation ? `<p>${esc(c.explanation)}</p>` : ""}
      <p class="fb">${esc(c.feedback || f.template_feedback)}</p>
      ${c.uncertainty ? `<p class="unc">Uncertainty: ${esc(c.uncertainty)}</p>` : ""}
      <div class="src"><details><summary>What the document says</summary><ul>${f.sources.map(s => `<li>p${s.page}, ${esc(s.ref)}: ${esc(s.says)}</li>`).join("")}</ul></details></div>
    </article>`;
  }).join("") || `<p class="fine">No findings match this filter.</p>`;
  $$("#findings .seek").forEach(b => b.onclick = () => seek(+b.dataset.t));
}
function openFinding(rep, cid) {
  if (filterRep !== "all" && filterRep !== String(rep)) { filterRep = "all"; renderReport(); }
  const el = $(`#f-${rep}-${cid}`);
  if (el) { const dd = el.querySelector("details"); if (dd) dd.open = true; el.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "center" }); el.classList.add("flash"); setTimeout(() => el.classList.remove("flash"), 1600); }
  const f = A.evaluation.findings.find(x => x.rep === rep && x.criterion_id === cid); if (f) seek(f.t);
}
function seek(t) { vid.pause(); vid.currentTime = t; if (innerWidth < 900) $("#player").scrollIntoView({ block: "start" }); }

$$(".layer-toggles input").forEach(i => i.addEventListener("change", () => layers[i.dataset.layer] = i.checked));

function frameAt(t) {
  const F = M.frames; if (!F.length) return null;
  let lo = 0, hi = F.length - 1;
  while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (F[mid].t <= t) lo = mid; else hi = mid - 1; }
  const a = F[lo], b = F[Math.min(lo + 1, F.length - 1)];
  if (a === b || b.t <= a.t) return a;
  const w = Math.min(1, Math.max(0, (t - a.t) / (b.t - a.t))), out = { t };
  for (const k in a) if (k !== "t" && b[k]) out[k] = [a[k][0] + (b[k][0] - a[k][0]) * w, a[k][1] + (b[k][1] - a[k][1]) * w, Math.min(a[k][2], b[k][2])];
  return out;
}
function angleDeg(p, q, r, R) { // angle at q
  const v1 = [(p[0] - q[0]) * R.w, (p[1] - q[1]) * R.h], v2 = [(r[0] - q[0]) * R.w, (r[1] - q[1]) * R.h];
  return Math.acos(Math.max(-1, Math.min(1, (v1[0] * v2[0] + v1[1] * v2[1]) / (Math.hypot(...v1) * Math.hypot(...v2))))) * 180 / Math.PI;
}
function label(ctx, txt, x, y, est) {
  ctx.font = "600 13px Barlow, sans-serif"; const w = ctx.measureText(txt).width + 10;
  ctx.fillStyle = "rgba(15,20,25,.78)"; ctx.fillRect(x, y - 15, w, 20);
  ctx.fillStyle = est ? "#cfd3d6" : "#fff"; ctx.fillText(txt, x + 5, y);
}
function loop() {
  if ($("#view-results").hidden) return;
  const ctx = fitCanvas(ov); ctx.clearRect(0, 0, ov.clientWidth, ov.clientHeight);
  if (M && vid.videoWidth) {
    const R = contentRect(vid, ov), P = p => [R.x + p[0] * R.w, R.y + p[1] * R.h], t = vid.currentTime, f = frameAt(t);
    const foot = M.markers.foot;
    if (layers.mid && foot) {
      const [mx] = P(foot.mid);
      ctx.strokeStyle = OV.foot; ctx.lineWidth = 2; ctx.setLineDash([7, 6]);
      ctx.beginPath(); ctx.moveTo(mx, R.y); ctx.lineTo(mx, R.y + R.h); ctx.stroke(); ctx.setLineDash([]);
      for (const k of ["heel", "toe"]) { const [x, y] = P(foot[k]); ctx.fillStyle = OV.foot; ctx.beginPath(); ctx.arc(x, y, 5, 0, 7); ctx.fill(); }
    }
    if (layers.bar && M.markers.bar) {
      const rep = M.reps.find(r => t >= r.start_t - 0.3 && t <= r.end_t + 0.3);
      const t0 = rep ? rep.start_t : t - 3;
      ctx.strokeStyle = OV.bar; ctx.lineWidth = 3; ctx.beginPath(); let pen = false;
      for (const fr of M.frames) {
        if (fr.t < t0 || fr.t > t) continue;
        if (!fr.bar) { pen = false; continue; }
        const [x, y] = P(fr.bar); pen ? ctx.lineTo(x, y) : ctx.moveTo(x, y); pen = true;
      }
      ctx.stroke();
      if (f && f.bar) { const [x, y] = P(f.bar); ctx.lineWidth = 3; ctx.beginPath(); ctx.arc(x, y, 10, 0, 7); ctx.stroke(); }
    }
    if (f && layers.body) {
      const chain = ["ear", "shoulder", "hip", "knee", "ankle"].filter(k => f[k]);
      ctx.lineWidth = 3; ctx.strokeStyle = OV.body;
      for (let i = 0; i < chain.length - 1; i++) {
        const est = f[chain[i]][2] < 1 || f[chain[i + 1]][2] < 1; ctx.setLineDash(est ? [5, 5] : []);
        const [x1, y1] = P(f[chain[i]]), [x2, y2] = P(f[chain[i + 1]]); ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
      }
      ctx.setLineDash([]);
      for (const k of chain) {
        const [x, y] = P(f[k]); ctx.beginPath(); ctx.arc(x, y, 6, 0, 7);
        if (f[k][2] >= 1) { ctx.fillStyle = OV.body; ctx.fill(); ctx.strokeStyle = "#111"; ctx.lineWidth = 1.5; ctx.stroke(); }
        else { ctx.strokeStyle = OV.est; ctx.lineWidth = 2.5; ctx.stroke(); }
      }
    }
    if (f && layers.angles && f.shoulder && f.hip && f.knee && f.ankle) {
      const est = [f.shoulder, f.hip, f.knee, f.ankle].some(p => p[2] < 1), suf = est ? " (est.)" : "";
      const dx = Math.abs(f.shoulder[0] - f.hip[0]) * R.w, dy = (f.hip[1] - f.shoulder[1]) * R.h;
      const back = Math.atan2(dy, dx) * 180 / Math.PI, knee = angleDeg(f.hip, f.knee, f.ankle, R);
      const [hx, hy] = P(f.hip), [kx, ky] = P(f.knee);
      label(ctx, `Back ${back.toFixed(0)}°${suf}`, hx + 12, hy - 10, est);
      label(ctx, `Knee ${knee.toFixed(0)}°${suf}`, kx + 12, ky + 4, est);
      // depth reference at each bottom
      const rep = M.reps.find(r => Math.abs(t - r.bottom_t) < 0.35);
      if (rep) {
        const fd = A.evaluation.findings.find(x => x.rep === rep.rep && x.criterion_id === "DEPTH");
        const col = fd ? { meets: "#4CB97E", does_not_meet: "#E36A55", cannot_assess: "#C9CED3" }[fd.result] : "#fff";
        ctx.strokeStyle = col; ctx.lineWidth = 2; ctx.setLineDash([10, 6]);
        ctx.beginPath(); ctx.moveTo(R.x, ky); ctx.lineTo(R.x + R.w, ky); ctx.stroke(); ctx.setLineDash([]);
        label(ctx, `Rep ${rep.rep} bottom: depth ${fd ? RESULT_TEXT[fd.result].toLowerCase() : "?"}`, R.x + 8, R.y + 24, false);
      }
    }
  }
  requestAnimationFrame(loop);
}

$("#reassess").addEventListener("click", async () => {
  const b = $("#reassess"); b.disabled = true; b.textContent = "Re-checking…";
  try {
    const r = await fetch(`api/jobs/${job}/reassess`, { method: "POST" }); const d = await r.json();
    if (!r.ok) throw new Error(d.detail || "Re-check failed.");
    const before = A.evaluation.skill_hash; A = d; renderReport();
    b.textContent = before === d.evaluation.skill_hash ? "Re-checked (standard unchanged)" : "Re-checked with the updated standard";
  } catch (e) { b.textContent = e.message; }
  setTimeout(() => { b.disabled = false; b.textContent = "Re-check with current standard"; }, 3500);
});
$("#new-video").addEventListener("click", () => { vid.pause(); resetUpload(); });

// ---------- skill viewer ----------
$("#open-skill").addEventListener("click", async () => {
  const d = await (await fetch("api/skill")).json(); const s = d.criteria;
  const band = ch => { const m = ch.meets_if; const parts = []; if (m == null) parts.push("never 'meets'"); else { if ("min" in m) parts.push(`≥ ${m.min}`); if ("max" in m) parts.push(`≤ ${m.max}`); } return parts.join(" and "); };
  $("#skill-body").innerHTML = s.criteria.map(c => `<div class="crit"><h3>${esc(c.name)}</h3>
    <div class="fine">${esc(c.id)}. Assessable from side view: ${esc(c.assessable)}. ${esc(c.why_partial || "")}</div>
    <table><tr><th>Source</th><th>What it says</th></tr>${c.source.map(x => `<tr><td>p${x.page}<br>${esc(x.ref)}</td><td>${esc(x.says)}</td></tr>`).join("")}</table>
    <table><tr><th>Measured</th><th>Meets if</th><th>Fails if</th><th>Origin of the numbers</th></tr>${c.checks.map(ch => `<tr><td>${esc(ch.metric)}<br><span class="fine">${esc(ch.unit)}</span></td><td>${esc(band(ch))}</td><td>${(ch.fails_if_any || []).map(x => "lt" in x ? `< ${x.lt}` : `> ${x.gt}`).join(" or ")}</td><td>${ch.tolerance_origin === "document" ? "Document" : "App tolerance"}: ${esc(ch.tolerance_note || "")}</td></tr>`).join("")}</table></div>`).join("") +
    `<div class="crit"><h3>Not assessable from a side view</h3><ul>${s.not_assessable_from_side_view.map(n => `<li>${esc(n.topic)} (p${n.page}): ${esc(n.why)}</li>`).join("")}</ul></div>`;
  $("#skill-dlg").showModal();
});
$("#close-skill").addEventListener("click", () => $("#skill-dlg").close());

init();
