const $ = (s, r = document) => r.querySelector(s);
const S = { token: null, role: null, me: null, view: "institution", draft: null, pdfUrl: null, sel: {} };
const MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
const fd = d => { if (!d) return "-"; const x = new Date(d + "T00:00:00"); return `${String(x.getDate()).padStart(2,"0")}-${MON[x.getMonth()]}-${x.getFullYear()}`; };
const inr = n => n == null ? "-" : "₹" + Number(n).toLocaleString("en-IN", { maximumFractionDigits: 0 });
const esc = s => String(s ?? "-").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const CATS = ["Active", "Expiring", "Matured", "Renewed", "Released", "Pending"];

async function api(path, opts = {}) {
  const h = { ...(opts.headers || {}) };
  if (S.token) h.Authorization = "Bearer " + S.token;
  if (opts.json) { h["Content-Type"] = "application/json"; opts.body = JSON.stringify(opts.json); }
  const r = await fetch("/api" + path, { ...opts, headers: h });
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch (e) {} throw new Error(m); }
  return opts.blob ? r.blob() : r.json();
}
function toast(msg) { const t = document.createElement("div"); t.className = "toast"; t.textContent = msg; document.body.appendChild(t); setTimeout(() => t.remove(), 3800); }
const guard = fn => async (...a) => { try { await fn(...a); } catch (e) { toast(e.message); } };

async function signIn(username) {
  const r = await api("/auth/login", { method: "POST", json: { username } });
  S.token = r.token; S.role = r.role; S.me = r;
  $("#roleSel").value = username;
  await refreshClock();
}
async function refreshClock() { const c = await api("/clock"); $("#clockTag").textContent = "Portal date: " + fd(c.today) + (c.offset_days ? ` (+${c.offset_days}d simulated)` : ""); }

function saveBlob(blob, name) { const u = URL.createObjectURL(blob); const a = document.createElement("a"); a.href = u; a.download = name; document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(u), 4000); }
function forensicsChip(f) {
  if (!f) return '<span class="chip warn">Forensics: not run</span>';
  return f.suspect ? `<span class="chip bad">Forensics: Tamper Suspect (ELA score ${f.tamper_score})</span>` : `<span class="chip ok">Forensics: Clean (ELA score ${f.tamper_score})</span>`;
}
/* swaps an <img> between the document preview and its ELA heatmap */
function wireEla(btn, img, id) {
  let normal = img.src, ela = null, on = false;
  btn.onclick = guard(async () => {
    if (!on && !ela) ela = URL.createObjectURL(await api(`/instruments/${id}/ela-preview`, { blob: true }));
    on = !on; img.src = on ? ela : normal; btn.textContent = on ? "Show document" : "Toggle ELA Heatmap";
  });
}
function chip(c) { return `<span class="chip ${c}">${c}</span>`; }
function daysTxt(d) { return d == null ? "-" : d < 0 ? `${-d}d overdue` : `${d}d`; }

/* ------------------------------------------------------------------ Institution view */
async function viewInstitution() {
  const el = $("#v-institution");
  const [insts, mine] = await Promise.all([api("/institutions"), api("/instruments")]);
  const soon = mine.filter(i => i.state === "ACTIVE" && i.days_to_maturity != null && i.days_to_maturity <= 90).sort((a, b) => a.days_to_maturity - b.days_to_maturity).slice(0, 2);
  const banner = i => { const d = i.days_to_maturity; const k = d < 0 ? "OVERDUE" : d <= 15 ? "T-15" : d <= 30 ? "T-30" : d <= 60 ? "T-60" : "T-90";
    return `<div class="banner ${k}"><b>${k === "OVERDUE" ? "OVERDUE" : k}</b><span>${esc(i.kind)} ${esc(i.declared.ref_no)} (${esc(i.declared.bank)}) ${d < 0 ? "matured " + (-d) + " days ago" : "matures in <b>" + d + " days</b> on " + fd(i.declared.maturity_date)} &mdash; renew or apply for release.</span></div>`; };
  el.innerHTML = `
    ${soon.map(banner).join("")}
    <div class="split">
      <div class="card"><h2>1. Upload FDR scan &amp; run OCR pre-fill</h2>
        <div class="row">
          <div style="flex:1"><label>Institution</label><select id="upInst">${insts.map(i => `<option value="${i.id}">${esc(i.name)} (${esc(i.pid)})</option>`).join("")}</select></div>
          <div style="width:150px"><label>Instrument</label><select id="upKind"><option>FDR</option><option>JOINT_FDR</option><option>RPGF</option><option>RTGS</option></select></div>
        </div>
        <label>FDR / RPGF / RTGS scan (PDF, PNG or JPG)</label><input type="file" id="upFile" accept=".pdf,image/*">
        <div class="row" style="margin-top:12px"><button class="pri" id="upGo">Upload &amp; read with OCR</button><span class="mut" id="upMsg"></span></div>
        <div id="pdfWrap" style="margin-top:12px"></div>
      </div>
      <div class="card" id="confirmCard"><h2>2. Review &amp; confirm extracted fields</h2><p class="mut">Upload a scan to pre-fill the instrument fields. Low-confidence values are highlighted; you confirm every value before it is saved.</p></div>
    </div>
    <div class="card" style="margin-top:14px"><h2>My instruments</h2>${instTable(mine.slice(0, 14))}</div>`;
  $("#upGo").onclick = guard(async () => {
    const f = $("#upFile").files[0]; if (!f) return toast("Choose a scan first");
    const fm = new FormData(); fm.append("file", f); fm.append("institution_id", $("#upInst").value); fm.append("kind", $("#upKind").value);
    $("#upMsg").textContent = "Reading document with OCR…";
    S.draft = await api("/instruments/upload", { method: "POST", body: fm });
    $("#upMsg").textContent = "OCR done.";
    const blob = await api(`/instruments/${S.draft.id}/preview`, { blob: true });
    if (S.pdfUrl) URL.revokeObjectURL(S.pdfUrl);
    S.pdfUrl = URL.createObjectURL(blob);
    $("#pdfWrap").innerHTML = `<img class="pdfbox" style="object-fit:contain;height:auto;max-height:460px" alt="Uploaded document" src="${S.pdfUrl}">`;
    renderConfirm();
  });
  el.querySelectorAll("tr.click").forEach(tr => tr.onclick = guard(async () => { S.sel.disc = +tr.dataset.id; go("discrepancy"); }));
}
function instTable(rows) {
  return `<table><thead><tr><th>Ref no.</th><th>Type</th><th>Bank</th><th>Amount</th><th>Maturity</th><th>Left</th><th>Bank check</th><th>Status</th></tr></thead><tbody>` +
    rows.map(i => `<tr class="click" data-id="${i.id}"><td>${esc(i.declared.ref_no)}</td><td>${esc(i.kind)}</td><td>${esc(i.declared.bank)}</td><td>${inr(i.declared.amount)}</td><td>${fd(i.declared.maturity_date)}</td><td>${daysTxt(i.days_to_maturity)}</td>
    <td>${i.verification === "VERIFIED" ? '<span class="chip ok">Verified</span>' : i.verification === "DISCREPANCY" ? '<span class="chip bad">Discrepancy</span>' : '<span class="chip warn">' + esc(i.verification) + '</span>'}</td><td>${chip(i.category)}</td></tr>`).join("") + `</tbody></table>`;
}
function renderConfirm() {
  const d = S.draft, f = d.ocr.fields, c = d.ocr.confidence;
  const fld = (k, label, val) => `<label>${label} <span class="mut">(OCR confidence ${Math.round((c[k] || 0) * 100)}%)</span></label><input data-k="${k}" class="${(c[k] || 0) < 0.8 || val == null ? "low" : ""}" value="${esc(val ?? "").replace(/^-$/, "")}">`;
  $("#confirmCard").innerHTML = `<h2>2. Review &amp; confirm extracted fields</h2>
    <div class="row"><span class="tag" style="background:#e6eefb;color:#0b2a4a">${esc(d.ocr.engine)} &middot; deskew ${d.ocr.deskew_angle}&deg;</span><span class="mut">SHA-256 ${esc(d.doc_sha256).slice(0, 16)}&hellip;</span></div>
    <div class="row" style="margin-top:6px">${forensicsChip(d.ocr.forensics)}<button class="sec" id="elaBtn">Toggle ELA Heatmap</button></div>
    <div class="grid g2">
      <div>${fld("ref_no", "FDR / reference no.", f.ref_no)}</div><div>${fld("bank", "Issuing bank", f.bank)}</div>
      <div>${fld("amount", "Deposit amount", f.amount)}</div><div><label>Joint lien in favour of AICTE</label><select data-k="lien"><option value="true" ${f.lien !== false ? "selected" : ""}>Yes (marked)</option><option value="false" ${f.lien === false ? "selected" : ""}>No</option></select></div>
      <div>${fld("issue_date", "Date of deposit", f.issue_date)}</div><div>${fld("maturity_date", "Maturity date", f.maturity_date)}</div>
    </div>
    <div class="row" style="margin-top:14px"><button class="pri" id="cfGo">Confirm &amp; send for bank verification</button><button class="sec" id="docGo">Attach Affidavit 6 / LoA</button></div>
    <div id="cfOut"></div>`;
  const docImg = $("#pdfWrap img"); if (docImg) wireEla($("#elaBtn"), docImg, S.draft.id); else $("#elaBtn").disabled = true;
  $("#cfGo").onclick = guard(async () => {
    const body = {}; document.querySelectorAll("#confirmCard [data-k]").forEach(i => body[i.dataset.k] = i.value);
    body.lien = body.lien === "true";
    const r = await api(`/instruments/${S.draft.id}/confirm`, { method: "POST", json: body });
    S.draft = { ...S.draft, ...r };
    $("#cfOut").innerHTML = outcome(r);
  });
  $("#docGo").onclick = guard(async () => {
    for (const t of ["affidavit_6", "loa_eoa"]) { const fm = new FormData(); fm.append("doc_type", t); fm.append("file", new Blob(["synthetic " + t], { type: "application/pdf" }), t + ".pdf"); await api(`/instruments/${S.draft.id}/documents`, { method: "POST", body: fm }); }
    toast("Affidavit 6 and LoA/EoA attached");
  });
}
function outcome(r) {
  if (r.missing_fields.length) return `<div class="flag">Missing fields: ${r.missing_fields.join(", ")}</div>`;
  if (r.verification === "VERIFIED") return `<div class="okbox">Bank-verified: all fields match the bank-confirmed record (${esc(r.bank_channel)}). Status: Active.</div>`;
  if (r.verification === "MANUAL_REVIEW") return `<div class="flag" style="background:#fff8e6;border-color:#ecd48a;color:#7a5b00">Bank not supported by the API adapter: routed to Manual Review Required.</div>`;
  return `<div class="flag">Discrepancy flagged: ${r.mismatches.map(m => esc(m.field)).join(", ")}. Record routed to the Regulation Bureau scrutiny officer.</div>`;
}

/* ------------------------------------------------------------------ Bureau dashboard */
async function viewBureau() {
  const el = $("#v-bureau");
  const [dash, alerts] = await Promise.all([api("/dashboard"), api("/alerts")]);
  const canClock = ["admin", "scrutiny_officer"].includes(S.role);
  const cat = S.sel.cat || "";
  const rows = await api("/instruments" + (cat ? "?category=" + cat : ""));
  el.innerHTML = `
    <div class="grid g6">${CATS.map(c => `<div class="stat s-${c}" style="cursor:pointer" data-cat="${c}"><b>${dash.counts[c] || 0}</b><span>${c}</span></div>`).join("")}</div>
    <div class="grid g3" style="margin-top:14px">
      <div class="card"><h3>Pledged corpus (active deposits)</h3><div style="font-size:26px;font-weight:700;color:var(--navy)">${inr(dash.pledged_corpus)}</div><div class="mut">${dash.total} instruments &middot; ${dash.overdue_escalated} overdue escalated</div></div>
      <div class="card"><h3>Simulated clock &amp; scheduler</h3><div class="row"><button class="sec" data-adv="30" ${canClock ? "" : "disabled"}>+30 days</button><button class="sec" data-adv="90" ${canClock ? "" : "disabled"}>+90 days</button><button class="pri" id="runSch" ${canClock ? "" : "disabled"}>Run daily scheduler</button></div><div class="mut" style="margin-top:6px">Advance the portal date and watch T-90/60/30/15 and overdue alerts fire.</div></div>
      <div class="card"><h3>Latest alerts</h3>${alerts.slice(0, 4).map(a => `<div class="row" style="margin:3px 0"><span class="chip ${a.kind === "OVERDUE" ? "bad" : "warn"}">${a.kind}</span><span style="font-size:12px">${esc(a.message.split(". ")[0])}.</span></div>`).join("") || '<span class="mut">No alerts yet. Run the scheduler.</span>'}</div>
    </div>
    <div class="card" style="margin-top:14px"><div class="row"><h2 style="margin:0">Consolidated deposits ${cat ? "&middot; " + cat : ""}</h2><span class="sp"></span><button class="sec" id="expCsv">Export CSV</button><button class="sec" id="impCsv">Import Legacy CSV</button><button class="sec" id="impSample">Load sample legacy CSV</button><input type="file" id="impFile" accept=".csv,text/csv" style="display:none"><select id="catSel" style="width:160px"><option value="">All states</option>${CATS.map(c => `<option ${c === cat ? "selected" : ""}>${c}</option>`).join("")}</select></div>
    <div style="margin-top:10px;max-height:420px;overflow:auto">${bureauTable(rows.slice(0, 40))}</div></div>`;
  el.querySelectorAll("[data-cat]").forEach(b => b.onclick = () => { S.sel.cat = b.dataset.cat; viewBureau(); });
  const importResult = r => { toast(`Imported ${r.created} record(s)` + (r.skipped.length ? `, ${r.skipped.length} skipped` : "") + (r.errors.length ? `, ${r.errors.length} row error(s)` : "") + " " + Object.entries(r.outcomes).map(([k, v]) => `${k}: ${v}`).join(" ")); viewBureau(); };
  $("#expCsv").onclick = guard(async () => saveBlob(await api("/instruments/export.csv", { blob: true }), "nidhisetu_deposits.csv"));
  $("#impCsv").onclick = () => $("#impFile").click();
  $("#impFile").onchange = guard(async e => { const f = e.target.files[0]; if (!f) return; const fm = new FormData(); fm.append("file", f); importResult(await api("/instruments/bulk-csv", { method: "POST", body: fm })); });
  $("#impSample").onclick = guard(async () => importResult(await api("/instruments/bulk-csv?sample=true", { method: "POST" })));
  $("#catSel").onchange = e => { S.sel.cat = e.target.value; viewBureau(); };
  el.querySelectorAll("[data-adv]").forEach(b => b.onclick = guard(async () => { await api("/clock", { method: "POST", json: { days: +b.dataset.adv } }); await refreshClock(); viewBureau(); }));
  $("#runSch").onclick = guard(async () => { const r = await api("/scheduler/run", { method: "POST" }); toast(`${r.created.length} alert(s) generated`); viewBureau(); });
  el.querySelectorAll("tr.click").forEach(tr => tr.onclick = () => { S.sel.disc = +tr.dataset.id; go("discrepancy"); });
}
function bureauTable(rows) {
  return `<table><thead><tr><th>Institution</th><th>Ref no.</th><th>Type</th><th>Bank</th><th>Amount</th><th>Maturity</th><th>Left</th><th>State</th></tr></thead><tbody>` +
    rows.map(i => `<tr class="click" data-id="${i.id}"><td>${esc(i.institution)}</td><td>${esc(i.declared.ref_no)}</td><td>${esc(i.kind)}</td><td>${esc(i.declared.bank)}</td><td>${inr(i.declared.amount)}</td><td>${fd(i.declared.maturity_date)}</td><td>${daysTxt(i.days_to_maturity)}</td><td>${chip(i.category)}</td></tr>`).join("") + `</tbody></table>`;
}

/* ------------------------------------------------------------------ Discrepancy view */
const FL = { ref_no: "FDR / reference no.", bank: "Issuing bank", amount: "Deposit amount", issue_date: "Date of deposit", maturity_date: "Maturity date", lien: "Joint lien (AICTE)" };
function val(k, v) { return v == null ? "&mdash;" : k === "amount" ? inr(v) : k.endsWith("date") ? fd(v) : k === "lien" ? (v ? "Marked" : "Not marked") : esc(v); }
async function viewDiscrepancy() {
  const el = $("#v-discrepancy");
  const rows = await api("/instruments");
  const flagged = rows.filter(r => r.verification === "DISCREPANCY" || r.verification === "MANUAL_REVIEW");
  const pick = S.sel.disc || (flagged[0] && flagged[0].id);
  el.innerHTML = `<div class="split" style="grid-template-columns:340px 1fr">
    <div class="card"><h2>Flagged instruments (${flagged.length})</h2><div style="max-height:560px;overflow:auto">${flagged.map(r => `<div class="chk click" style="cursor:pointer;${r.id === pick ? "background:#eaf1fd" : ""}" data-id="${r.id}"><div><b>${esc(r.declared.ref_no)}</b><div class="mut" style="font-size:12px">${esc(r.institution)}</div></div><span class="sp"></span>${r.verification === "MANUAL_REVIEW" ? '<span class="chip warn">manual</span>' : `<span class="chip bad">${r.mismatches.length} diff</span>`}</div>`).join("") || '<p class="mut">Nothing flagged.</p>'}</div></div>
    <div class="card" id="dBody"><p class="mut">Select an instrument.</p></div></div>`;
  el.querySelectorAll("[data-id]").forEach(d => d.onclick = () => { S.sel.disc = +d.dataset.id; viewDiscrepancy(); });
  if (!pick) return;
  const r = await api("/instruments/" + pick);
  const bad = new Set(r.mismatches.map(m => m.field));
  const grid = ["ref_no", "bank", "amount", "issue_date", "maturity_date", "lien"].map(k => `<div class="k">${FL[k]}</div><div class="${bad.has(k) ? "bad" : "good"}">${val(k, r.declared[k])}</div><div class="${bad.has(k) ? "bad" : "good"}">${val(k, r.bank[k])}</div><div>${bad.has(k) ? "&#9888;" : "&#10003;"}</div>`).join("");
  $("#dBody").innerHTML = `<div class="row"><h2 style="margin:0">${esc(r.institution)}</h2><span class="sp"></span>${chip(r.category)}</div>
    <div class="mut" style="margin:2px 0 10px">${esc(r.kind)} ${esc(r.declared.ref_no)} &middot; channel: ${esc(r.bank_channel)} &middot; PID ${esc(r.pid)}</div>
    <div class="cmp"><div class="h">Field</div><div class="h">Institution-declared</div><div class="h">Bank-verified</div><div class="h"></div>${grid}</div>
    ${r.verification === "MANUAL_REVIEW" ? '<div class="flag" style="background:#fff8e6;border-color:#ecd48a;color:#7a5b00">Manual Review Required: this bank is not supported by the API adapter. A scrutiny officer can mark it verified after the bank nodal officer confirms the record.</div>' : r.mismatches.length ? `<div class="flag">Discrepancy detected (${r.mismatches.map(m => FL[m.field] || "Bank record").join(", ")}): record locked and routed to the Regulation Bureau scrutiny officer. Auto-approval is blocked.</div>` : '<div class="okbox">All fields match the bank-confirmed record.</div>'}
    <div class="row" style="margin:10px 0" id="dForensics">${forensicsChip(r.ocr && r.ocr.forensics)}${r.doc_sha256 ? '<button class="sec" id="dEla">Toggle ELA Heatmap</button>' : ""}</div>
    ${r.doc_sha256 ? '<img id="dDoc" alt="Document preview" style="max-width:100%;max-height:260px;object-fit:contain;border:1px solid #d6dde8;border-radius:6px" hidden>' : ""}
    <div class="timeline">${r.lineage.map(l => `<span class="n">${esc(l.ref_no)} &middot; ${l.state}</span>`).join(" &rarr; ")}</div>
    <label>Scrutiny observation</label><textarea id="sNote" rows="2" placeholder="e.g. Receipt shows 2034 maturity; bank confirms 2028. Institution asked to resubmit."></textarea>
    <div class="row" style="margin-top:8px"><button class="pri" id="sGo">Log observation</button><button class="sec" id="rvGo">Re-run bank verification</button>${r.verification === "DISCREPANCY" ? '<button class="sec" id="ntGo">Download Show-Cause Notice (PDF)</button>' : ""}${r.verification === "MANUAL_REVIEW" ? '<button class="pri" id="mrGo">Mark VERIFIED (manual nodal confirmation)</button>' : ""}</div>`;
  if ($("#dDoc")) { guard(async () => { const img = $("#dDoc"); img.src = URL.createObjectURL(await api(`/instruments/${pick}/preview`, { blob: true })); img.hidden = false; wireEla($("#dEla"), img, pick); })(); }
  if ($("#ntGo")) $("#ntGo").onclick = guard(async () => saveBlob(await api(`/instruments/${pick}/discrepancy-notice.pdf`, { blob: true }), `show_cause_${pick}.pdf`));
  if ($("#mrGo")) $("#mrGo").onclick = guard(async () => { const note = $("#sNote").value.trim(); if (!note) return toast("Enter the nodal confirmation note in the observation box first"); await api(`/instruments/${pick}/manual-resolve`, { method: "POST", json: { note } }); toast("Marked VERIFIED after manual nodal confirmation"); viewDiscrepancy(); });
  $("#sGo").onclick = guard(async () => { await api(`/instruments/${pick}/scrutiny`, { method: "POST", json: { note: $("#sNote").value || "reviewed" } }); toast("Observation written to the audit trail"); });
  $("#rvGo").onclick = guard(async () => { await api(`/instruments/${pick}/reverify`, { method: "POST" }); toast("Re-verified"); viewDiscrepancy(); });
}

/* ------------------------------------------------------------------ Release (APH Cl. 6.46) */
async function viewRelease() {
  const el = $("#v-release");
  const rows = (await api("/instruments")).filter(r => (r.category === "Matured" || r.state === "RELEASE_PENDING" || r.state === "RELEASED") && r.verification === "VERIFIED");
  const pick = S.sel.rel || (rows[0] && rows[0].id);
  el.innerHTML = `<div class="split" style="grid-template-columns:340px 1fr">
    <div class="card"><h2>Matured / release cases</h2><div style="max-height:560px;overflow:auto">${rows.map(r => `<div class="chk" style="cursor:pointer;${r.id === pick ? "background:#eaf1fd" : ""}" data-id="${r.id}"><div><b>${esc(r.declared.ref_no)}</b><div class="mut" style="font-size:12px">${esc(r.institution)}</div></div><span class="sp"></span>${chip(r.state === "ACTIVE" ? "Matured" : r.state === "RELEASED" ? "Released" : "Pending")}</div>`).join("") || '<p class="mut">No matured instruments.</p>'}</div></div>
    <div class="card" id="rBody"><p class="mut">Select a case.</p></div></div>`;
  el.querySelectorAll("[data-id]").forEach(d => d.onclick = () => { S.sel.rel = +d.dataset.id; viewRelease(); });
  if (!pick) return;
  const inst = rows.find(r => r.id === pick);
  let st = null;
  try { st = await api(`/instruments/${pick}/release`); } catch (e) {}
  const dot = v => v === true ? '<div class="dot y">&#10003;</div>' : v === false ? '<div class="dot x">&times;</div>' : '<div class="dot n"></div>';
  const body = st ? `
    <h3>Document checklist (APH Clause 6.46)</h3>${Object.entries(st.documents).map(([k, v]) => `<div class="chk">${dot(v)}<span>${k.replace(/_/g, " ")}</span></div>`).join("")}
    <h3 style="margin-top:12px">Inter-cell clearances</h3>${Object.entries(st.clearances).map(([k, v]) => `<div class="chk">${dot(v)}<span>${k.toUpperCase()}</span><span class="sp"></span>${st.state === "OPEN" ? `<button class="sec" data-cl="${k}" data-ap="true">Clear</button><button class="dng" data-cl="${k}" data-ap="false">Deny</button>` : ""}</div>`).join("")}
    ${st.state === "APPROVED" ? `<div class="okbox">Released. ${st.order_type === "NOC" ? "No Objection Certificate" : "Council Sanction Order"} <b>${esc(st.order_ref)}</b> generated.
      <div class="mut" style="margin-top:6px">Order SHA-256: <span class="hash">${esc(st.order_sha256)}</span></div>
      <div style="margin-top:8px"><button class="pri" id="ordPdf">Download NOC / Sanction Order (PDF)</button></div></div>` :
      st.eligible ? '<div class="okbox">All Clause 6.46 conditions met. Ready for Competent Authority decision.</div>' : `<div class="flag">Release blocked: ${st.blockers.map(esc).join("; ")}</div>`}
    <div class="row" style="margin-top:10px"><button class="pri" id="apr" ${st.state === "OPEN" ? "" : "disabled"}>Competent Authority: approve release</button><button class="dng" id="rej" ${st.state === "OPEN" ? "" : "disabled"}>Reject</button></div>` :
    `<p>No release request yet.</p><button class="pri" id="opn">Open release request</button>`;
  $("#rBody").innerHTML = `<div class="row"><h2 style="margin:0">${esc(inst.institution)}</h2><span class="sp"></span>${chip(inst.category)}</div><div class="mut" style="margin:2px 0 10px">${esc(inst.kind)} ${esc(inst.declared.ref_no)} &middot; ${inr(inst.declared.amount)} &middot; matured ${fd(inst.declared.maturity_date)}</div>${body}`;
  if ($("#ordPdf")) $("#ordPdf").onclick = guard(async () => { const b = await api(`/instruments/${pick}/release/order-pdf`, { blob: true }); window.open(URL.createObjectURL(b), "_blank") || saveBlob(b, `${st.order_type}_${st.request_id}.pdf`); });
  if ($("#opn")) $("#opn").onclick = guard(async () => { await api(`/instruments/${pick}/release`, { method: "POST" }); viewRelease(); });
  $("#rBody").querySelectorAll("[data-cl]").forEach(b => b.onclick = guard(async () => { await api(`/instruments/${pick}/release/clearance`, { method: "POST", json: { cell: b.dataset.cl, approve: b.dataset.ap === "true" } }); viewRelease(); }));
  if ($("#apr")) { $("#apr").onclick = guard(async () => { const r = await api(`/instruments/${pick}/release/decision`, { method: "POST", json: { approve: true } }); toast("Released: " + r.order_ref); viewRelease(); });
    $("#rej").onclick = guard(async () => { await api(`/instruments/${pick}/release/decision`, { method: "POST", json: { approve: false } }); viewRelease(); }); }
}

/* ------------------------------------------------------------------ Audit */
async function viewAudit() {
  const el = $("#v-audit");
  const [rows, v] = await Promise.all([api("/audit?limit=40"), api("/audit/verify")]);
  el.innerHTML = `<div class="card"><div class="row"><h2 style="margin:0">Tamper-evident audit trail</h2><span class="sp"></span><span class="chip ${v.valid ? "ok" : "bad"}">${v.valid ? "Chain valid" : "Chain BROKEN at #" + v.broken_at}</span><span class="mut">${v.checked} entries verified</span></div>
    <p class="mut">Every entry stores sha256(previous hash + entry). Editing any historical row breaks every hash after it.</p>
    <table><thead><tr><th>#</th><th>Time (UTC)</th><th>Actor</th><th>Action</th><th>Instrument</th><th>Hash</th></tr></thead><tbody>${rows.map(r => `<tr><td>${r.id}</td><td>${esc(r.ts).slice(0, 19)}</td><td>${esc(r.actor)} <span class="mut">(${esc(r.role)})</span></td><td>${esc(r.action)}</td><td>${r.entity_id ?? "-"}</td><td class="hash">${r.hash.slice(0, 16)}&hellip;</td></tr>`).join("")}</tbody></table></div>`;
}

/* ------------------------------------------------------------------ shell */
const VIEWS = { institution: viewInstitution, bureau: viewBureau, discrepancy: viewDiscrepancy, release: viewRelease, audit: viewAudit };
function go(v) {
  S.view = v;
  document.querySelectorAll("#nav button").forEach(b => b.classList.toggle("on", b.dataset.view === v));
  document.querySelectorAll(".view").forEach(s => s.classList.toggle("on", s.id === "v-" + v));
  guard(VIEWS[v])();
}
async function boot() {
  const users = await (await fetch("/api/auth/demo-users")).json();
  $("#roleSel").innerHTML = users.map(u => `<option value="${u.username}">${esc(u.display)}</option>`).join("");
  const p = new URLSearchParams(location.search);
  await signIn(p.get("as") || "dealing_hand");
  if (p.get("id")) { S.sel.disc = S.sel.rel = +p.get("id"); }
  document.querySelectorAll("#nav button").forEach(b => b.onclick = () => go(b.dataset.view));
  $("#roleSel").onchange = guard(async e => { await signIn(e.target.value); go(S.view); });
  go(p.get("view") || "institution");
}
boot();
