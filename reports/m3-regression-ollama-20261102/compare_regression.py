"""Item-by-item regression check (ADR 0015, « Non-régression des modules 1 et 2»).

Usage: compare_regression.py <reference.json> <new.json> <out.json>
Compares refused, refusal_reason and citations per item id; prints ids and field names
only (never answers or questions), and writes the comparison to <out.json>.
"""

import json
import sys
from pathlib import Path

FIELDS = ("refused", "refusal_reason", "citations")


def load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


ref, new, out = load(sys.argv[1]), load(sys.argv[2]), sys.argv[3]
ref_items = {it["id"]: it for it in ref["items"]}
new_items = {it["id"]: it for it in new["items"]}
diffs = []
for item_id in sorted(set(ref_items) | set(new_items)):
    a, b = ref_items.get(item_id), new_items.get(item_id)
    if a is None or b is None:
        diffs.append({"id": item_id, "field": "missing", "in_reference": a is not None})
        continue
    for field in FIELDS:
        if a.get(field) != b.get(field):
            diffs.append(
                {"id": item_id, "field": field, "reference": a.get(field), "new": b.get(field)}
            )
errors = [i for i, it in new_items.items() if it.get("error")]
result = {
    "reference": sys.argv[1],
    "new": sys.argv[2],
    "n_items": len(new_items),
    "n_reference_items": len(ref_items),
    "fields": list(FIELDS),
    "n_differences": len(diffs),
    "differences": diffs,
    "errored_items": errors,
}
Path(out).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"items {len(new_items)} (reference {len(ref_items)}); differences {len(diffs)}")
for d in diffs:
    print(d["id"], d["field"])
print("errored:", errors)
