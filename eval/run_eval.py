"""Prototype evaluation on a synthetic test set. Produces eval/results/metrics.json and METRICS.md.

Scenario -> what is measured (all on SYNTHETIC data; see docs/METRICS.md for definitions):
 1 New FDR submission   field-level OCR precision/recall (before human confirmation)
 2 Validity tracking    classification of Active / Expiring / Matured against bank-truth dates
 3 Approaching maturity T-90/60/30/15 + overdue alerts vs an independently computed expected schedule
 4 Renewal chain        parent-FDR suggestion accuracy and lineage reconstruction
 5 Discrepancy ident.   seeded mismatches (amount / maturity / lien / not-in-bank) vs formatting-only noise
 6 Authorized release   Clause 6.46 gate: blocks every incomplete request, passes complete ones
"""
import json
import os
import random
import statistics
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["NIDHISETU_DATA"] = tempfile.mkdtemp(prefix="nidhisetu_eval_")
os.environ["DEMO_MODE"] = "1"

from eval import synth  # noqa: E402


def _ocr_one(args):
    path, = args
    from app import ocr
    t = time.perf_counter()
    r = ocr.extract_fields(path)
    return r, time.perf_counter() - t


def prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 1.0
    r = tp / (tp + fn) if tp + fn else 1.0
    return round(100 * p, 1), round(100 * r, 1)


def main(n=150, workers=None, out=ROOT / "eval" / "results"):
    out.mkdir(parents=True, exist_ok=True)
    data_dir = Path(os.environ["NIDHISETU_DATA"]) / "synthetic"
    today = date.today()
    ds = synth.generate(data_dir, n=n, today=today)
    items = ds["items"]
    workers = workers or max(1, (os.cpu_count() or 2) - 0)

    print(f"[1/7] OCR on {len(items)} synthetic documents with {workers} workers ...", flush=True)
    t0 = time.perf_counter()
    cache = out / ".ocr_cache.json"
    if cache.exists() and json.loads(cache.read_text()).get("key") == [n, today.isoformat(), ds["seed"]]:
        ocr_res = [tuple(x) for x in json.loads(cache.read_text())["res"]]
    else:
        with ProcessPoolExecutor(workers) as ex:
            ocr_res = list(ex.map(_ocr_one, [(str(data_dir / it["file"]),) for it in items], chunksize=2))
        cache.write_text(json.dumps({"key": [n, today.isoformat(), ds["seed"]], "res": ocr_res}))
    print(f"      OCR wall time {time.perf_counter() - t0:.0f}s", flush=True)

    from app.db import init_db, SessionLocal
    from app import models, services, scheduler, release, clock, audit, bank
    from app.models import Institution, Instrument, Document, User, ReleaseRequest
    from app.normalize import parse_amount, parse_date, parse_bool, canon_bank, canon_ref
    from app.reconcile import compare
    from app.rules import get_rules
    from app.security import hash_password
    init_db()
    db = SessionLocal()
    inst_ids = {}
    for i in ds["institutions"]:
        row = Institution(pid=i["pid"], name=i["name"], state=i["state"]); db.add(row); db.flush(); inst_ids[i["pid"]] = row.id
    user = User(username="eval", password_hash=hash_password("x"), role="dealing_hand"); db.add(user); db.commit()
    for it in items:                        # bank-side registry = ground truth at the bank
        t = it["bank_truth"]
        if t:
            bank.register_bank_record(db, t["ref_no"], t["bank"], t["amount"], date.fromisoformat(t["issue_date"]),
                                      date.fromisoformat(t["maturity_date"]), t["lien"])
    db.commit()
    rules = get_rules(db)
    metrics = {"n_documents": len(items), "generated_for": today.isoformat(), "seed": ds["seed"], "scenarios": {}}

    # ---------------- 1. OCR extraction (field-level, before human confirmation)
    print("[2/7] scenario 1: OCR extraction", flush=True)
    F = ("ref_no", "bank", "amount", "issue_date", "maturity_date", "lien")
    def norm(f, v):
        if v is None: return None
        if f == "bank": return canon_bank(v)
        if f == "ref_no": return canon_ref(str(v))
        if f == "amount": return parse_amount(v)
        if f in ("issue_date", "maturity_date"): return parse_date(v)
        return parse_bool(v)
    tp = fp = fn = 0
    by_scan = {}
    per_field = {f: [0, 0, 0] for f in F}
    for it, (res, _) in zip(items, ocr_res):
        for f in F:
            truth = norm(f, it[f])
            got = norm(f, res["fields"].get(f))
            s = by_scan.setdefault(it["scan"], [0, 0, 0])
            if got is not None and got == truth:
                tp += 1; s[0] += 1; per_field[f][0] += 1
            else:
                fn += 1; s[2] += 1; per_field[f][2] += 1
                if got is not None:
                    fp += 1; s[1] += 1; per_field[f][1] += 1
    p, r = prf(tp, fp, fn)
    metrics["scenarios"]["1_new_fdr_submission"] = {
        "precision": p, "recall": r, "false_positive": fp, "false_negative": fn, "field_total": tp + fn,
        "by_scan_quality": {k: dict(zip(("precision", "recall"), prf(*v)), docs=sum(1 for i in items if i["scan"] == k)) for k, v in by_scan.items()},
        "by_field_recall": {f: prf(*v)[1] for f, v in per_field.items()}}
    doc_all_ok = sum(1 for it, (res, _) in zip(items, ocr_res) if all(norm(f, it[f]) == norm(f, res["fields"].get(f)) for f in F))
    metrics["scenarios"]["1_new_fdr_submission"]["documents_fully_correct_pct"] = round(100 * doc_all_ok / len(items), 1)

    # ---------------- 5. discrepancy identification (post human-confirmation + raw OCR end-to-end)
    print("[3/7] scenario 5: discrepancy identification", flush=True)
    pipe_times, created = [], []
    for it, (res, ocr_t) in zip(items, ocr_res):
        t = time.perf_counter()
        content = (data_dir / it["file"]).read_bytes()
        inst = services.create_draft(db, user, inst_ids[it["pid"]], it["kind"], Path(it["file"]).name, content, run_ocr=False)
        inst.ocr = res
        for dt in ("affidavit_6", "loa_eoa"):
            db.add(Document(instrument_id=inst.id, doc_type=dt, filename=f"{dt}.pdf", sha256="0" * 64))
        db.flush()
        services.confirm(db, user, inst, {f: it[f] for f in F})      # uploader confirmed the (true) printed values
        pipe_times.append(time.perf_counter() - t)
        created.append(inst.id)
    def conf_matrix(pred_pos):
        tp = fp = fn = tn = 0
        for it, pp in zip(items, pred_pos):
            pos = bool(it["seeded"])
            tp += pos and pp; fp += (not pos) and pp; fn += pos and (not pp); tn += (not pos) and (not pp)
        return tp, fp, fn, tn
    pred = [db.get(Instrument, iid).verification == "DISCREPANCY" for iid in created]
    tp5, fp5, fn5, tn5 = conf_matrix(pred)
    p, r = prf(tp5, fp5, fn5)
    # raw OCR end-to-end (no human correction): look up the bank by OCR'd reference
    adapter = bank.get_adapter()
    pred_raw = []
    for it, (res, _) in zip(items, ocr_res):
        f = res["fields"]
        declared = {"ref_no": f["ref_no"], "bank": canon_bank(f["bank"]), "amount": parse_amount(f["amount"]),
                    "issue_date": parse_date(f["issue_date"]), "maturity_date": parse_date(f["maturity_date"]), "lien": parse_bool(f["lien"])}
        br = adapter.verify(db, f["ref_no"], f["bank"], rules)
        pred_raw.append(br.status == "not_found" or (br.status == "confirmed" and bool(compare(declared, br.values))))
    tpr, fpr, fnr, tnr = conf_matrix(pred_raw)
    pr, rr = prf(tpr, fpr, fnr)
    by_type = {}
    for it, pp in zip(items, pred):
        for t in it["seeded"]:
            by_type.setdefault(t, [0, 0]); by_type[t][1] += 1; by_type[t][0] += pp
    metrics["scenarios"]["5_discrepancy_identification"] = {
        "precision": p, "recall": r, "false_positive": fp5, "false_negative": fn5, "true_positive": tp5, "true_negative": tn5,
        "seeded_total": tp5 + fn5, "detection_rate_by_type": {k: round(100 * a / b, 1) for k, (a, b) in by_type.items()},
        "end_to_end_without_human_confirmation": {"precision": pr, "recall": rr, "false_positive": fpr, "false_negative": fnr},
        "note": "Formatting variants (SBI vs State Bank of India, 3 date styles, 3 amount styles) are present in the set and must NOT be flagged."}

    # ---------------- 2. validity tracking
    print("[4/7] scenario 2: validity tracking", flush=True)
    tp = fp = fn = correct = total = 0
    for it, iid in zip(items, created):
        inst = db.get(Instrument, iid)
        if inst.verification != "VERIFIED":
            continue
        truth = it["bank_truth"]
        d = (date.fromisoformat(truth["maturity_date"]) - today).days
        exp = "MATURED" if d < 0 else ("EXPIRING" if d <= rules["expiring_window_days"] else "ACTIVE")
        got = services.validity(inst, today, rules)
        total += 1; correct += exp == got
        ep, gp = exp in ("EXPIRING", "MATURED"), got in ("EXPIRING", "MATURED")
        tp += ep and gp; fp += (not ep) and gp; fn += ep and (not gp)
    p, r = prf(tp, fp, fn)
    metrics["scenarios"]["2_validity_tracking"] = {"precision": p, "recall": r, "false_positive": fp, "false_negative": fn,
                                                   "classification_accuracy": round(100 * correct / total, 1), "evaluated": total}

    # ---------------- 4. renewal / replacement chain
    print("[5/7] scenario 4: renewal chain", flush=True)
    rnd = random.Random(4)
    parents = [iid for iid in created if db.get(Instrument, iid).verification == "VERIFIED" and db.get(Instrument, iid).state == "ACTIVE"]
    rnd.shuffle(parents)
    tests = []       # (child_id_or_none, true_parent_or_None)
    # note: the matching pool for suggest_parent is every ACTIVE instrument of the same institution
    def mk_child(inst_id, kind, amount, issue, mat, ref):
        c = services.create_draft(db, user, inst_id, kind, "r.pdf", b"x", run_ocr=False)
        c.d_ref_no, c.d_bank, c.d_amount, c.d_issue_date, c.d_maturity_date, c.d_lien = ref, "State Bank of India", amount, issue, mat, True
        db.flush()
        return c
    snapshot_state = {}
    for pid_ in parents[:40]:                      # true renewals: issued within +-45 days of parent maturity
        par = db.get(Instrument, pid_)
        issue = par.d_maturity_date + timedelta(days=rnd.randint(-45, 45))
        c = mk_child(par.institution_id, par.kind, float(par.d_amount) * rnd.choice([1, 1, 1.5]), issue,
                     issue + timedelta(days=365 * 8), f"RN{rnd.randint(10**9, 10**10-1)}")
        tests.append((c, par.id))
    for pid_ in parents[40:80]:                    # brand-new deposits (no parent): issue date arbitrary
        par = db.get(Instrument, pid_)
        issue = today + timedelta(days=rnd.randint(-2500, 300))
        c = mk_child(par.institution_id, par.kind, float(par.d_amount), issue, issue + timedelta(days=365 * 8), f"NW{rnd.randint(10**9, 10**10-1)}")
        tests.append((c, None))
    tp = fp = fn = 0
    for c, truth_parent in tests:
        sug = services.suggest_parent(db, c)
        sid = sug.id if sug else None
        if truth_parent is not None:
            tp += sid == truth_parent; fn += sid != truth_parent; fp += (sid is not None and sid != truth_parent)
        else:
            fp += sid is not None
    p, r = prf(tp, fp, fn)
    chain_ok = 0
    chain_n = 0
    for c, truth_parent in [t for t in tests if t[1]][:15]:     # two-level chains: lineage must return root -> child
        c.parent_id = truth_parent
        db.get(Instrument, truth_parent).state = "RENEWED"
        g = mk_child(c.institution_id, c.kind, float(c.d_amount), c.d_maturity_date, c.d_maturity_date + timedelta(days=365 * 8), f"GC{rnd.randint(10**9, 10**10-1)}")
        g.parent_id = c.id
        db.flush()
        ln = [x["id"] for x in services.lineage(db, g)]
        chain_n += 1; chain_ok += ln == [truth_parent, c.id, g.id]
    db.commit()
    metrics["scenarios"]["4_renewal_replacement"] = {"precision": p, "recall": r, "false_positive": fp, "false_negative": fn,
                                                     "true_renewals": 40, "new_deposits": 40, "lineage_chain_correct_pct": round(100 * chain_ok / chain_n, 1)}

    # ---------------- 6. authorized release gate
    print("[6/7] scenario 6: release gate", flush=True)
    rnd = random.Random(6)
    cand = [i for i in created if db.get(Instrument, i).verification == "VERIFIED" and db.get(Instrument, i).state == "ACTIVE"
            and services.validity(db.get(Instrument, i), today, rules) == "MATURED"]
    cand += [i for i in created if db.get(Instrument, i).verification == "VERIFIED" and db.get(Instrument, i).state == "ACTIVE"
             and services.validity(db.get(Instrument, i), today, rules) != "MATURED"][:10]    # not-yet-matured must be blocked
    ca = User(username="ca", password_hash="x", role="competent_authority"); db.add(ca); db.commit()
    tp = fp = fn = tn = 0
    for iid in cand:
        inst = db.get(Instrument, iid)
        req = release.open_request(db, user, inst)
        for dt in rules["release_documents"]:
            if dt != "affidavit_6" and dt != "loa_eoa" and rnd.random() < 0.8:
                db.add(Document(instrument_id=iid, doc_type=dt, filename=f"{dt}.pdf", sha256="0" * 64))
        db.flush(); db.refresh(inst)
        for cell in rules["release_clearances"]:
            r_ = rnd.random()
            if r_ < 0.85:
                release.set_clearance(db, user, req, cell, True)
            elif r_ < 0.93:
                release.set_clearance(db, user, req, cell, False)
        db.commit(); db.refresh(inst)
        have = {d.doc_type for d in inst.documents}
        should_pass = (services.validity(inst, today, rules) == "MATURED" and all(d in have for d in rules["release_documents"])
                       and all(req.clearances.get(c) is True for c in rules["release_clearances"]))
        try:
            release.decide(db, ca, inst, req, True, today); passed = True
        except ValueError:
            passed = False
        tp += (not should_pass) and (not passed); fp += should_pass and (not passed)
        fn += (not should_pass) and passed; tn += should_pass and passed
    p, r = prf(tp, fp, fn)
    metrics["scenarios"]["6_authorized_release"] = {"precision": p, "recall": r, "false_positive": fp, "false_negative": fn,
                                                    "requests": len(cand), "released": tn, "blocked_correctly": tp,
                                                    "note": "Positive class = request that must be blocked (any missing document, clearance or not yet matured)."}

    # ---------------- 3. approaching-maturity alerts (clock simulated day by day)
    print("[7/7] scenario 3: alert timeliness (simulated clock, 130 days)", flush=True)
    from app.models import Alert
    offsets = sorted(rules["reminder_offsets_days"])
    act = db.query(Instrument).filter(Instrument.state == "ACTIVE", Instrument.d_maturity_date.isnot(None)).all()
    act = [a for a in act if a.verification in ("VERIFIED", "DISCREPANCY", "MANUAL_REVIEW")]
    db.query(Alert).delete()
    db.commit()
    start, horizon = today, 130
    exp2 = set()
    for a in act:
        m = a.d_maturity_date
        left0 = (m - start).days
        if left0 < 0:
            exp2.add((a.id, "OVERDUE", start))
            continue
        crossed = [o for o in offsets if left0 <= o]
        if crossed:                                     # first run sends only the tightest threshold already crossed
            exp2.add((a.id, f"T-{min(crossed)}", start))
        for o in offsets:                               # later thresholds fire on their own calendar day
            d = m - timedelta(days=o)
            if start < d < start + timedelta(days=horizon):
                exp2.add((a.id, f"T-{o}", d))
        if m + timedelta(days=1) < start + timedelta(days=horizon):
            exp2.add((a.id, "OVERDUE", m + timedelta(days=1)))
    for k in range(horizon):
        scheduler.run_daily(db, start + timedelta(days=k), actor="eval")
    from app.models import Alert
    actual = {(al.instrument_id, al.kind, al.sent_on) for al in db.query(Alert).all()}
    tp = len(exp2 & actual); fp = len(actual - exp2); fn = len(exp2 - actual)
    p, r = prf(tp, fp, fn)
    metrics["scenarios"]["3_approaching_maturity"] = {"precision": p, "recall": r, "false_positive": fp, "false_negative": fn,
                                                      "expected_alerts": len(exp2), "delivered_on_time": tp, "instruments": len(act), "simulated_days": horizon}

    # ---------------- turnaround
    totals = [o + p_ for (_, o), p_ in zip(ocr_res, pipe_times)]
    metrics["turnaround_seconds"] = {"median": round(statistics.median(totals), 2), "p95": round(sorted(totals)[int(0.95 * len(totals)) - 1], 2),
                                     "ocr_median": round(statistics.median(o for _, o in ocr_res), 2),
                                     "pipeline_median_ms": round(1000 * statistics.median(pipe_times), 1), "n": len(totals),
                                     "note": "Upload -> OCR -> bank lookup -> discrepancy verdict, single document, single CPU core. Excludes human confirmation time."}
    metrics["audit_chain"] = audit.verify_chain(db)
    db.close()
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str))
    write_md(metrics, out / "METRICS.md")
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk in ("precision", "recall", "false_positive", "false_negative")} for k, v in metrics["scenarios"].items()}, indent=1))
    print("turnaround", metrics["turnaround_seconds"])
    return metrics


def write_md(m, path):
    names = {"1_new_fdr_submission": "1. New FDR submission (OCR pre-fill, before human confirmation)", "2_validity_tracking": "2. Validity and lock-in tracking",
             "3_approaching_maturity": "3. Approaching-maturity alerts", "4_renewal_replacement": "4. Renewal and replacement chain",
             "5_discrepancy_identification": "5. Discrepancy identification", "6_authorized_release": "6. Authorized release processing"}
    L = ["# NidhiSetu prototype metrics (synthetic test set)\n",
         f"Documents: **{m['n_documents']}** synthetic FDR/RPGF/RTGS receipts (seed {m['seed']}), 3 bank layouts, 50% clean / 30% skewed scans / 20% poor scans.  ",
         "All data is fictitious. Results describe this prototype on this synthetic set, not production performance.\n",
         "| Official scenario | Precision % | Recall % | False positives | False negatives |", "|---|---|---|---|---|"]
    for k, v in m["scenarios"].items():
        L.append(f"| {names[k]} | {v['precision']} | {v['recall']} | {v['false_positive']} | {v['false_negative']} |")
    t = m["turnaround_seconds"]
    L += ["", f"**Verification turnaround** (upload to discrepancy verdict): median **{t['median']} s**, p95 {t['p95']} s (n={t['n']}); OCR median {t['ocr_median']} s, rules+bank lookup median {t['pipeline_median_ms']} ms. {t['note']}", "",
          f"**Audit chain**: {m['audit_chain']}", "", "## Details", "```json", json.dumps(m["scenarios"], indent=2, default=str), "```"]
    path.write_text("\n".join(L))


if __name__ == "__main__":
    main(n=int(sys.argv[1]) if len(sys.argv) > 1 else 150)
