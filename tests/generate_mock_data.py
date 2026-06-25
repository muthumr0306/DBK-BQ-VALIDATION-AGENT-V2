#!/usr/bin/env python3
"""Generate mock CSV datasets for local DQ agent testing.

Run from the project root:
    python tests/generate_mock_data.py

Writes CSVs to tests/data/. Baked-in DQ issues per table:

  fact_sales        rows 500-502 missing in target, row 300 null sale_id,
                    rows 100/200 invalid transaction_status ("FAILED"),
                    rows 996-1000 orphan market_sid=99,
                    row 1 sales_amount inflated +15.00 in target (measure mismatch)

  dim_brand_scd2    target has extra active record brand_sid=999 (SCD2 drift),
                    historical rows have stale updated_at (beyond 24h SLA)

  dim_market        clean on both sides (the orphan market_sid=99 is intentionally absent)

  bq_only_table     record_id 50 and 150 each duplicated (uniqueness failure),
                    all rows stale (5 days old, SLA = 1440 min)
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

random.seed(42)

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

NOW = datetime.utcnow()
RECENT = NOW - timedelta(hours=1)
STALE = NOW - timedelta(days=5)


def fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# ── fact_sales ────────────────────────────────────────────────────────────────
STATUSES = ["POSTED"] * 80 + ["PENDING"] * 15 + ["CANCELLED"] * 5

source_sales = []
for i in range(1, 1001):
    source_sales.append({
        "sale_id": i,
        "brand_sid": random.randint(1, 30),
        "market_sid": 99 if i >= 996 else random.randint(1, 10),
        "sales_amount": round(random.uniform(10.0, 500.0), 2),
        "transaction_status": "FAILED" if i in (100, 200) else random.choice(STATUSES),
        "updated_at": fmt(RECENT),
    })

target_sales = []
for r in source_sales:
    if r["sale_id"] in (500, 501, 502):
        continue
    row = r.copy()
    if row["sale_id"] == 300:
        row["sale_id"] = None
    if row["sale_id"] == 1:
        row["sales_amount"] = round(row["sales_amount"] + 15.00, 2)
    target_sales.append(row)

pd.DataFrame(source_sales).to_csv(DATA_DIR / "source_fact_sales.csv", index=False)
pd.DataFrame(target_sales).to_csv(DATA_DIR / "target_fact_sales.csv", index=False)
print(f"fact_sales        source={len(source_sales):>4}  target={len(target_sales):>4}")


# ── dim_brand_scd2 ────────────────────────────────────────────────────────────
brand_rows = []
for b in range(1, 31):
    brand_rows.append({
        "brand_sid": b * 2 - 1,
        "brand_name": f"Brand {b:02d} (historical)",
        "active_indicator": False,
        "source_system": "BRANDING FOUNDATION",
        "updated_at": fmt(STALE),
    })
    brand_rows.append({
        "brand_sid": b * 2,
        "brand_name": f"Brand {b:02d}",
        "active_indicator": True,
        "source_system": "BRANDING FOUNDATION",
        "updated_at": fmt(RECENT),
    })

target_brand_rows = brand_rows.copy()
target_brand_rows.append({
    "brand_sid": 999,
    "brand_name": "Brand 01 (duplicate active)",
    "active_indicator": True,
    "source_system": "BRANDING FOUNDATION",
    "updated_at": fmt(STALE),
})

pd.DataFrame(brand_rows).to_csv(DATA_DIR / "source_dim_brand_scd2.csv", index=False)
pd.DataFrame(target_brand_rows).to_csv(DATA_DIR / "target_dim_brand_scd2.csv", index=False)
print(f"dim_brand_scd2    source={len(brand_rows):>4}  target={len(target_brand_rows):>4}")


# ── dim_market ────────────────────────────────────────────────────────────────
market_rows = [
    {"market_sid": i, "market_name": name, "region": region, "updated_at": fmt(RECENT)}
    for i, (name, region) in enumerate([
        ("North America", "NA"), ("Europe", "EU"), ("Asia Pacific", "APAC"),
        ("Latin America", "LATAM"), ("Middle East", "ME"), ("Africa", "AF"),
        ("South Asia", "SA"), ("East Asia", "EA"), ("Oceania", "OC"), ("Global", "GL"),
    ], start=1)
]

pd.DataFrame(market_rows).to_csv(DATA_DIR / "source_dim_market.csv", index=False)
pd.DataFrame(market_rows).to_csv(DATA_DIR / "target_dim_market.csv", index=False)
print(f"dim_market        source={len(market_rows):>4}  target={len(market_rows):>4}  (clean)")


# ── bq_only_table ─────────────────────────────────────────────────────────────
bq_rows = [
    {
        "record_id": i,
        "category": random.choice(["A", "B", "C"]),
        "value": round(random.uniform(1.0, 1000.0), 2),
        "updated_at": fmt(STALE),
    }
    for i in range(1, 201)
]
bq_rows.append(bq_rows[49].copy())   # duplicate record_id=50
bq_rows.append(bq_rows[149].copy())  # duplicate record_id=150

pd.DataFrame(bq_rows).to_csv(DATA_DIR / "target_bq_only_table.csv", index=False)
print(f"bq_only_table                target={len(bq_rows):>4}  (2 dupes, all stale)")

print(f"\nMock data written to: {DATA_DIR.resolve()}")
