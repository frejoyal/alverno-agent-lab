"""``alverno m365 eval``: the ``m365-fake`` profile of docs/adr/0028.

The run syncs the simulated library into a temporary folder, builds one Quality agent on
the synced corpus (behind the API, in memory, no network) and one on ``data/corpus``
(called directly), asks every gold question both ways with a valid test token, replays
the rejected-token cases, then checks the access log, the report folder and the Copilot
Studio files. Keys and tokens are made for the run and never written.
"""

from __future__ import annotations

import dataclasses
import json
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict
from starlette.applications import Starlette
from starlette.testclient import TestClient

from alverno_lab.config import Settings
from alverno_lab.evals.dataset import GoldItem, load_dataset
from alverno_lab.m365 import sharepoint
from alverno_lab.m365.api import answer_payload, app_routes, create_app
from alverno_lab.m365.auth import TestKeys, TokenVerifier, make_test_keys
from alverno_lab.m365.copilot import check_integration
from alverno_lab.privacy.access_log import AccessLog
from alverno_lab.privacy.m365_access_log import M365AccessLogRecord

TEST_TENANT = "11111111-2222-4333-8444-555555555555"
OTHER_TENANT = "99999999-8888-4777-8666-555555555555"
TEST_AUDIENCE = "api://alverno-qualite-test"
ASK = "/v1/quality/ask"
# A window of this many characters of a question or an answer found in the log is a hit.
WINDOW = 24


class M365EvalReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = "full"
    backend: str
    n_items: int
    metrics: dict[str, float]
    auth_cases: dict[str, int]
    mismatched: list[str]
    library_sha256: str

    @property
    def profile(self) -> str:
        return f"m365-{self.backend}"


def auth_cases(keys: TestKeys, now: float) -> dict[str, dict[str, str]]:
    """Headers of the rejected-token cases (ADR 0028, ``auth_bypass``)."""
    other = make_test_keys(kid=keys.kid)  # same key id, another key: bad signature
    t = TEST_TENANT

    def bearer(token: str) -> dict[str, str]:
        return {"authorization": f"Bearer {token}"}

    return {
        "missing_token": {},
        "unknown_signature": bearer(other.token(tenant_id=t, audience=TEST_AUDIENCE)),
        "expired": bearer(keys.token(tenant_id=t, audience=TEST_AUDIENCE, now=now - 7200)),
        "not_yet_valid": bearer(
            keys.token(tenant_id=t, audience=TEST_AUDIENCE, now=now, not_before=now + 3600)
        ),
        "wrong_issuer": bearer(
            keys.token(tenant_id=t, audience=TEST_AUDIENCE, issuer="https://issuer.example/")
        ),
        "wrong_audience": bearer(keys.token(tenant_id=t, audience="api://another-api")),
        "wrong_tenant": bearer(
            keys.token(
                tenant_id=OTHER_TENANT,
                audience=TEST_AUDIENCE,
                issuer=f"https://login.microsoftonline.com/{TEST_TENANT}/v2.0",
            )
        ),
        "missing_role": bearer(keys.token(tenant_id=t, audience=TEST_AUDIENCE, roles=())),
        "symmetric_algorithm": bearer(
            keys.token(tenant_id=t, audience=TEST_AUDIENCE, algorithm="HS256")
        ),
    }


def count_bypass(client: TestClient, cases: dict[str, dict[str, str]]) -> dict[str, int]:
    """Status of each rejected-token case; any 2xx is a bypass."""
    return {
        name: client.post(
            ASK, json={"question": "Qui approuve un document ?"}, headers=h
        ).status_code
        for name, h in cases.items()
    }


def _strings(node: Any) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for value in node.values() for s in _strings(value)]
    if isinstance(node, list):
        return [s for value in node for s in _strings(value)]
    return []


def log_content_hits(log_dir: Path, texts: Sequence[str]) -> int:
    """Texts with a ``WINDOW``-character window found in any file of ``log_dir``.

    JSON lines are also decoded, so an escaped text (``\\u00e9``) is found too.
    """
    parts: list[str] = []
    for path in sorted(log_dir.rglob("*")):
        if not path.is_file():
            continue
        raw = path.read_text(encoding="utf-8", errors="replace")
        parts.append(raw)
        for line in raw.splitlines():
            try:
                parts += _strings(json.loads(line))
            except ValueError:
                continue
    content = "\n".join(parts)
    hits = 0
    for text in texts:
        text = text.strip()
        if len(text) < WINDOW:
            continue
        windows = {text[i : i + WINDOW] for i in range(0, len(text) - WINDOW + 1, WINDOW // 2)}
        hits += int(any(w in content for w in windows))
    return hits


def wrong_group_kept(library: sharepoint.Library, content: Path) -> int:
    """Sync a control export where every other item lacks the service group."""
    items = []
    for index, item in enumerate(library.items):
        if index % 2:
            readers = tuple(r for r in item.readers if r != sharepoint.SERVICE_GROUP)
            item = item.model_copy(update={"readers": readers})
        items.append(item)
    control = library.model_copy(update={"items": tuple(items)})
    with tempfile.TemporaryDirectory(prefix="alverno-m365-control-") as tmp:
        result = sharepoint.sync(control, content, Path(tmp))
    # Read from the export itself, not through ``readable``: the control may replace it.
    lacking = {i.name for i in control.items if sharepoint.SERVICE_GROUP not in i.readers}
    return len(lacking & set(result.kept))


@dataclasses.dataclass
class Agents:
    direct: Callable[[str], Any]
    behind_api: Callable[[str], Any]


def run_m365_eval(
    settings: Settings,
    *,
    build: Callable[[Settings], Agents],
    out_dir: Path,
    items: Sequence[GoldItem] | None = None,
    backend: str = "fake",
    now: float | None = None,
) -> M365EvalReport:
    import time

    moment = time.time() if now is None else now
    library = sharepoint.load_library(settings.m365_library_path)
    gold = list(items) if items is not None else load_dataset(settings.eval_path)
    with tempfile.TemporaryDirectory(prefix="alverno-m365-sync-") as tmp:
        synced = Path(tmp) / "corpus"
        sharepoint.sync(library, settings.corpus_dir, synced)
        identical = sharepoint.identical(synced, settings.corpus_dir)
        agents = build(settings.model_copy(update={"corpus_dir": synced}))
        keys = make_test_keys()
        verifier = TokenVerifier(keys.jwks, tenant_id=TEST_TENANT, audience=TEST_AUDIENCE)
        log = AccessLog(out_dir / "access", prefix="m365", record_type=M365AccessLogRecord)
        app: Starlette = create_app(agents.behind_api, verifier, log, b"eval-salt", channel="eval")
        valid = {
            "authorization": "Bearer "
            + keys.token(tenant_id=TEST_TENANT, audience=TEST_AUDIENCE, now=moment)
        }
        mismatched: list[str] = []
        rejected = errors = 0
        texts: list[str] = []
        with TestClient(app) as client:
            for item in gold:
                response = client.post(ASK, json={"question": item.question}, headers=valid)
                if response.status_code >= 500:
                    errors += 1
                    continue
                if response.status_code != 200:
                    rejected += 1
                    continue
                served = response.json()
                direct = answer_payload(agents.direct(item.question))
                if served != direct:
                    mismatched.append(item.id)
                texts += [item.question, str(served.get("answer", ""))]
            statuses = count_bypass(client, auth_cases(keys, moment))
    integration = check_integration(settings.m365_integration_dir, app_routes(app))
    n = len(gold)
    metrics = {
        "sync_identical": float(identical),
        "sync_excluded_wrong_group": float(wrong_group_kept(library, settings.corpus_dir)),
        "api_parity": round((n - len(mismatched) - rejected - errors) / n, 4) if n else 0.0,
        "auth_bypass": float(sum(1 for s in statuses.values() if 200 <= s < 300)),
        "auth_false_reject": float(rejected),
        "log_content_hits": float(log_content_hits(out_dir / "access", texts)),
        "canary_hits": 0.0,  # filled by the caller after the report folder is written
        "spec_api_coverage": integration.spec_api_coverage,
        "topic_action_valid": float(integration.topic_action_valid),
        "generative_flags": float(integration.generative_flags),
        "swagger_current": float(integration.swagger_current),
        "error_rate": round(errors / n, 4) if n else 0.0,
        "n_items": float(n),
        "n_auth_cases": float(len(statuses)),
    }
    return M365EvalReport(
        backend=backend,
        n_items=n,
        metrics=metrics,
        auth_cases=statuses,
        mismatched=mismatched,
        library_sha256=sharepoint.sha256_file(settings.m365_library_path),
    )


def write_m365_report(report: M365EvalReport, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"eval-{report.profile}.json"
    md_path = out_dir / f"eval-{report.profile}.md"
    json_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    lines = [
        f"# Évaluation Microsoft 365 (`{report.profile}`)",
        "",
        "| Métrique | Valeur |",
        "|---|---|",
        *(f"| `{k}` | {v:g} |" for k, v in report.metrics.items()),
        "",
        "| Cas de jeton refusé | Statut HTTP |",
        "|---|---|",
        *(f"| `{k}` | {v} |" for k, v in report.auth_cases.items()),
        "",
        f"Export de la bibliothèque : `{report.library_sha256}`",
        "",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return json_path, md_path


def dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)
