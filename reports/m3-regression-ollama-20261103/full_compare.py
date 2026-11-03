"""Compare two eval reports item by item on every model-dependent field.

Usage: full_compare.py <a.json> <b.json>. Prints item ids and field names only.
"""

import json
import sys
from pathlib import Path

IGNORED = {"latency_ms"}


def items(path: str) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {it["id"]: it for it in data["items"]}


a, b = items(sys.argv[1]), items(sys.argv[2])
diffs = [
    (i, k)
    for i in sorted(set(a) | set(b))
    for k in sorted(set(a.get(i, {})) | set(b.get(i, {})))
    if k not in IGNORED and a.get(i, {}).get(k) != b.get(i, {}).get(k)
]
print(f"items {len(a)} / {len(b)}; differing (item, field) pairs: {len(diffs)}")
for d in diffs:
    print(*d)
