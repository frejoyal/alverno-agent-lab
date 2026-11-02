"""The result markers of README.md (ADR 0015 « Banc d'essai »).

Each bench rewrites only its own marker family: ``RESULTS`` (``alverno bench``),
``RESULTS-HR`` (``alverno hr bench``) and ``RESULTS-SVC`` (``alverno service bench``).
Every family appears exactly once per language, the opening marker before the closing
one, each on a line of its own, French in the French part and English in the English
part. ``RESULTS-MAINT`` (``alverno maint bench``) is added at increment I9: until then it
must not appear at all. Rewriting one family leaves every other byte of the README
identical. ``tests/test_quality_frozen.py`` keeps its own fixture-README check unchanged.
"""

from __future__ import annotations

import pytest

from alverno_lab.cli import replace_marked_block

from .conftest import REPO_ROOT

README = REPO_ROOT / "README.md"
FAMILIES: tuple[str, ...] = ("RESULTS", "RESULTS-HR", "RESULTS-SVC")
LANGS: tuple[str, ...] = ("FR", "EN")
ENGLISH_HEADING = "## English"


def _text() -> str:
    return README.read_text(encoding="utf-8")


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.split("\n")]


def _markers(family: str, lang: str) -> tuple[str, str]:
    return f"<!-- {family}:{lang} -->", f"<!-- /{family}:{lang} -->"


def _masked(text: str, family: str) -> str:
    """``text`` with the inside of each block of ``family`` replaced by one placeholder."""
    lines = text.split("\n")
    kept: list[str] = []
    inside = False
    for line in lines:
        bare = line.strip()
        if any(bare == _markers(family, lang)[1] for lang in LANGS):
            inside = False
        if not inside:
            kept.append(line)
        if any(bare == _markers(family, lang)[0] for lang in LANGS):
            inside = True
            kept.append("<masked>")
    return "\n".join(kept)


def _rewritten(text: str, family: str) -> str:
    for lang in LANGS:
        text = replace_marked_block(text, f"{family}:{lang}", f"new table {family}:{lang}")
    return text


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("family", FAMILIES)
def test_each_family_appears_once_per_language_in_order(family: str, lang: str) -> None:
    text = _text()
    bare = _lines(text)
    start, end = _markers(family, lang)
    assert bare.count(start) == 1, start
    assert bare.count(end) == 1, end
    assert bare.index(start) < bare.index(end), start
    # A marker counts only alone on its line: no other line may quote it.
    assert text.count(start) == 1 and text.count(end) == 1, start


@pytest.mark.parametrize("family", FAMILIES)
def test_each_language_sits_in_its_part_of_the_readme(family: str) -> None:
    bare = _lines(_text())
    english = bare.index(ENGLISH_HEADING)
    french_end = bare.index(_markers(family, "FR")[1])
    english_start = bare.index(_markers(family, "EN")[0])
    assert french_end < english < english_start, family


def test_the_families_follow_each_other_in_both_languages() -> None:
    bare = _lines(_text())
    for lang in LANGS:
        positions = [bare.index(_markers(family, lang)[0]) for family in FAMILIES]
        assert positions == sorted(positions), lang
        # The service block comes right after the HR one, with no other family between.
        hr_end = bare.index(_markers("RESULTS-HR", lang)[1])
        svc_start = bare.index(_markers("RESULTS-SVC", lang)[0])
        between = bare[hr_end + 1 : svc_start]
        assert not [line for line in between if line.startswith("<!-- ")], lang


def test_the_maintenance_family_is_absent_until_i9() -> None:
    assert "RESULTS-MAINT" not in _text()


@pytest.mark.parametrize("family", FAMILIES)
def test_rewriting_one_family_leaves_every_other_byte_identical(family: str) -> None:
    before = _text()
    after = _rewritten(before, family)
    assert after != before, family
    assert _masked(after, family) == _masked(before, family), family
    for lang in LANGS:
        assert f"new table {family}:{lang}" in after, (family, lang)
    for name in FAMILIES:
        for lang in LANGS:
            start, end = _markers(name, lang)
            assert _lines(after).count(start) == 1 and _lines(after).count(end) == 1, start


def test_a_rewrite_is_stable_when_repeated() -> None:
    once = _rewritten(_text(), "RESULTS-SVC")
    assert _rewritten(once, "RESULTS-SVC") == once
