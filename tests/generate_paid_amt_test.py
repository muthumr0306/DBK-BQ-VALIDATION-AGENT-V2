#!/usr/bin/env python3
"""Generate a single-table mock for testing per-key value mismatch detection.

Run from the project root:
    python tests/generate_paid_amt_test.py

Writes two CSVs to tests/data/. Baked-in DQ issue:

  paid_amt_test
    - Keyed by order_id. ALL keys present on both sides (no missing rows).
    - For 10 of the 50 order_ids, paid_amt DIFFERS in target (value drift on
      matching keys) -> should be caught by row_reconciliation
      (mapped_rows_when_small) and the paid_amt measure reconciliation.
    - Columns share names on both sides (focus is purely value mismatch).
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

random.seed(11)

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

NOW = datetime.utcnow()
RECENT = NOW - timedelta(hours=2)


def fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# ── source: 50 orders, each with a paid amount ────────────────────────────────
source_orders = []
for i in range(1, 51):
    source_orders.append({
        "order_id": i,
        "paid_amt": round(random.uniform(20.0, 800.0), 2),
        "updated_at": fmt(RECENT),
    })

# ── target: same keys, but paid_amt mutated on 10 order_ids ───────────────────
mismatch_ids = set(random.sample([r["order_id"] for r in source_orders], 10))

target_orders = []
for r in source_orders:
    paid = r["paid_amt"]
    if r["order_id"] in mismatch_ids:
        # shift the amount by a noticeable delta (mix of up/down, net non-zero)
        delta = round(random.uniform(15.0, 120.0), 2)
        paid = round(paid + delta, 2)
    target_orders.append({
        "order_id": r["order_id"],
        "paid_amt": paid,
        "updated_at": r["updated_at"],
    })

pd.DataFrame(source_orders).to_csv(DATA_DIR / "source_paid_amt_test.csv", index=False)
pd.DataFrame(target_orders).to_csv(DATA_DIR / "target_paid_amt_test.csv", index=False)

src_total = round(sum(r["paid_amt"] for r in source_orders), 2)
tgt_total = round(sum(r["paid_amt"] for r in target_orders), 2)
print(f"paid_amt_test  source={len(source_orders)}  target={len(target_orders)}  (all keys matched)")
print(f"  paid_amt mismatched on {len(mismatch_ids)} order_ids: {sorted(mismatch_ids)}")
print(f"  total paid_amt: source={src_total}  target={tgt_total}  diff={round(tgt_total-src_total,2)}")
print(f"\nMock data written to: {DATA_DIR.resolve()}")
