#!/usr/bin/env python3
"""Run the DQ agent against mock local CSV data."""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Point at the mock CSVs
ROOT = Path(__file__).parent.parent
os.environ["LOCAL_DATA_DIR"] = str(ROOT / "tests" / "data")

sys.path.insert(0, str(ROOT / "src"))

from dq_agent.workflow import DQWorkflow

wf = DQWorkflow(
    root=ROOT,
    project_file="tests/config/project.yaml",
    llm_file="config/llm.yaml",
)

manifest = wf.run()

print("\n" + "=" * 60)
print(f"Run ID  : {manifest['run_id']}")
print(f"Status  : {manifest['status']}")
print(f"Tables  : {len(manifest.get('table_results', []))}")
for t in manifest.get("table_results", []):
    status = t.get("status", "?")
    pair   = t.get("pair_id", "?")
    passed = t.get("rules_passed", 0)
    failed = t.get("rules_failed", 0)
    print(f"  {status:8s}  {pair}  ({passed} passed / {failed} failed)")
print("=" * 60)
print(f"Output  : {wf.output_dir}")
