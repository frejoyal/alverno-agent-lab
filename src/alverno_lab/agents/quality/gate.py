"""Gate of the Quality agent: question screening and the no-match barrier."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel

from alverno_lab.retrieval import Hit, HybridRetriever

RefusalReason = Literal[
    "empty_question",
    "prompt_injection",
    "personal_information",
    "no_match",
    "llm_error",
    "no_valid_citation",
    "low_confidence",
]

# Instruction-override attempts (French and English). Questions are data, never orders.
_INJECTION_PATTERNS = [
    r"\b(ignore[rz]?|oublie[rz]?|néglige[rz]?)\b.{0,40}\b(instructions?|consignes?|règles?"
    r"|directives?|prompt)\b",
    r"\bne tiens? (plus )?pas compte\b.{0,40}\b(instructions?|consignes?|règles?)\b",
    r"\bignore (all|any|the|your|previous|prior)\b.{0,30}\binstructions?\b",
    r"\b(prompt|message) syst[eè]me\b",
    r"\bsystem prompt\b",
    r"\b(tu es|vous êtes) maintenant\b",
    r"\byou are now\b",
    # "agis" alone is a normal verb ("comment agis-tu si une pièce est hors tolérance?").
    r"\b(agis|agissez)\s+(comme|en tant que|en qualité de)\b",
    r"\b(fais comme si|faites comme si|act as|pretend)\b",
    r"\b(mode (développeur|administrateur|admin|debug)|developer mode|admin mode|jailbreak)\b",
    r"\binvente[rz]?\b.{0,40}\b(tolérances?|valeurs?|réponses?|normes?|chiffres?|données)\b",
    r"\b(révèle|dévoile|affiche|montre)[rz]?\b.{0,30}\b(prompt|consignes?|instructions?)\b",
    r"\b(mot de passe|password|jeton d'accès|clé d'api|api key)\b",
]

# People (roles) that a pay or employee-number question can target. Instruments and
# labour categories ("matricule de l'instrument", "taux horaire du soudage") are not people.
_PERSON = (
    r"(soudeu(r|se)|employée?|inspect(eur|rice)|technicien(ne)?|opérat(eur|rice)"
    r"|superviseure?|travailleu(r|se)|salariée?|personne|collègue|machiniste|peintre"
    r"|contremaîtresse|contremaître|direct(eur|rice)|gestionnaire|stagiaire|apprentie?)s?"
)

# Requests about an identified individual's personal information (Law 25).
_PERSONAL_INFO_PATTERNS = [
    rf"\b(salaire|rémunération|taux horaire)\b.{{0,40}}\b{_PERSON}\b",
    r"\b(son|sa|leur|ses) (salaire|rémunération|taux horaire)\b",
    r"\badresse (personnelle|du domicile|de résidence)\b",
    r"\bnuméro d'assurance sociale\b",
    r"\bdate de naissance\b",
    r"\bdossier (médical|disciplinaire|personnel|d'employé)\b",
    r"\b(état de santé|diagnostic médical|congé de maladie|arrêt de travail)\b",
    r"\b(évaluation de rendement|évaluation annuelle) (de|du|d')\b",
    r"\b(téléphone|cellulaire|courriel) (personnel|perso)\b",
    rf"\bmatricules? (du|de la|de l'|d'un|d'une|des) ?{_PERSON}\b",
    r"\b(son|sa|leur|ses) (nom|matricules?)\b",
]

_INJECTION_RE = [re.compile(p, re.IGNORECASE) for p in _INJECTION_PATTERNS]
_PERSONAL_RE = [re.compile(p, re.IGNORECASE) for p in _PERSONAL_INFO_PATTERNS]
# Civility title + capitalised name ("M. Tremblay", "madame Gagnon", "Mme Côté"). Only the
# title ignores case; the name must stay capitalised, and "M." must be upper case so that
# a unit ("5 m. Quelle...") is not read as a title.
_PERSONAL_RE.append(re.compile(r"\b(?:M\.|(?i:monsieur|madame|mme))\s+[A-ZÀ-Ý][a-zà-ÿ]+"))


class GateDecision(BaseModel):
    """Outcome of the gate node."""

    allowed: bool
    reason: RefusalReason | None = None
    relevance: float | None = None


def screen_question(question: str) -> RefusalReason | None:
    """Return a refusal reason for empty, injected or personal-information questions."""
    if not question.strip():
        return "empty_question"
    if any(p.search(question) for p in _INJECTION_RE):
        return "prompt_injection"
    if any(p.search(question) for p in _PERSONAL_RE):
        return "personal_information"
    return None


def gate_decision(question: str, hits: Sequence[Hit], retriever: HybridRetriever) -> GateDecision:
    """Screen the question, then apply the retriever's no-match barrier."""
    reason = screen_question(question)
    relevance = retriever.relevance(hits) if hits else None
    if reason is not None:
        return GateDecision(allowed=False, reason=reason, relevance=relevance)
    if retriever.is_no_match(hits):
        return GateDecision(allowed=False, reason="no_match", relevance=relevance)
    return GateDecision(allowed=True, relevance=relevance)
