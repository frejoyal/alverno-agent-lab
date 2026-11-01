"""Corpus privacy check (spec §5.7) and the organisation roles it relies on."""

from __future__ import annotations

import dataclasses
import time
from pathlib import Path

import pytest

from alverno_lab.corpus import CorpusError, Document, load_corpus, parse_document
from alverno_lab.hr_pipeline import check_hr_corpus, load_contacts, load_privacy_context
from alverno_lab.privacy import OrgRoles, PrivacyConfigError, PrivacyContext, check_corpus_privacy
from alverno_lab.privacy.documents import _AMOUNT_RE
from alverno_lab.privacy.roles import Role

from .conftest import HR_DATA, REPO_ROOT, hr_repo_settings

HR_CORPUS = HR_DATA / "corpus"
QUALITY_CORPUS = REPO_ROOT / "data" / "corpus"
TEMPLATE = HR_CORPUS / "RH-004.md"


def planted(tmp_path: Path, *, front: dict[str, str] | None = None, body: str = "") -> Path:
    """A copy of RH-004 with front-matter lines replaced and ``body`` added to the body."""
    raw = TEMPLATE.read_text(encoding="utf-8")
    for key, value in (front or {}).items():
        lines = raw.split("\n")
        lines = [f"{key}: {value}" if line.startswith(f"{key}:") else line for line in lines]
        if value == "<drop>":
            lines = [line for line in lines if not line.startswith(f"{key}:")]
        raw = "\n".join(lines)
    if body:
        raw = raw.replace("## Objet\n\n", f"## Objet\n\n{body}\n\n", 1)
    path = tmp_path / "RH-099.md"
    path.write_text(raw.replace("doc_id: RH-004", "doc_id: RH-099"), encoding="utf-8")
    return path


def errors_of(path: Path, ctx: PrivacyContext, *, hr_rules: bool = True) -> str:
    with pytest.raises(CorpusError) as info:
        check_corpus_privacy([parse_document(path)], ctx, hr_rules=hr_rules)
    return str(info.value)


# --------------------------------------------------------------------------- real corpora
def test_the_18_hr_documents_pass_every_rule() -> None:
    docs, ctx = check_hr_corpus(hr_repo_settings())
    assert len(docs) == 18
    assert ctx.k == 5


def test_the_quality_corpus_passes_rule_2() -> None:
    ctx = load_privacy_context(hr_repo_settings())
    check_corpus_privacy(load_corpus(QUALITY_CORPUS), ctx, hr_rules=False)


def test_the_quality_corpus_is_not_an_hr_corpus(privacy_ctx: PrivacyContext) -> None:
    with pytest.raises(CorpusError, match=r"privacy\.front_matter \(department\)"):
        check_corpus_privacy(load_corpus(QUALITY_CORPUS), privacy_ctx, hr_rules=True)


def test_the_template_document_is_clean(privacy_ctx: PrivacyContext, tmp_path: Path) -> None:
    check_corpus_privacy([parse_document(planted(tmp_path))], privacy_ctx)


# --------------------------------------------------------------------------- rule 1
@pytest.mark.parametrize(
    ("front", "key"),
    [
        ({"sensitivity": "1"}, "sensitivity"),
        ({"department": "qualite"}, "department"),
        ({"reviewed_by": '"la direction"'}, "reviewed_by"),
        ({"reviewed_by": "<drop>"}, "reviewed_by"),
        ({"legal_checked": '"bientôt"'}, "legal_checked"),
        ({"legal_checked": "<drop>"}, "legal_checked"),
    ],
)
def test_rule_1_front_matter(
    front: dict[str, str], key: str, privacy_ctx: PrivacyContext, tmp_path: Path
) -> None:
    message = errors_of(planted(tmp_path, front=front), privacy_ctx)
    assert f"RH-099.md:1: privacy.front_matter ({key})" in message


def test_rule_1_accepts_an_iso_date_string(privacy_ctx: PrivacyContext, tmp_path: Path) -> None:
    path = planted(tmp_path, front={"legal_checked": '"2026-10-31"'})
    check_corpus_privacy([parse_document(path)], privacy_ctx)


# --------------------------------------------------------------------------- rule 2
@pytest.mark.parametrize(
    ("sentence", "pi_type"),
    [
        ("Pour toute question, appelez le 819 555-0177.", "telephone"),
        ("Écrivez à boite.perso@domicile.test pour un suivi.", "courriel"),
        ("Le formulaire est remis à Mme Gagnon.", "nom"),
        ("Julie Bérubé coordonne les primes.", "nom"),
        ("Le matricule ALV-20417 sert d'exemple.", "matricule"),
        ("Le dossier 66204418 est archivé.", "numero"),
    ],
)
def test_rule_2_detected_pi(
    sentence: str, pi_type: str, privacy_ctx: PrivacyContext, tmp_path: Path
) -> None:
    path = planted(tmp_path, body=sentence)
    message = errors_of(path, privacy_ctx)
    line = path.read_text(encoding="utf-8").split("\n").index(sentence) + 1
    assert f"RH-099.md:{line}: privacy.pi ({pi_type})" in message
    # The detected value never appears in the error.
    for token in sentence.split():
        if any(c.isdigit() for c in token) or "@" in token or token in {"Gagnon.", "Bérubé"}:
            assert token.strip(".,") not in message


def test_rule_2_skips_role_mailboxes_and_allowlisted_terms(
    privacy_ctx: PrivacyContext, tmp_path: Path
) -> None:
    body = "Écrivez à paie@alverno.example ; la CNESST et la Fête nationale du Québec."
    check_corpus_privacy([parse_document(planted(tmp_path, body=body))], privacy_ctx)


def test_rule_2_checks_the_title(privacy_ctx: PrivacyContext, tmp_path: Path) -> None:
    path = planted(tmp_path, front={"title": '"Primes de Mme Gagnon"'})
    assert "RH-099.md:3: privacy.pi (nom)" in errors_of(path, privacy_ctx)


def test_rule_2_only_for_the_quality_corpus(privacy_ctx: PrivacyContext, tmp_path: Path) -> None:
    clean = planted(tmp_path, front={"department": "qualite", "reviewed_by": "<drop>"})
    check_corpus_privacy([parse_document(clean)], privacy_ctx, hr_rules=False)
    dirty = planted(tmp_path, body="Appelez le 819 555-0177.")
    message = errors_of(dirty, privacy_ctx, hr_rules=False)
    assert "privacy.pi (telephone)" in message
    assert "front_matter" not in message


# --------------------------------------------------------------------------- rule 3
@pytest.mark.parametrize(
    ("sentence", "role"),
    [
        ("Le cariste reçoit une prime de 2,00 $ l'heure.", "cariste"),
        ("La prime du directeur de production est de 500 $.", "directeur_production"),
        ("Un électricien touche 1,25 $ de plus.", "electricien_entretien"),
        # Roles of 3 or 4 people are small groups too (k = 5), not only single incumbents.
        ("Un estimateur reçoit 500 $ de prime.", "estimateurs"),
        ("Les dessinateurs CAO touchent 1,25 $ de plus.", "dessinateurs_cao"),
    ],
)
def test_rule_3_pay_of_a_small_role(
    sentence: str, role: str, privacy_ctx: PrivacyContext, tmp_path: Path
) -> None:
    path = planted(tmp_path, body=sentence)
    line = path.read_text(encoding="utf-8").split("\n").index(sentence) + 1
    assert f"RH-099.md:{line}: privacy.small_group_pay ({role})" in errors_of(path, privacy_ctx)


def test_rule_3_allows_amounts_for_large_roles_and_roles_without_amounts(
    privacy_ctx: PrivacyContext, tmp_path: Path
) -> None:
    body = (
        "Les soudeurs reçoivent la prime de soir de 1,50 $. Le cariste suit la formation annuelle."
    )
    check_corpus_privacy([parse_document(planted(tmp_path, body=body))], privacy_ctx)


def test_rule_3_threshold_is_below_k_exactly(privacy_ctx: PrivacyContext, tmp_path: Path) -> None:
    roles = OrgRoles.from_roles(
        [
            *privacy_ctx.roles.roles,  # the template's reviewer must stay a known role
            Role(id="r5", department="d", aliases=("régleur",), day=5, evening=0),
            Role(id="r4", department="d", aliases=("monteur",), day=4, evening=0),
        ]
    )
    ctx = dataclasses.replace(privacy_ctx, roles=roles)
    # 5 people (= k) is not a small group; 4 is.
    ok = planted(tmp_path, body="Le régleur reçoit 2,00 $ de prime.")
    check_corpus_privacy([parse_document(ok)], ctx)
    bad = planted(tmp_path, body="Le monteur reçoit 2,00 $ de prime.")
    assert "privacy.small_group_pay (r4)" in errors_of(bad, ctx)


# --------------------------------------------------------------------------- NFD text
def test_rule_2_reads_decomposed_accents(privacy_ctx: PrivacyContext, tmp_path: Path) -> None:
    # A macOS paste decomposes « é » into « e » + U+0301; the name must still be caught.
    acute = chr(0x0301)
    name = "Julie " + "".join(["Be", acute, "rube", acute])
    path = planted(tmp_path, body=f"Selon {name}, la prime change.")
    assert "privacy.pi (nom)" in errors_of(path, privacy_ctx)
    titled = planted(tmp_path, front={"title": f'"Primes de {name}"'})
    assert "RH-099.md:3: privacy.pi (nom)" in errors_of(titled, privacy_ctx)


# --------------------------------------------------------------------------- rule 4
def test_rule_4_disclaimer(privacy_ctx: PrivacyContext, tmp_path: Path) -> None:
    path = planted(tmp_path)
    raw = path.read_text(encoding="utf-8").replace("Document fictif", "Document")
    path.write_text(raw, encoding="utf-8")
    assert "privacy.disclaimer (body)" in errors_of(path, privacy_ctx)


def test_every_violation_is_reported_at_once(privacy_ctx: PrivacyContext, tmp_path: Path) -> None:
    path = planted(
        tmp_path,
        front={"department": "qualite"},
        body="Le cariste touche 2,00 $ ; appelez le 819 555-0177.",
    )
    message = errors_of(path, privacy_ctx)
    for rule in ("front_matter (department)", "pi (telephone)", "small_group_pay (cariste)"):
        assert f"privacy.{rule}" in message


def test_check_hr_corpus_fails_closed_on_a_bad_document(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    for source in sorted(HR_CORPUS.glob("*.md")):
        (corpus / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    planted(corpus, body="Appelez le 819 555-0177.")
    with pytest.raises(CorpusError, match=r"RH-099\.md:\d+: privacy\.pi \(telephone\)"):
        check_hr_corpus(hr_repo_settings(hr_corpus_dir=corpus))


def test_documents_without_a_file_report_line_one(privacy_ctx: PrivacyContext) -> None:
    doc = parse_document(TEMPLATE).model_copy(
        update={"path": Path("absent.md"), "body": "Appelez le 819 555-0177."}
    )
    with pytest.raises(CorpusError, match=r"absent\.md:1: privacy\.pi \(telephone\)"):
        check_corpus_privacy([doc], privacy_ctx)
    assert isinstance(doc, Document)


# --------------------------------------------------------------------------- roles
def test_org_roles_match_the_company_profile(privacy_ctx: PrivacyContext) -> None:
    roles = privacy_ctx.roles
    assert roles.total == 118
    assert roles.get("soudeurs").headcount("evening") == 11
    assert roles.get("cariste").headcount() == 1
    assert roles.get("chefs_equipe").headcount("night") == 0
    # Every role below k = 5 people, exactly: a looser rule 3 would drop some of them.
    assert {r.id for r in roles.small_roles(5)} == {
        "adjointes_administratives",
        "agents_approvisionnement",
        "cariste",
        "charges_de_projet",
        "commis_expedition",
        "comptables",
        "conseillere_principale_rh",
        "dessinateurs_cao",
        "directeur_finances_administration",
        "directeur_ingenierie",
        "directeur_production",
        "directeur_qualite",
        "directrice_rh",
        "electricien_entretien",
        "estimateurs",
        "inspecteur_soudage_certifie",
        "inspecteurs",
        "mecaniciens_entretien",
        "presidente",
        "programmeurs_cnc_laser",
        "superviseurs_production",
        "technicienne_paie",
        "techniciens_qualite",
    }
    assert "soudeurs" not in {r.id for r in roles.small_roles(5)}
    with pytest.raises(KeyError):
        roles.get("pilote")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("le chef d'equipe de soir", [("chefs_equipe", "evening", 2)]),
        ("les soudeurs du quart de jour", [("soudeurs", "day", 15)]),
        ("la soudeuse de nuit", [("soudeurs", "night", 0)]),
        ("l'inspecteur en soudage", [("inspecteur_soudage_certifie", None, 1)]),
        ("un inspecteur", [("inspecteurs", None, 3)]),
        (
            "le superviseur soudage et le cariste",
            [("superviseurs_production", None, 3), ("cariste", None, 1)],
        ),
        ("une piece soudee", []),
    ],
)
def test_role_mentions_with_shifts(
    text: str, expected: list[tuple[str, str | None, int]], privacy_ctx: PrivacyContext
) -> None:
    got = [(m.role.id, m.shift, m.headcount) for m in privacy_ctx.roles.mentions(text)]
    assert got == expected


def test_is_role_uses_normalised_aliases(privacy_ctx: PrivacyContext) -> None:
    assert privacy_ctx.roles.is_role("Directrice des Ressources Humaines")
    assert not privacy_ctx.roles.is_role("pilote d'avion")


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("departments: [", "invalid YAML"),
        ("departments: []", "too_short"),
        (
            "departments:\n  - name: A\n    total: 3\n    roles:\n"
            "      - {id: a, aliases: [x], day: 1, evening: 1}\n",
            "department total",
        ),
        (
            "departments:\n  - name: A\n    total: 2\n    roles:\n"
            "      - {id: a, aliases: [x], day: 1, evening: 0}\n"
            "      - {id: b, aliases: [X], day: 1, evening: 0}\n",
            "alias names two roles",
        ),
        (
            "departments:\n  - name: A\n    total: 2\n    roles:\n"
            "      - {id: a, aliases: [x], day: 1, evening: 0}\n"
            "      - {id: a, aliases: [y], day: 1, evening: 0}\n",
            "duplicate role id",
        ),
    ],
)
def test_org_roles_loader_errors(tmp_path: Path, content: str, message: str) -> None:
    path = tmp_path / "roles.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(PrivacyConfigError, match=message):
        OrgRoles.load(path)


def test_org_roles_missing_file(tmp_path: Path) -> None:
    with pytest.raises(PrivacyConfigError, match="not readable"):
        OrgRoles.load(tmp_path / "absent.yaml")


@pytest.mark.parametrize("alias", ["''", "' '", "de", "La"])
def test_org_roles_reject_empty_and_common_aliases(tmp_path: Path, alias: str) -> None:
    # Such an alias would match almost anywhere and fire small-group rules silently.
    path = tmp_path / "roles.yaml"
    path.write_text(
        "departments:\n  - name: A\n    total: 1\n    roles:\n"
        f"      - {{id: a, aliases: [{alias}], day: 1, evening: 0}}\n",
        encoding="utf-8",
    )
    with pytest.raises(PrivacyConfigError, match=r"too_short|empty or a common word"):
        OrgRoles.load(path)


def test_role_headcounts() -> None:
    role = Role(id="x", department="d", aliases=("x",), day=3, evening=1)
    assert (role.total, role.headcount("day"), role.headcount("evening")) == (4, 3, 1)


# --------------------------------------------------------------------------- contacts
def test_contacts_are_role_mailboxes(privacy_ctx: PrivacyContext) -> None:
    contacts = load_contacts(HR_DATA / "contacts.yaml")
    assert contacts.mailboxes["confidentialite"] == "confidentialite@alverno.example"
    assert privacy_ctx.role_mailboxes == frozenset(contacts.mailboxes.values())


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("mailboxes: [", "invalid YAML"),
        ("mailboxes: {}\nrprp_role: x\nharassment_designated_role: y\n", "every role mailbox"),
        ("mailboxes: {rh: rh@alverno.example}\n", "invalid contacts"),
    ],
)
def test_contacts_loader_errors(tmp_path: Path, content: str, message: str) -> None:
    path = tmp_path / "contacts.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(PrivacyConfigError, match=message):
        load_contacts(path)


def test_contacts_must_stay_at_the_fictional_domain(tmp_path: Path) -> None:
    raw = (HR_DATA / "contacts.yaml").read_text(encoding="utf-8")
    path = tmp_path / "contacts.yaml"
    path.write_text(raw.replace("rh@alverno.example", "rh@ailleurs.test"), encoding="utf-8")
    with pytest.raises(PrivacyConfigError, match=r"alverno\.example"):
        load_contacts(path)
    with pytest.raises(PrivacyConfigError, match="not readable"):
        load_contacts(tmp_path / "absent.yaml")


def test_governance_roles_must_be_org_roles(tmp_path: Path) -> None:
    raw = (HR_DATA / "contacts.yaml").read_text(encoding="utf-8")
    path = tmp_path / "contacts.yaml"
    path.write_text(raw.replace('rprp_role: "directeur', 'rprp_role: "pilote'), encoding="utf-8")
    with pytest.raises(PrivacyConfigError, match="unknown governance role"):
        load_privacy_context(hr_repo_settings(hr_contacts_path=path))


def test_privacy_context_reads_every_lexicon_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    # A missing given-name lexicon fails when the context is built, not mid-request.
    from alverno_lab.privacy import lexicon

    real = lexicon._read_lines

    def without_given_names(name: str) -> list[str]:
        if name == "prenoms.txt":
            raise PrivacyConfigError("privacy lexicon not readable: data/prenoms.txt")
        return real(name)

    monkeypatch.setattr(lexicon, "_read_lines", without_given_names)
    lexicon.given_names.cache_clear()
    try:
        with pytest.raises(PrivacyConfigError, match="prenoms"):
            load_privacy_context(hr_repo_settings())
    finally:
        lexicon.given_names.cache_clear()


@pytest.mark.parametrize(
    ("sentence", "has_amount"),
    [
        ("Le salaire est de 52 000 $ par année.", True),
        ("Une prime de 1,234.56$ est versée.", True),
        ("Une prime de 2 $ de l'heure.", True),
        ("Appelez le 819 555 0142 pour un rendez-vous.", False),
        ("Le quart compte 12 345 678 pièces.", False),
    ],
)
def test_amount_pattern_still_finds_amounts(sentence: str, has_amount: bool) -> None:
    assert bool(_AMOUNT_RE.search(sentence)) is has_amount


def _best_search_time(text: str) -> float:
    best = float("inf")
    for _ in range(3):
        start = time.perf_counter()
        _AMOUNT_RE.search(text)
        best = min(best, time.perf_counter() - start)
    return best


@pytest.mark.parametrize("unit", ["1 ", "12 345 ", "1234567", "1,"])
def test_amount_pattern_is_linear_on_long_digit_runs(unit: str) -> None:
    # Regression (freeze review, redos-1): a long run of digit groups without « $ » made
    # the search quadratic, once per corpus sentence, on every HR load (2.3 s at 20 000
    # characters). Linear growth is about 10x from 20 000 to 200 000 characters,
    # quadratic about 100x; both bounds are generous.
    small = _best_search_time(unit * (20_000 // len(unit)))
    large = _best_search_time(unit * (200_000 // len(unit)))
    assert large < 0.5
    assert large < 40 * max(small, 1e-4)
