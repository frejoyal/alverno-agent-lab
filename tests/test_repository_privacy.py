"""Repository-wide privacy checks (spec §11 step 4) and the canary scan of artifacts.

Every PI-like value the detectors find in ``data/``, ``docs/``, ``tests/``, README.md
and AGENTS.md must be synthetic by rule (data/eval/synthetic_identities.yaml, D14), a
role mailbox, or the allowed author name. Failures name a file, a line, a type and a
rule, never the value: the holdout is scanned too, and it stays unseen (ADR 0010).
"""

from __future__ import annotations

import ast
import codecs
import json
import re
from functools import cache
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from alverno_lab.cli import app
from alverno_lab.privacy import (
    Canary,
    PISpan,
    PIType,
    PrivacyContext,
    canary_in,
    detect_pi,
    luhn_valid,
    normalize,
    scan_paths,
)

from .conftest import EVAL_DATA, REPO_ROOT, case_id

SCOPE = [REPO_ROOT / p for p in ("data", "docs", "tests", "README.md", "AGENTS.md")]
TEXT_SUFFIXES = frozenset({".md", ".py", ".json", ".jsonl", ".yaml", ".yml", ".txt", ".toml"})
PRIVACY_SRC = REPO_ROOT / "src" / "alverno_lab" / "privacy"
# One-shot holdouts (ADR 0010, ADR 0015, ADR 0020): never read by any test that looks at
# their text. The module 3 and module 5 names are listed before the files exist (step I1).
HOLDOUTS = frozenset(
    {"hr_holdout.jsonl", "svc_holdout.jsonl", "maint_holdout.jsonl", "dir_holdout.jsonl"}
)
# Keys whose values are free text a screen or a guard reads: HR questions, the module 3
# operator requests and the edited text of an approval script, at any nesting depth.
QUESTION_KEYS = ("question", "text", "request", "edited_text")
BENCH = REPO_ROOT / "reports" / "bench-ollama-20261031-125148"
# Capitalised terms the detectors read as a name but that are not people. Each entry is
# justified here, so the rule stays strict:
# - « Claude Code » is a coding-assistant product named in AGENTS.md and ADR 0002.
# - « Rockwell » is a hardness scale in a Quality question (q038); only the question-mode
#   pass, which runs the N3 heuristic with the HR vocabulary, reads it as a name.
NOT_PEOPLE = frozenset({"claude code", "rockwell"})
# - « Roy » is a very common Québec surname used by a frozen module-1 test probe
#   (tests/test_graph.py); it is shorter than the 4-letter canary rule, hence not listed
#   in common_surnames, which only holds canary-grade surnames.
MODULE1_PROBE_NAMES = frozenset({"roy"})


@cache
def synthetic() -> dict[str, Any]:  # Any: the rules file is free-form YAML
    raw = yaml.safe_load((EVAL_DATA / "synthetic_identities.yaml").read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return raw


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


@cache
def name_words() -> frozenset[str]:
    syn = synthetic()
    names = [
        *syn["names"]["common_given_names"],
        *syn["names"]["hard_nicknames"],
        *syn["common_surnames"],
        *syn["common_word_surnames_hard_only"],
    ]
    return frozenset(normalize(n) for n in names) | MODULE1_PROBE_NAMES


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def author_files() -> set[str]:
    """Repository paths where the author's name may appear (root files only)."""
    allowed = synthetic()["author_attribution"]["allowed_files"]
    return {*allowed, "data/eval/synthetic_identities.yaml"}


def is_synthetic(span: PISpan, value: str, path: Path) -> bool:
    """D14 rule of ``span``'s type, applied to its value."""
    syn = synthetic()
    digits = _digits(value)
    match span.type:
        case PIType.NAS:
            return value in syn["nas"]["examples"] or not luhn_valid(digits)
        case PIType.NAM:
            return value.upper().startswith(syn["nam_prefix"])
        case PIType.COURRIEL:
            return value.lower().endswith(tuple(syn["email_suffixes"]))
        case PIType.TELEPHONE:
            phones = syn["phones"]
            area, exchange, line = digits[-10:-7], digits[-7:-4], digits[-4:]
            return (
                area in phones["area_codes"]
                and exchange == phones["exchange"]
                and phones["line_min"] <= line[:4] <= phones["line_max"]
            ) or _phone_with_extension(value, phones)
        case PIType.MATRICULE:
            # The ALV format (a space may replace the hyphen, M12), or its 5-digit part
            # written after « matricule » (that context rule only).
            alv = re.sub(r"(?i)^alv ", "ALV-", value)
            return re.fullmatch(syn["matricule_pattern"], alv, re.IGNORECASE) is not None or (
                span.rule == "matricule.context" and re.fullmatch(r"\d{5}", value) is not None
            )
        case PIType.CODE_POSTAL:
            code = value.upper().replace(" ", "")
            rules = syn["postal_codes"]
            return code[:3] in rules["forward_sortation_areas"] and code[3:] == rules["local_part"]
        case PIType.ADRESSE:
            streets = [s.split(" ", 1)[1] for s in syn["fictional_streets"]]
            return any(normalize(s) in normalize(value) for s in streets)
        case PIType.DATE_NAISSANCE:
            return True  # any date; the rule is never to pair it with a name
        case PIType.COMPTE_BANCAIRE:
            full = re.fullmatch(r"(\d{5})[ -]?(\d{3})[ -]?(\d{7,12})", value)
            if full:
                return full.group(2) == syn["bank_accounts"]["institution"]
            return digits.startswith(syn["bank_accounts"]["account_prefix"])
        case PIType.NUMERO:
            return len(digits) != 9 or not luhn_valid(digits)
        case PIType.NOM:
            norm = normalize(value)
            if norm in NOT_PEOPLE:
                return True
            author = syn["author_attribution"]
            if norm == normalize(author["name"]):
                return _rel(path) in author_files()
            return all(w in name_words() for w in norm.split())
    return False  # pragma: no cover - every type is handled above


def _phone_with_extension(value: str, phones: dict[str, Any]) -> bool:
    match = re.match(r"(.*?)(?:,?\s*(?:poste|p\.)\s*\d{1,5})$", value, re.IGNORECASE)
    if match is None:
        return False
    digits = _digits(match.group(1))[-10:]
    return (
        digits[:3] in phones["area_codes"]
        and digits[3:6] == phones["exchange"]
        and phones["line_min"] <= digits[6:] <= phones["line_max"]
    )


def scanned_files() -> list[Path]:
    files: list[Path] = []
    for root in SCOPE:
        candidates = [root] if root.is_file() else sorted(root.rglob("*"))
        files += [
            p
            for p in candidates
            if p.is_file() and p.suffix in TEXT_SUFFIXES and "__pycache__" not in p.parts
        ]
    return files


def test_every_pi_like_value_in_the_repository_is_synthetic(
    privacy_ctx: PrivacyContext,
) -> None:
    violations: list[str] = []
    checked = 0
    for path in scanned_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for span in detect_pi(text, privacy_ctx, mode="document"):
            checked += 1
            if not is_synthetic(span, text[span.start : span.end], path):
                line = text.count("\n", 0, span.start) + 1
                rel = path.relative_to(REPO_ROOT).as_posix()
                # Location, type and rule only: never the value.
                violations.append(f"{rel}:{line}: {span.type.value} ({span.rule})")
    assert checked > 100  # the scan really ran over the evaluation sets
    assert not violations, violations


def test_the_author_name_is_only_in_the_allowed_files() -> None:
    author = normalize(synthetic()["author_attribution"]["name"]).split()[-1]
    # By repository path: a README.md under data/ or tests/fixtures/ is not exempt.
    allowed = author_files()
    offenders = [
        _rel(p)
        for p in scanned_files()
        if _rel(p) not in allowed
        and re.search(rf"\b{author}\b", normalize(p.read_text(encoding="utf-8", errors="replace")))
        and p.name != Path(__file__).name
    ]
    assert not offenders, offenders


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level:
            # privacy/ sits one level below alverno_lab: level 2 means alverno_lab.
            base = ["alverno_lab", "privacy"][: max(0, 3 - node.level)]
            modules.add(".".join([*base, *([node.module] if node.module else [])]))
            modules.update(".".join([*base, alias.name]) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Call) and _is_dynamic_import(node.func):
            modules.add("<dynamic import>")
    return modules


def _is_dynamic_import(func: ast.expr) -> bool:
    if isinstance(func, ast.Name):
        return func.id in {"__import__", "import_module"}
    return isinstance(func, ast.Attribute) and func.attr == "import_module"


def test_privacy_package_depends_on_nothing_above_it() -> None:
    forbidden = (
        "alverno_lab.agents",
        "alverno_lab.cli",
        "alverno_lab.evals",
        "alverno_lab.pipeline",
        "alverno_lab.hr_pipeline",
        "alverno_lab.ops_pipeline",
        "alverno_lab.connectors",
        "alverno_lab.tools",
    )
    sources = sorted(PRIVACY_SRC.glob("*.py"))
    assert sources
    for path in sources:
        bad = sorted(
            m for m in _imports(path) if m.startswith(forbidden) or m == "<dynamic import>"
        )
        assert not bad, f"{path.name} imports {bad}"


def test_privacy_package_never_reads_the_synthetic_rules() -> None:
    for path in sorted(PRIVACY_SRC.rglob("*")):
        if path.is_file() and path.suffix in {".py", ".txt"}:
            assert "synthetic_identities" not in path.read_text(encoding="utf-8"), path.name


def test_lexicons_are_shipped_as_package_data() -> None:
    for name in ("prenoms.txt", "allowlist.txt"):
        raw = (PRIVACY_SRC / "data" / name).read_text(encoding="utf-8")
        assert raw.endswith("\n") and "\r\n" not in raw
        lines = [line for line in raw.splitlines() if line and not line.startswith("#")]
        assert len(lines) == len(set(lines)), f"duplicate entry in {name}"


# --------------------------------------------------------------------------- canaries
@pytest.mark.parametrize(
    ("canary", "text", "hit"),
    [
        (Canary(type=PIType.NAS, value="046 454 286"), "NAS : 046-454-286.", True),
        (Canary(type=PIType.NAS, value="046 454 286"), "le 046454286", True),
        (Canary(type=PIType.NAS, value="046 454 286"), "0.046454", False),
        (Canary(type=PIType.TELEPHONE, value="819 555-0142"), "+1 (819) 555 0142", True),
        (Canary(type=PIType.TELEPHONE, value="819 555-0142"), "819, 555, 0142", False),
        (Canary(type=PIType.TELEPHONE, value="819 555-0142"), "+1 819.555.0142", True),
        (Canary(type=PIType.TELEPHONE, value="819 555-0142"), "8195550142", True),
        # Report floats (6 decimals, M8) and short numbers never match.
        (Canary(type=PIType.TELEPHONE, value="819 555-0142"), '{"mrr": 0.948213}', False),
        (Canary(type=PIType.NAS, value="384 120 577"), "1299 tokens, 0.948213", False),
        (Canary(type=PIType.MATRICULE, value="ALV-48213"), "matricule alv 48213", True),
        (Canary(type=PIType.MATRICULE, value="ALV-48213"), "ALV482130", False),
        (Canary(type=PIType.MATRICULE, value="ALV-48213"), "48213", False),
        (Canary(type=PIType.CODE_POSTAL, value="J2B 9Z9"), "secteur j2b9z9", True),
        (Canary(type=PIType.CODE_POSTAL, value="J2B 9Z9"), "J2B  9Z9", True),
        (Canary(type=PIType.CODE_POSTAL, value="J2B 9Z9"), "XJ2B 9Z9", False),
        (
            Canary(type=PIType.ADRESSE, value="245, rue des Exemples"),
            "245,  RUE des exemples",
            True,
        ),
        (
            Canary(type=PIType.COURRIEL, value="cafe.matinal@courriel.example"),
            "CAFE.MATINAL@courriel.example",
            True,
        ),
        (Canary(type=PIType.NOM, value="Isabelle Lavoie"), "Mme LAVOIE a appelé", True),
        (Canary(type=PIType.NOM, value="Isabelle Lavoie"), "Isabelle a appelé", False),
        (Canary(type=PIType.NOM, value="Marc Bergeron"), "les Bergeronnettes", False),
        (Canary(type=PIType.NOM, value="Stéphane Tremblay"), "tremblay-fortin", True),
    ],
)
def test_canary_matching_by_type(canary: Canary, text: str, hit: bool) -> None:
    assert canary_in(canary, text) is hit


def test_canary_repr_hides_the_value() -> None:
    canary = Canary(type=PIType.NOM, value="Isabelle Lavoie")
    assert "Lavoie" not in repr(canary) and "Lavoie" not in str(canary)


def test_scan_finds_canaries_in_raw_and_decoded_json(tmp_path: Path) -> None:
    run = tmp_path / "run"
    (run / "access").mkdir(parents=True)
    # ensure_ascii escapes « é »: only the decoded strings show the surname.
    payload = {"items": [{"answer": "Selon Mélanie Bérubé, oui."}]}
    (run / "report.json").write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
    (run / "access" / "hr-2026-11-01.jsonl").write_text('{"reason": "no_match"}\n', "utf-8")
    (run / "notes.md").write_text("Appeler le 819.555.0163 demain.\n", encoding="utf-8")
    (run / "clean.md").write_text("Rien à signaler : 0.948213, 1299.\n", encoding="utf-8")
    canaries = [
        Canary(type=PIType.NOM, value="Julie Bérubé"),
        Canary(type=PIType.TELEPHONE, value="819 555-0163"),
    ]
    hits = scan_paths([run], canaries)
    assert hits == [(run / "notes.md", PIType.TELEPHONE), (run / "report.json", PIType.NOM)]
    assert scan_paths([run / "clean.md", tmp_path / "absent"], canaries) == []


def test_scan_reads_jsonl_lines_and_tolerates_broken_json(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    lines = [json.dumps({"detail": "Selon Julie Bérubé"}), json.dumps({"n": 1}), "{broken"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # ensure_ascii escapes the accents: only the decoded line shows the surname.
    assert "Bérubé" not in path.read_text(encoding="utf-8")
    assert scan_paths([path], [Canary(type=PIType.NOM, value="Julie Bérubé")]) == [
        (path, PIType.NOM)
    ]


def test_cli_scan_reports_type_only_and_exits_1(tmp_path: Path) -> None:
    planted = tmp_path / "eval-hr-full-fake.md"
    planted.write_text("La réponse cite Isabelle Lavoie.\n", encoding="utf-8")
    gold = str(EVAL_DATA / "hr_gold.jsonl")
    result = CliRunner().invoke(app, ["privacy", "scan", str(tmp_path), "--dataset", gold])
    assert result.exit_code == 1
    assert "eval-hr-full-fake.md: nom" in result.output
    assert "Lavoie" not in result.output


def test_cli_scan_clean_folder_and_default_dataset(tmp_path: Path) -> None:
    (tmp_path / "ok.md").write_text("Rien.\n", encoding="utf-8")
    env = {"ALVERNO_HR_EVAL_PATH": str(EVAL_DATA / "hr_gold.jsonl")}
    result = CliRunner().invoke(app, ["privacy", "scan", str(tmp_path)], env=env)
    assert result.exit_code == 0, result.output
    assert "No canary found (13 canaries)." in result.output
    missing = CliRunner().invoke(app, ["privacy", "scan", str(tmp_path)])
    assert missing.exit_code == 2  # data/eval/hr_gold.jsonl is relative to the working dir


@pytest.mark.skipif(not BENCH.is_dir(), reason="published Quality bench not available")
def test_published_quality_bench_holds_no_hr_canary() -> None:
    gold = str(EVAL_DATA / "hr_gold.jsonl")
    result = CliRunner().invoke(app, ["privacy", "scan", str(BENCH), "--dataset", gold])
    assert result.exit_code == 0, result.output


def test_tracebacks_never_show_locals() -> None:
    import typer

    from alverno_lab import cli

    apps = [v for v in vars(cli).values() if isinstance(v, typer.Typer)]
    assert len(apps) >= 2
    assert all(a.pretty_exceptions_show_locals is False for a in apps)


# --------------------------------------------------------------------------- review, lot 1
NBSP, NNBSP, NB_HYPHEN = chr(0xA0), chr(0x202F), chr(0x2011)
PHONE = Canary(type=PIType.TELEPHONE, value="819-555-0142")
MATRICULE = Canary(type=PIType.MATRICULE, value="ALV-48213")


@pytest.mark.parametrize(
    ("canary", "text", "hit"),
    [
        # French number typography, non-breaking hyphens and full-width digits fold first.
        (PHONE, f"819{NBSP}555{NBSP}0142", True),
        (PHONE, f"819{NNBSP}555{NNBSP}0142", True),
        (PHONE, f"(819) 555{NB_HYPHEN}0142", True),
        (PHONE, "819 - 555 - 0142", True),
        (PHONE, "819/555/0142", True),
        (PHONE, "".join(chr(0xFF10 + int(d)) for d in "8195550142"), True),
        (MATRICULE, f"ALV{NB_HYPHEN}48213", True),
        (MATRICULE, "alv" + chr(0x2013) + "48213", True),
        # Negative controls: commas, pipes and line breaks still end a run.
        (PHONE, "819, 555, 0142", False),
        (PHONE, "| 819 | 555 | 0142 |", False),
        (PHONE, "819\n555\n0142", False),
        (PHONE, "- 0.948213\n- 0.819555\n- 0.014200", False),
        (PHONE, "0.829 / 1.000 / 0.914 / 0.948", False),
    ],
    ids=case_id,
)
def test_canary_matching_reads_unicode_separators(canary: Canary, text: str, hit: bool) -> None:
    assert canary_in(canary, text) is hit


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-le", "utf-16-be", "utf-8-sig"])
def test_scan_decodes_utf16_and_bom_files(tmp_path: Path, encoding: str) -> None:
    # Windows PowerShell 5.1 « > » and Out-File write UTF-16.
    path = tmp_path / "report.md"
    data = "Appeler le 819-555-0142.\n".encode(encoding)
    if encoding == "utf-16-be":
        data = codecs.BOM_UTF16_BE + data
    path.write_bytes(data)
    assert scan_paths([path], [PHONE]) == [(path, PIType.TELEPHONE)]


def test_cli_scan_without_canaries_fails_closed(tmp_path: Path) -> None:
    items = [json.loads(line) for line in (EVAL_DATA / "hr_gold.jsonl").open(encoding="utf-8")]
    plain = next(i for i in items if not i["canaries"] and i["pair_id"] is None)
    dataset = tmp_path / "no-canary.jsonl"
    dataset.write_text(json.dumps(plain, ensure_ascii=False) + "\n", encoding="utf-8")
    (tmp_path / "ok.md").write_text("Rien.\n", encoding="utf-8")
    result = CliRunner().invoke(app, ["privacy", "scan", str(tmp_path), "--dataset", str(dataset)])
    assert result.exit_code == 2
    assert "no canary" in result.output


def test_scan_skips_env_files(tmp_path: Path) -> None:
    lavoie = Canary(type=PIType.NOM, value="Isabelle Lavoie")
    for name in (".env", ".env.local"):
        (tmp_path / name).write_text("ALVERNO_NOTE=Mme LAVOIE\n", encoding="utf-8")
    assert scan_paths([tmp_path], [lavoie]) == []
    assert scan_paths([tmp_path / ".env"], [lavoie]) == []
    (tmp_path / "notes.md").write_text("Mme LAVOIE\n", encoding="utf-8")
    assert scan_paths([tmp_path], [lavoie]) == [(tmp_path / "notes.md", PIType.NOM)]


def test_ci_uploads_reports_without_access_logs() -> None:
    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text("utf-8"))
    steps = workflow["jobs"]["test"]["steps"]
    upload = next(s for s in steps if str(s.get("uses", "")).startswith("actions/upload-artifact"))
    paths = upload["with"]["path"].split()
    # Module 3 (ADR 0015, step I10, with the maintainer's consent): access logs are left
    # out at any depth, and one scan covers the three gold sets (HR, service, maintenance).
    assert paths == ["reports/ci/", "!reports/ci/**/access/"]
    # Nothing is uploaded unless the canary scan of the same folder passed.
    assert "steps.canary_scan.outcome == 'success'" in upload["if"]
    scan = next(s for s in steps if s.get("id") == "canary_scan")
    assert scan["run"].split() == [
        *("uv", "run", "alverno", "privacy", "scan", "reports/ci"),
        *("--dataset", "data/eval/hr_gold.jsonl"),
        *("--dataset", "data/eval/svc_gold.jsonl"),
        *("--dataset", "data/eval/maint_gold.jsonl"),
    ]
    # A single scan step: no second scan the upload could skip waiting for.
    assert [s.get("id") for s in steps if "privacy scan" in str(s.get("run", ""))] == [
        "canary_scan"
    ]


def _fake_repo(root: Path) -> Path:
    """A repository layout with a placeholder holdout (never the real one) and a .env."""
    (root / "data" / "eval").mkdir(parents=True)
    (root / "data" / "hr").mkdir()
    for name in sorted(HOLDOUTS):
        (root / "data" / "eval" / name).write_text("{}\n", encoding="utf-8")
    (root / ".env").write_text("ALVERNO_LOG_LEVEL=INFO\n", encoding="utf-8")
    (root / "reports" / "ci").mkdir(parents=True)
    (root / "reports" / "ci" / "ok.md").write_text("Rien.\n", encoding="utf-8")
    return root


@pytest.mark.parametrize(
    "target",
    [
        ".",
        "data",
        "data/eval",
        *(f"data/eval/{name}" for name in sorted(HOLDOUTS)),
        "data/hr",
        ".env",
    ],
)
def test_cli_scan_refuses_data_and_env_before_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    repo = _fake_repo(tmp_path / "repo")
    monkeypatch.setattr("alverno_lab.cli._PACKAGE_ROOT", repo)

    def never(*_: object, **__: object) -> None:
        raise AssertionError("the scan read files")

    monkeypatch.setattr("alverno_lab.cli.scan_paths", never)
    monkeypatch.setattr("alverno_lab.cli.load_hr_gold", never)
    missing = str(tmp_path / "no-dataset.jsonl")
    result = CliRunner().invoke(app, ["privacy", "scan", str(repo / target), "--dataset", missing])
    assert result.exit_code == 2, result.output
    assert "unseen holdout" in result.output


def test_cli_scan_refuses_the_working_directory_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _fake_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    result = CliRunner().invoke(app, ["privacy", "scan", "."])
    assert result.exit_code == 2
    assert "unseen holdout" in result.output


def test_cli_scan_accepts_a_report_folder_of_the_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _fake_repo(tmp_path / "repo")
    monkeypatch.setattr("alverno_lab.cli._PACKAGE_ROOT", repo)
    gold = str(EVAL_DATA / "hr_gold.jsonl")
    result = CliRunner().invoke(
        app, ["privacy", "scan", str(repo / "reports" / "ci"), "--dataset", gold]
    )
    assert result.exit_code == 0, result.output
    assert "No canary found" in result.output


def _free_texts(value: object) -> list[str]:
    """Every string under a :data:`QUESTION_KEYS` key of a JSON value, at any depth."""
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in QUESTION_KEYS and isinstance(child, str):
                found.append(child)
            else:
                found.extend(_free_texts(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_free_texts(child))
    return found


def test_free_texts_reads_nested_request_and_edit_keys() -> None:
    item = {
        "id": "s001",
        "request": "Où en est la commande CMD-2026-0401 ?",
        "approval_script": [{"decision": "edit", "edited_text": "Bonjour,"}],
        "expected": {"reason": None},
    }
    assert _free_texts(item) == ["Où en est la commande CMD-2026-0401 ?", "Bonjour,"]


def test_every_holdout_file_is_listed() -> None:
    # A holdout missing from HOLDOUTS would be read by the question-mode test.
    on_disk = {path.name for path in EVAL_DATA.glob("*holdout*")}
    assert on_disk <= HOLDOUTS, sorted(on_disk - HOLDOUTS)


def test_eval_questions_are_synthetic_in_question_mode(privacy_ctx: PrivacyContext) -> None:
    # The document scan skips N3 and N4; questions are what the screen reads, so a lone
    # real surname (« Tremblay a signé ») must be caught too. The holdout stays unseen.
    violations: list[str] = []
    checked = 0
    for path in sorted(EVAL_DATA.glob("*.jsonl")):
        if path.name in HOLDOUTS:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            for text in _free_texts(item):
                for span in detect_pi(text, privacy_ctx, mode="question"):
                    checked += 1
                    if not is_synthetic(span, text[span.start : span.end], path):
                        # Id, type and rule only: never the value.
                        violations.append(
                            f"{path.name}:{item['id']}: {span.type.value} ({span.rule})"
                        )
    assert checked > 100
    assert not violations, violations


def test_layering_check_resolves_relative_and_dynamic_imports(tmp_path: Path) -> None:
    source = tmp_path / "x.py"
    source.write_text(
        "from ..agents import quality\n"
        "from . import types\n"
        "import importlib\n"
        "importlib.import_module('alverno_lab.cli')\n",
        encoding="utf-8",
    )
    found = _imports(source)
    assert "alverno_lab.agents" in found
    assert "alverno_lab.privacy.types" in found
    assert "<dynamic import>" in found
