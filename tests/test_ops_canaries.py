"""Schema-agnostic canary loader and the scan of module 3 sets (spec I4 decision 8, ADR 0015).

On the HR gold set ``load_canaries`` equals ``gold_canaries(load_hr_gold(...))``; on the
two module 3 gold sets it loads every value of every item's ``canaries`` key and never
reads ``contact_canaries``. String canaries are typed by strict full-match inference or
labelled ``texte``, and ``alverno privacy scan`` prints ``path: label``. Only the gold
sets and synthetic values are read, never a holdout. Assertion messages cite ids, labels
and rules, never a value.
"""

from __future__ import annotations

import json
from collections import Counter
from functools import cache
from pathlib import Path

import pytest
from typer.testing import CliRunner

from alverno_lab.cli import _labelled_hits, app
from alverno_lab.evals.canaries import (
    UNTYPED_LABEL,
    LoadedCanary,
    infer_canary,
    is_numeric_value,
    load_canaries,
    numeric_value_in,
    untyped_hits,
)
from alverno_lab.evals.dataset import DatasetError
from alverno_lab.evals.hr import gold_canaries, load_hr_gold
from alverno_lab.privacy.canary import Canary, canary_in, scan_paths
from alverno_lab.privacy.types import PIType

from .conftest import EVAL_DATA, case_id

HR_GOLD = EVAL_DATA / "hr_gold.jsonl"
OPS_GOLD = (EVAL_DATA / "svc_gold.jsonl", EVAL_DATA / "maint_gold.jsonl")
LABELS = frozenset({t.value for t in PIType} | {UNTYPED_LABEL})
runner = CliRunner()

# Synthetic values (data/eval/synthetic_identities.yaml).
NOTE = "Note interne synthétique numéro quatre"
EMAIL = "service.achats@client01.example"
PHONE = "819 555-0142"
STREET = "1250, rue des Exemples"
CONTACT_ONLY = "contact.seulement@client02.example"


@cache
def raw_values(path: Path) -> tuple[frozenset[str], frozenset[str]]:
    """Every value of the ``canaries`` and ``contact_canaries`` keys of a module 3 set."""
    canaries: set[str] = set()
    contacts: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            canaries.update(item["canaries"])
            contacts.update(item.get("contact_canaries", []))  # 3a only
    return frozenset(canaries), frozenset(contacts)


def write_jsonl(path: Path, *items: object) -> Path:
    lines = [item if isinstance(item, str) else json.dumps(item) for item in items]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- gold sets
def test_hr_gold_canaries_are_those_of_the_hr_loader() -> None:
    loaded = load_canaries(HR_GOLD)
    assert [lc.canary for lc in loaded] == gold_canaries(load_hr_gold(HR_GOLD))
    assert all(lc.label == lc.canary.type.value for lc in loaded)
    assert len(loaded) == 13


@pytest.mark.parametrize("path", OPS_GOLD, ids=lambda p: p.name)
def test_every_ops_canary_is_loaded_and_no_contact_canary(path: Path) -> None:
    canaries, contacts = raw_values(path)
    loaded = load_canaries(path)
    values = {lc.canary.value for lc in loaded}
    assert len(loaded) == len(values), f"{path.name}: duplicates"
    assert len(values ^ canaries) == 0, f"{path.name}: loaded values differ from the canaries"
    contact_only = contacts - canaries
    assert len(contact_only & values) == 0, f"{path.name}: a contact canary was loaded"
    if path.name.startswith("svc"):
        assert contact_only, "svc_gold: contact canaries exist"


@pytest.mark.parametrize("path", OPS_GOLD, ids=lambda p: p.name)
def test_ops_labels_match_the_canary_types(path: Path) -> None:
    loaded = load_canaries(path)
    for lc in loaded:
        assert lc.label in LABELS, lc.label
        if lc.label == UNTYPED_LABEL:
            assert lc.canary.type is PIType.ADRESSE
        else:
            assert lc.canary.type.value == lc.label
    labels = Counter(lc.label for lc in loaded)
    assert labels[UNTYPED_LABEL] > 0, f"{path.name}: untyped values"
    assert set(labels) - {UNTYPED_LABEL}, f"{path.name}: typed values"


# --------------------------------------------------------------------------- inference
@pytest.mark.parametrize(
    ("value", "label"),
    [
        (EMAIL, "courriel"),
        ("a.b+c@client03.example", "courriel"),
        ("ALV-48213", "matricule"),
        (PHONE, "telephone"),
        ("(819) 555-0142", "telephone"),
        ("+1 819 555 0142", "telephone"),
        ("819.555.0142", "telephone"),
        ("8195550142", "telephone"),
        ("J2B 9Z9", "code_postal"),
        ("j2b9z9", "code_postal"),
        ("Sylvie Gagnon", "nom"),
        ("Marc-André Tremblay", "nom"),
        ("Julie Gagnon Lavoie", "nom"),
        # Untyped: no strict full match.
        ("Gagnon Sylvie", UNTYPED_LABEL),  # first word is not a given name
        ("sylvie gagnon", UNTYPED_LABEL),  # not capitalised
        ("Sylvie", UNTYPED_LABEL),  # one word
        ("Sylvie Julie Marc Gagnon", UNTYPED_LABEL),  # four words
        ("alv-48213", UNTYPED_LABEL),
        ("ALV-482130", UNTYPED_LABEL),
        ("555-0142", UNTYPED_LABEL),  # 7 digits
        ("819 555-0142 poste 12", UNTYPED_LABEL),
        ("Courriel : service.achats@client01.example", UNTYPED_LABEL),
        ("J2B 9Z9 Drummondville", UNTYPED_LABEL),
        (STREET, UNTYPED_LABEL),
        (NOTE, UNTYPED_LABEL),
        ("184,25", UNTYPED_LABEL),
        ("stremblay3", UNTYPED_LABEL),
    ],
    ids=case_id,
)
def test_string_type_inference(value: str, label: str) -> None:
    loaded = infer_canary(value)
    assert loaded.label == label
    expected = PIType.ADRESSE if label == UNTYPED_LABEL else PIType(label)
    assert loaded.canary == Canary(type=expected, value=value)


def test_loaded_canary_repr_hides_the_value() -> None:
    loaded = infer_canary(NOTE)
    assert NOTE not in repr(loaded) and "texte" in repr(loaded)


def test_an_untyped_canary_matches_as_a_substring() -> None:
    loaded = infer_canary(NOTE)
    hit = canary_in(loaded.canary, f"Trace : {NOTE.upper()} fin")
    miss = canary_in(loaded.canary, "Note interne")
    assert hit and not miss


# --------------------------------------------------------------------------- reading
def test_only_id_and_canaries_are_read(tmp_path: Path) -> None:
    path = write_jsonl(
        tmp_path / "set.jsonl",
        {
            "id": "s900",
            "request": 12,
            "flags": "pas une liste",
            "contact_canaries": [CONTACT_ONLY, 5, {"bad": True}],
            "canaries": [NOTE, EMAIL],
            "notes": {"x": [1, None]},
        },
        "",
        {"id": "s901", "canaries": [PHONE, NOTE]},
        {"id": "s902"},
        {"id": "h900", "canaries": [{"type": "adresse", "value": STREET}]},
    )
    loaded = load_canaries(path)
    assert [lc.label for lc in loaded] == ["texte", "courriel", "telephone", "adresse"]
    assert CONTACT_ONLY not in {lc.canary.value for lc in loaded}


def test_a_set_without_canaries_loads_empty(tmp_path: Path) -> None:
    path = write_jsonl(tmp_path / "set.jsonl", {"id": "a", "canaries": []}, {"id": "b"})
    assert load_canaries(path) == []


# Each case: the lines of the file, the line number and rule of the error.
ERROR_CASES: list[tuple[list[object], int, str]] = [
    ([{"id": "a", "canaries": [NOTE]}, "{broken " + NOTE], 2, "json.invalid"),
    (['"' + NOTE + '"'], 1, "json.invalid"),
    ([[NOTE]], 1, "json.invalid"),
    (['{"id": "a", "canaries": [' + "1" * 5000 + "]}"], 1, "json.invalid"),
    (["[" * 100000 + "]" * 100000], 1, "json.invalid"),
    ([{"canaries": [NOTE]}], 1, "id.missing"),
    ([{"id": 7, "canaries": [NOTE]}], 1, "id.missing"),
    ([{"id": " ", "canaries": [NOTE]}], 1, "id.missing"),
    ([{"id": "a", "canaries": NOTE}], 1, "canaries.not_a_list"),
    ([{"id": "a", "canaries": {"value": NOTE}}], 1, "canaries.not_a_list"),
    ([{"id": "a", "canaries": None}], 1, "canaries.not_a_list"),
    ([{"id": "a", "canaries": [NOTE, 42]}], 1, "canary.invalid"),
    ([{"id": "a", "canaries": [" "]}], 1, "canary.invalid"),
    ([{"id": "a", "canaries": [None]}], 1, "canary.invalid"),
    ([{"id": "a", "canaries": [{"type": "passeport", "value": NOTE}]}], 1, "canary.invalid"),
    ([{"id": "a", "canaries": [{"type": "nom", "value": ""}]}], 1, "canary.invalid"),
    (
        [{"id": "a", "canaries": [{"type": "adresse", "value": STREET, "note": NOTE}]}],
        1,
        "canary.invalid",
    ),
]


@pytest.mark.parametrize(("lines", "lineno", "rule"), ERROR_CASES, ids=case_id)
def test_errors_name_the_line_and_rule_never_a_value(
    tmp_path: Path, lines: list[object], lineno: int, rule: str
) -> None:
    path = write_jsonl(tmp_path / "set.jsonl", *lines)
    with pytest.raises(DatasetError) as caught:
        load_canaries(path)
    message = str(caught.value)
    assert message == f"{path}:{lineno}:{rule}"
    assert caught.value.__context__ is None and caught.value.__cause__ is None


@pytest.mark.parametrize(
    ("content", "rule"),
    [
        (None, "file.missing"),
        (b"", "file.empty"),
        (b"\n \n", "file.empty"),
        (bytes([0xFF]) + b'{"id": "a"}', "file.unreadable"),
    ],
    ids=["missing", "empty", "blank", "not-utf8"],
)
def test_file_errors(tmp_path: Path, content: bytes | None, rule: str) -> None:
    path = tmp_path / "set.jsonl"
    if content is not None:
        path.write_bytes(content)
    with pytest.raises(DatasetError) as caught:
        load_canaries(path)
    assert str(caught.value) == f"{path}:0:{rule}"
    assert caught.value.__context__ is None


def test_a_directory_is_not_a_set(tmp_path: Path) -> None:
    with pytest.raises(DatasetError, match=r":0:file\.missing$"):
        load_canaries(tmp_path)


def test_canaries_are_unique_in_order_of_first_appearance(tmp_path: Path) -> None:
    path = write_jsonl(
        tmp_path / "set.jsonl",
        {"id": "a", "canaries": [EMAIL, NOTE, EMAIL]},
        {"id": "b", "canaries": [NOTE, PHONE]},
    )
    assert [lc.label for lc in load_canaries(path)] == ["courriel", "texte", "telephone"]
    assert all(isinstance(lc, LoadedCanary) for lc in load_canaries(path))


# --------------------------------------------------------------------------- CLI scan
@pytest.fixture
def ops_set(tmp_path: Path) -> Path:
    return write_jsonl(
        tmp_path / "ops.jsonl",
        {"id": "s900", "canaries": [NOTE, EMAIL], "contact_canaries": [CONTACT_ONLY]},
    )


@pytest.fixture
def hr_set(tmp_path: Path) -> Path:
    return write_jsonl(
        tmp_path / "hr.jsonl",
        {"id": "h900", "question": "Q", "canaries": [{"type": "adresse", "value": STREET}]},
    )


def leaked(output: str, *values: str) -> bool:
    return any(value in output for value in values)


def test_cli_scan_prints_the_label_of_an_untyped_canary(tmp_path: Path, ops_set: Path) -> None:
    artifacts = tmp_path / "out"
    artifacts.mkdir()
    (artifacts / "trace.jsonl").write_text(json.dumps({"x": NOTE}) + "\n", encoding="utf-8")
    (artifacts / "report.md").write_text(f"Écrire à {EMAIL}\n", encoding="utf-8")
    result = runner.invoke(app, ["privacy", "scan", str(artifacts), "--dataset", str(ops_set)])
    assert result.exit_code == 1
    lines = [line for line in result.stdout.splitlines() if line]
    assert [line.rsplit(" ", 1)[-1] for line in lines] == ["courriel", "texte"]
    assert "report.md: courriel" in lines[0] and "trace.jsonl: texte" in lines[1]
    assert "2 canary hit(s)" in result.stderr
    assert not leaked(result.stdout + result.stderr, NOTE, EMAIL)


def test_cli_scan_ignores_contact_canaries(tmp_path: Path, ops_set: Path) -> None:
    artifact = tmp_path / "draft.md"
    artifact.write_text(f"Bonjour, écrivez à {CONTACT_ONLY}.\n", encoding="utf-8")
    result = runner.invoke(app, ["privacy", "scan", str(artifact), "--dataset", str(ops_set)])
    assert result.exit_code == 0, result.output
    assert "No canary found (2 canaries)." in result.stdout


def test_cli_scan_keeps_a_typed_and_an_untyped_label_apart(tmp_path: Path, hr_set: Path) -> None:
    ops = write_jsonl(tmp_path / "ops2.jsonl", {"id": "s901", "canaries": [STREET]})
    artifact = tmp_path / "report.md"
    artifact.write_text(f"Adresse : {STREET}\n", encoding="utf-8")
    args = ["privacy", "scan", str(artifact), "--dataset", str(hr_set), "--dataset", str(ops)]
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert result.stdout.splitlines() == ["report.md: adresse", "report.md: texte"]
    assert not leaked(result.stdout + result.stderr, STREET)


def test_cli_scan_reports_a_broken_set_without_values(tmp_path: Path) -> None:
    bad = write_jsonl(tmp_path / "bad.jsonl", {"id": "a", "canaries": [NOTE, 3]})
    (tmp_path / "ok.md").write_text("Rien.\n", encoding="utf-8")
    result = runner.invoke(app, ["privacy", "scan", str(tmp_path / "ok.md"), "--dataset", str(bad)])
    assert result.exit_code == 2
    assert "bad.jsonl:1:canary.invalid" in result.stderr
    assert not leaked(result.stdout + result.stderr, NOTE)


def test_cli_scan_of_the_three_gold_sets(tmp_path: Path) -> None:
    (tmp_path / "ok.md").write_text("Rien.\n", encoding="utf-8")
    paths = (HR_GOLD, *OPS_GOLD)
    args = ["privacy", "scan", str(tmp_path / "ok.md")]
    for path in paths:
        args += ["--dataset", str(path)]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    expected = len(dict.fromkeys(lc for path in paths for lc in load_canaries(path)))
    assert f"No canary found ({expected} canaries)." in result.stdout


def test_cli_scan_help_names_the_three_kinds_of_sets() -> None:
    result = runner.invoke(app, ["privacy", "scan", "--help"])
    assert result.exit_code == 0
    # The help is drawn in a box: drop its vertical bars and rejoin the wrapped lines.
    flat = " ".join(result.stdout.replace(chr(0x2502), " ").split())
    assert "jeu de référence ou réservé (RH, service ou entretien)" in flat


# --------------------------------------------------------------------------- HR scan output
def test_an_hr_scan_prints_the_same_lines_as_before_module_3(tmp_path: Path) -> None:
    """Same hits, same order (files as walked, types sorted within a file) and same count,
    duplicates included, as ``scan_paths`` over ``gold_canaries`` (the module 2 scan)."""
    hr_canaries = gold_canaries(load_hr_gold(HR_GOLD))
    folder = tmp_path / "out"
    folder.mkdir()
    (folder / "b.txt").write_text(" ".join(c.value for c in hr_canaries[:4]), encoding="utf-8")
    (folder / "a.txt").write_text(" ".join(c.value for c in hr_canaries[4:]), encoding="utf-8")
    (folder / "c.txt").write_text("Rien.\n", encoding="utf-8")
    paths = [folder / "b.txt", folder, folder / "a.txt"]
    before = [(path, pi_type.value) for path, pi_type in scan_paths(paths, hr_canaries)]
    assert len(before) > len({p for p, _ in before})
    assert _labelled_hits(paths, load_canaries(HR_GOLD)) == before


# --------------------------------------------------------------------------- numeric values
# A short untyped number (a margin, a price) must not be found inside a longer number,
# such as a measured latency in a report: found at step I7, where a latency held a margin
# of the svc gold set by chance and failed the gate on some runs only.
MARGIN = "38.5"


@pytest.mark.parametrize(
    ("value", "numeric"),
    [
        (MARGIN, True),
        ("12,5 %", True),
        ("1 245,80 $", True),
        ("7", True),
        (NOTE, False),
        (EMAIL, False),
        ("ALV-12345", False),
    ],
    ids=case_id,
)
def test_numeric_values_are_recognised(value: str, numeric: bool) -> None:
    assert is_numeric_value(value) is numeric


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("marge de 38.5 %", True),
        ("valeur 38.5", True),
        ("(38.5)", True),
        ('{"m": 38.5}', True),
        ("latence 138.52 ms", False),
        ("latence 238.5 ms", False),
        ("valeur 38.55", False),
        ("valeur 38.5,7", False),
        ("1238.5", False),
    ],
    ids=case_id,
)
def test_a_number_counts_only_apart_from_other_digits(text: str, found: bool) -> None:
    assert numeric_value_in(MARGIN, text) is found


def test_untyped_hits_skip_a_number_inside_a_longer_one(tmp_path: Path) -> None:
    inside = tmp_path / "latency.json"
    inside.write_text(json.dumps({"latency_p50_ms": 238.52}), encoding="utf-8")
    apart = tmp_path / "leak.md"
    apart.write_text(f"Marge : {MARGIN} %\n", encoding="utf-8")
    note = tmp_path / "note.md"
    note.write_text(f"{NOTE}\n", encoding="utf-8")
    canaries = [infer_canary(MARGIN).canary, infer_canary(NOTE).canary]
    hits = untyped_hits([inside, apart, note], canaries)
    assert [(path.name, c.value == MARGIN) for path, c in hits] == [
        ("leak.md", True),
        ("note.md", False),
    ]


def test_cli_scan_does_not_flag_a_latency_that_holds_a_margin(tmp_path: Path) -> None:
    dataset = write_jsonl(tmp_path / "ops.jsonl", {"id": "s902", "canaries": [MARGIN]})
    out = tmp_path / "out"
    out.mkdir()
    (out / "report.json").write_text(json.dumps({"latency_p50_ms": 238.52}), encoding="utf-8")
    result = runner.invoke(app, ["privacy", "scan", str(out), "--dataset", str(dataset)])
    assert result.exit_code == 0, result.output
    (out / "leak.md").write_text(f"Marge : {MARGIN} %\n", encoding="utf-8")
    result = runner.invoke(app, ["privacy", "scan", str(out), "--dataset", str(dataset)])
    assert result.exit_code == 1
    lines = [line for line in result.stdout.splitlines() if line]
    assert len(lines) == 1 and lines[0].endswith("leak.md: texte")
    assert MARGIN not in result.stdout + result.stderr


def test_a_json_number_is_never_a_numeric_canary_but_a_json_string_is(tmp_path: Path) -> None:
    # Step I8: a count written as a JSON number equalled a fixture margin by chance.
    canary = infer_canary(MARGIN).canary
    metrics = tmp_path / "report.json"
    metrics.write_text(json.dumps({"n_composed": float(MARGIN)}), encoding="utf-8")
    lines = tmp_path / "trace.jsonl"
    lines.write_text(json.dumps({"latency": float(MARGIN)}) + "\n", encoding="utf-8")
    assert untyped_hits([metrics, lines], [canary]) == []
    leaked = tmp_path / "leak.json"
    leaked.write_text(json.dumps({"draft": f"Marge de {MARGIN} %"}), encoding="utf-8")
    broken = tmp_path / "broken.jsonl"
    broken.write_text(f"not json {MARGIN} here\n", encoding="utf-8")
    keyed = tmp_path / "keyed.json"
    keyed.write_text(json.dumps({MARGIN: 1}), encoding="utf-8")
    nested = tmp_path / "nested.json"
    nested.write_text(json.dumps({"a": [{"b": f"x {MARGIN}"}, 2, None]}), encoding="utf-8")
    found = [path.name for path, _ in untyped_hits([leaked, broken, keyed, nested], [canary])]
    assert found == ["leak.json", "broken.jsonl", "keyed.json", "nested.json"]
