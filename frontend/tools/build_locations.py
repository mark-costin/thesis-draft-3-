"""Builds patient_portal/data/ph_locations.json by hand (the app also does this by itself on first start).

    python tools/build_locations.py

Needs internet. Source: https://psgc.gitlab.io/api/ (Philippine Standard Geographic Code).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from patient_portal.locations import DATA_FILE, build_from_psgc, _count  # noqa: E402

data = build_from_psgc()
DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"Wrote {DATA_FILE}: {len(data)} provinces, {sum(len(v) for v in data.values())} cities, {_count(data)} barangays")
