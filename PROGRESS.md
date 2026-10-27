# NidhiSetu: build progress

Team Data_VeriGuard (ID 152167) · SIH 2026 · PS SIH26265

## Done

| Stage | What was built | Evidence |
|---|---|---|
| 1. Core platform | Institution/instrument records, OCR pre-fill (Tesseract + OpenCV deskew), declared (Column A) vs bank-verified (Column B) split, reconcile engine, T-90/60/30/15 scheduler, Clause 6.46 release gate, renewal chains, hash-chained audit log, role-based access | 20 tests, screenshots 01-05 |
| 2. Measured evaluation | 150 synthetic receipts, per-scenario precision and recall, turnaround time | `docs/METRICS.md`, `eval/` |
| 3. Release outputs | NOC / Sanction Order PDF with SHA-256 and download button | screenshot 10 |
| 4. Bureau tools | Consolidated CSV export, legacy CSV bulk import (audited), one-click sample import | screenshot 06 |
| 5. Document forensics | ELA score, tamper-suspect flag, heatmap toggle | screenshot 07, `docs/METRICS.md` |
| 6. Discrepancy handling | Show-Cause Notice PDF, manual nodal resolution for unsupported banks | screenshots 08, 09, 11 |
| 7. Hardening | Malformed password hashes handled safely | `pytest -q` |
| 8. Security review | State locks on released/approved cases, institution-scoped dashboard and alerts, upload and rule validation, bank-record protection, background maturity run, non-root container, `.dockerignore`; 39 tests passing | `tests/test_hardening.py` |

## Next

- Public live demo (Snapdeploy & Blitz Cloud) and repository made public from private on 27 Oct.
- Real CBS / NeSL bank adapters behind the existing adapter interface.
- Calibrate the ELA threshold on real scanned documents.
- Encryption at rest for the document vault; SMS / email alert gateways.

## Known limits

Synthetic data only; simulated bank API; the ELA score is a screening heuristic, not proof of tampering. See the README for the full list.
