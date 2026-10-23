# NidhiSetu prototype metrics (synthetic test set)

Documents: **150** synthetic FDR/RPGF/RTGS receipts (seed 26265), 3 bank layouts, 50% clean / 30% skewed scans / 20% poor scans.  
All data is fictitious. Results describe this prototype on this synthetic set, not production performance.

| Official scenario | Precision % | Recall % | False positives | False negatives |
|---|---|---|---|---|
| 1. New FDR submission (OCR pre-fill, before human confirmation) | 96.4 | 95.4 | 32 | 41 |
| 5. Discrepancy identification | 100.0 | 100.0 | 0 | 0 |
| 2. Validity and lock-in tracking | 100.0 | 100.0 | 0 | 0 |
| 4. Renewal and replacement chain | 86.7 | 97.5 | 6 | 1 |
| 6. Authorized release processing | 100.0 | 100.0 | 0 | 0 |
| 3. Approaching-maturity alerts | 100.0 | 100.0 | 0 | 0 |

**Verification turnaround** (upload to discrepancy verdict): median **3.52 s**, p95 4.51 s (n=150); OCR median 3.51 s, rules+bank lookup median 9.6 ms. Upload -> OCR -> bank lookup -> discrepancy verdict, single document, single CPU core. Excludes human confirmation time.

**Audit chain**: {'valid': True, 'checked': 849, 'head': '577fdcdb3c0e69874f4b87eb2f870f119f61d6a60ab6696f4abb9747472cf835'}

## Details
```json
{
  "1_new_fdr_submission": {
    "precision": 96.4,
    "recall": 95.4,
    "false_positive": 32,
    "false_negative": 41,
    "field_total": 900,
    "by_scan_quality": {
      "skew": {
        "precision": 100.0,
        "recall": 100.0,
        "docs": 41
      },
      "clean": {
        "precision": 100.0,
        "recall": 100.0,
        "docs": 72
      },
      "poor": {
        "precision": 85.0,
        "recall": 81.5,
        "docs": 37
      }
    },
    "by_field_recall": {
      "ref_no": 89.3,
      "bank": 96.0,
      "amount": 88.0,
      "issue_date": 100.0,
      "maturity_date": 99.3,
      "lien": 100.0
    },
    "documents_fully_correct_pct": 80.0
  },
  "5_discrepancy_identification": {
    "precision": 100.0,
    "recall": 100.0,
    "false_positive": 0,
    "false_negative": 0,
    "true_positive": 43,
    "true_negative": 107,
    "seeded_total": 43,
    "detection_rate_by_type": {
      "lien": 100.0,
      "not_in_bank": 100.0,
      "maturity_date": 100.0,
      "amount": 100.0
    },
    "end_to_end_without_human_confirmation": {
      "precision": 68.3,
      "recall": 100.0,
      "false_positive": 20,
      "false_negative": 0
    },
    "note": "Formatting variants (SBI vs State Bank of India, 3 date styles, 3 amount styles) are present in the set and must NOT be flagged."
  },
  "2_validity_tracking": {
    "precision": 100.0,
    "recall": 100.0,
    "false_positive": 0,
    "false_negative": 0,
    "classification_accuracy": 100.0,
    "evaluated": 107
  },
  "4_renewal_replacement": {
    "precision": 86.7,
    "recall": 97.5,
    "false_positive": 6,
    "false_negative": 1,
    "true_renewals": 40,
    "new_deposits": 40,
    "lineage_chain_correct_pct": 100.0
  },
  "6_authorized_release": {
    "precision": 100.0,
    "recall": 100.0,
    "false_positive": 0,
    "false_negative": 0,
    "requests": 21,
    "released": 3,
    "blocked_correctly": 18,
    "note": "Positive class = request that must be blocked (any missing document, clearance or not yet matured)."
  },
  "3_approaching_maturity": {
    "precision": 100.0,
    "recall": 100.0,
    "false_positive": 0,
    "false_negative": 0,
    "expected_alerts": 221,
    "delivered_on_time": 221,
    "instruments": 114,
    "simulated_days": 130
  }
}
```