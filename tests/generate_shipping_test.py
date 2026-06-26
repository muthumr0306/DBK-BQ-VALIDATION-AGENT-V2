#!/usr/bin/env python3
"""Generate a single-table mock for testing segment-level RCA.

Run from the project root:
    python tests/generate_shipping_test.py

Writes two CSVs to tests/data/. Baked-in DQ issues:

  shipping_segment_test
    - ALL rows where fulfillment_channel == 'shipping' are missing in target
      (headline issue — should trigger segment RCA: source-only=['shipping'])
    - ~15 'digital' rows have amt inflated by +20 in target (value drift)
    - target columns are renamed (also exercises column mapping):
        order_id -> order_key, fulfillment_channel -> channel,
        order_amount -> amt, order_ts -> load_ts
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

random.seed(7)

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

NOW = datetime.utcnow()
RECENT = NOW - timedelta(hours=2)


def fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# ── source: 300 orders across 3 fulfillment channels ──────────────────────────
# shipping = ids 1-120, pickup = 121-220, digital = 221-300
def channel_for(i: int) -> str:
    if i <= 120:
        return "shipping"
    if i <= 220:
        return "pickup"
    return "digital"


source_orders = []
for i in range(1, 301):
    source_orders.append({
        "order_id": i,
        "fulfillment_channel": channel_for(i),
        "order_amount": round(random.uniform(10.0, 500.0), 2),
        "order_ts": fmt(RECENT),
    })

# ── target: drop all 'shipping', rename cols, inflate ~15 'digital' amounts ────
digital_ids = [r["order_id"] for r in source_orders if r["fulfillment_channel"] == "digital"]
inflated_ids = set(random.sample(digital_ids, 15))

target_orders = []
for r in source_orders:
    if r["fulfillment_channel"] == "shipping":
        continue  # headline issue: entire shipping segment missing
    amt = r["order_amount"]
    if r["order_id"] in inflated_ids:
        amt = round(amt + 20.0, 2)  # value drift on a subset of digital
    target_orders.append({
        "order_key": r["order_id"],
        "channel": r["fulfillment_channel"],
        "amt": amt,
        "load_ts": r["order_ts"],
    })

pd.DataFrame(source_orders).to_csv(DATA_DIR / "source_shipping_segment_test.csv", index=False)
pd.DataFrame(target_orders).to_csv(DATA_DIR / "target_shipping_segment_test.csv", index=False)

src_ship = sum(1 for r in source_orders if r["fulfillment_channel"] == "shipping")
tgt_ship = sum(1 for r in target_orders if r["channel"] == "shipping")
print(f"shipping_segment_test  source={len(source_orders):>4}  target={len(target_orders):>4}")
print(f"  shipping rows: source={src_ship}  target={tgt_ship}  (expect 120 / 0)")
print(f"  digital rows inflated +20 in target: {len(inflated_ids)}")
print(f"\nMock data written to: {DATA_DIR.resolve()}")
