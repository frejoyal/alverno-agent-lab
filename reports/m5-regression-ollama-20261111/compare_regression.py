"""Item-by-item non-regression of modules 1 to 3 (ADR 0020, « Non-régression des modules 1 à 3 »).

Usage: compare_regression.py <folder>   (holds reference/ and new/, each with m1, m2, svc, maint)

Modules 1 and 2: refusal, refusal reason and citations of each item must be identical.
Module 3: outcome, refusal code and executed actions (turn kinds, connector calls, injected
actions) of each item must be identical; the route and the diagnosis codes are compared
too, for information. Prints ids and field names only (never answers or questions) and
writes ``comparison.json`` in the folder.
"""

import json
import sys
from pathlib import Path

PROFILES = {
    "m1": ("eval-full-ollama.json", ("refused", "refusal_reason", "citations"), ()),
    "m2": ("eval-hr-full-ollama.json", ("refused", "refusal_reason", "citations"), ()),
    "svc": (
        "eval-svc-full-ollama.json",
        ("outcome", "reason", "kinds", "connector_calls", "injected_actions"),
        ("actual_route",),
    ),
    "maint": (
        "eval-maint-full-ollama.json",
        ("outcome", "reason", "kinds", "connector_calls", "injected_actions"),
        ("actual_route", "codes"),
    ),
}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def compare(ref: dict, new: dict, fields: tuple[str, ...]) -> list[dict]:
    ref_items = {it["id"]: it for it in ref["items"]}
    new_items = {it["id"]: it for it in new["items"]}
    diffs = []
    for item_id in sorted(set(ref_items) | set(new_items)):
        a, b = ref_items.get(item_id), new_items.get(item_id)
        if a is None or b is None:
            diffs.append({"id": item_id, "field": "missing", "in_reference": a is not None})
            continue
        for field in fields:
            if a.get(field) != b.get(field):
                diffs.append(
                    {"id": item_id, "field": field, "reference": a.get(field), "new": b.get(field)}
                )
    return diffs


root = Path(sys.argv[1])
result = {}
for name, (filename, fields, info_fields) in PROFILES.items():
    ref = load(root / "reference" / name / filename)
    new = load(root / "new" / name / filename)
    diffs = compare(ref, new, fields)
    info = compare(ref, new, info_fields)
    errors = {
        "reference": [it["id"] for it in ref["items"] if it.get("error")],
        "new": [it["id"] for it in new["items"] if it.get("error")],
    }
    result[name] = {
        "n_items": len(new["items"]),
        "n_reference_items": len(ref["items"]),
        "fields": list(fields),
        "n_differences": len(diffs),
        "differences": diffs,
        "info_fields": list(info_fields),
        "n_info_differences": len(info),
        "info_differences": info,
        "errored_items": errors,
    }
    print(
        f"{name}: items {len(new['items'])} / {len(ref['items'])}; differences {len(diffs)}; "
        f"informative differences {len(info)}; errors {errors}"
    )
    for d in diffs + info:
        print("  ", d["id"], d["field"])
(root / "comparison.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
