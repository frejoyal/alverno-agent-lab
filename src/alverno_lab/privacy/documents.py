"""Privacy check of a module corpus (spec §5.7), run on every HR load; fails closed.

An HR document must satisfy four rules:

1. ``privacy.front_matter``: ``sensitivity: 2``, ``department: rh``, ``reviewed_by`` (a
   role of ``org_roles.yaml``) and ``legal_checked`` (an ISO date);
2. ``privacy.pi``: the document detectors find no personal information in the title
   and the body (allow-listed terms and role mailboxes are not PI);
3. ``privacy.small_group_pay``: no sentence with an amount names a role of fewer than k
   people;
4. ``privacy.disclaimer``: the body says « Document fictif ».

The Quality corpus is checked with rule 2 only (``hr_rules=False``). Errors name the
file, the line, the rule and a type, never the detected value.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from collections.abc import Sequence

from alverno_lab.corpus import CorpusError, Document
from alverno_lab.privacy.detect import detect_pi
from alverno_lab.privacy.lexicon import normalize
from alverno_lab.privacy.types import PrivacyContext

# A match may only start at the beginning of a digit-group run: starting inside one
# re-scans the run to its end, which made a long run without « $ » quadratic. The
# boolean result is unchanged (a match inside a run implies one from its start).
_AMOUNT_RE = re.compile(r"(?<!\d)(?<!\d[ ,.])\d+(?:[ ,.]\d+)*\s?\$")
_SENTENCE_RE = re.compile(r"(?<=[.!?;:])\s+|\n+")
_DISCLAIMER = "Document fictif"


def _offsets(doc: Document) -> tuple[int, int]:
    """1-based file lines of the title and of the first body line (1 when unknown)."""
    try:
        raw = doc.path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    except OSError:
        return 1, 1
    title_at = re.search(r"^title:", raw, re.MULTILINE)
    body_at = raw.find(doc.body) if doc.body else -1
    title_line = raw.count("\n", 0, title_at.start()) + 1 if title_at else 1
    body_line = raw.count("\n", 0, body_at) + 1 if body_at >= 0 else 1
    return title_line, body_line


def _line(body: str, offset: int, first_line: int) -> int:
    return first_line + body.count("\n", 0, offset)


def _front_matter_errors(doc: Document, ctx: PrivacyContext) -> list[str]:
    errors: list[str] = []
    if doc.sensitivity != 2:
        errors.append("sensitivity")
    if doc.department != "rh":
        errors.append("department")
    reviewer = doc.metadata.get("reviewed_by")
    if not isinstance(reviewer, str) or not ctx.roles.is_role(reviewer):
        errors.append("reviewed_by")
    checked = doc.metadata.get("legal_checked")
    if not isinstance(checked, dt.date):
        try:
            dt.date.fromisoformat(str(checked))
        except ValueError:
            errors.append("legal_checked")
    return errors


def _pay_errors(doc: Document, ctx: PrivacyContext, first_line: int) -> list[str]:
    small = {r.id for r in ctx.roles.small_roles(ctx.k)}
    errors: list[str] = []
    start = 0
    for sentence in _SENTENCE_RE.split(doc.body):
        offset = doc.body.find(sentence, start)
        start = max(start, offset)
        if not _AMOUNT_RE.search(sentence):
            continue
        for mention in ctx.roles.mentions(normalize(sentence)):
            if mention.role.id in small:
                line = _line(doc.body, offset, first_line)
                errors.append(f"{doc.path}:{line}: privacy.small_group_pay ({mention.role.id})")
    return errors


def check_corpus_privacy(
    docs: Sequence[Document], ctx: PrivacyContext, *, hr_rules: bool = True
) -> None:
    """Raise :class:`CorpusError` listing every violation; return when the corpus is clean."""
    errors: list[str] = []
    for doc in docs:
        title_line, body_line = _offsets(doc)
        if hr_rules:
            errors.extend(
                f"{doc.path}:1: privacy.front_matter ({key})"
                for key in _front_matter_errors(doc, ctx)
            )
        # NFC first: a decomposed accent (macOS paste) splits a name into two words.
        # Composition never crosses a newline, so line numbers stay right.
        for span in detect_pi(unicodedata.normalize("NFC", doc.title), ctx, mode="document"):
            errors.append(f"{doc.path}:{title_line}: privacy.pi ({span.type.value})")
        body = unicodedata.normalize("NFC", doc.body)
        for span in detect_pi(body, ctx, mode="document"):
            line = _line(body, span.start, body_line)
            errors.append(f"{doc.path}:{line}: privacy.pi ({span.type.value})")
        if hr_rules:
            errors.extend(_pay_errors(doc, ctx, body_line))
            if _DISCLAIMER not in doc.body:
                errors.append(f"{doc.path}:{body_line}: privacy.disclaimer (body)")
    if errors:
        raise CorpusError("corpus privacy check failed: " + "; ".join(errors))
