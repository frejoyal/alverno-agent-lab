"""Compare every item field of the reference and new reports, latencies excepted.

Usage: full_compare.py <folder>. Prints profile names, ids and field names only.
"""

import json
import sys
from pathlib import Path

FILES = {
    "m1": "eval-full-ollama.json",
    "m2": "eval-hr-full-ollama.json",
    "svc": "eval-svc-full-ollama.json",
    "maint": "eval-maint-full-ollama.json",
}
SKIP = {"latency_ms"}

root = Path(sys.argv[1])
for name, filename in FILES.items():
    ref = json.loads((root / "reference" / name / filename).read_text(encoding="utf-8"))
    new = json.loads((root / "new" / name / filename).read_text(encoding="utf-8"))
    ref_items = {it["id"]: it for it in ref["items"]}
    new_items = {it["id"]: it for it in new["items"]}
    diffs = []
    for item_id in sorted(set(ref_items) | set(new_items)):
        a, b = ref_items.get(item_id, {}), new_items.get(item_id, {})
        for field in sorted((set(a) | set(b)) - SKIP):
            if a.get(field) != b.get(field):
                diffs.append((item_id, field))
    print(f"{name}: items {len(new_items)} / {len(ref_items)}; differing (item, field) pairs: {len(diffs)}")
    for d in diffs:
        print("  ", *d)
