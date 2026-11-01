"""Refusal messages shared by the modules, and the redaction note (spec §5.9, D11).

A module builds its own :class:`RefusalPolicy` from its scope and its role mailboxes;
:func:`hr_refusal_policy` builds the HR instance whose texts are quoted verbatim in the
spec. A refusal never repeats the question or a detected value, and never confirms that
a person exists: every message is a fixed text.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from alverno_lab.privacy.types import PIType

# French labels of the PI types, in the order of the redaction note (§5.9).
PI_LABELS: dict[PIType, str] = {
    PIType.NAS: "numéro d'assurance sociale",
    PIType.NAM: "numéro d'assurance maladie",
    PIType.COURRIEL: "adresse courriel",
    PIType.TELEPHONE: "numéro de téléphone",
    PIType.MATRICULE: "matricule",
    PIType.CODE_POSTAL: "code postal",
    PIType.ADRESSE: "adresse",
    PIType.DATE_NAISSANCE: "date de naissance",
    PIType.COMPTE_BANCAIRE: "numéro de compte",
    PIType.NOM: "nom",
    PIType.NUMERO: "numéro",
}


@dataclass(frozen=True)
class RefusalPolicy:
    """Refusal texts of one module.

    ``scope`` names what the module answers on (« politiques RH d'Alverno »),
    ``general_scope`` what it can still explain after a privacy refusal, ``examples`` the
    topics suggested after an empty question. ``contact`` and ``privacy_contact`` are
    written after « le » / « au »: « service des ressources humaines (rh@…) ».
    """

    scope: str
    contact: str
    privacy_contact: str
    general_scope: str = "politiques générales"
    examples: str = ""

    def message(self, code: str | None) -> str:
        """The served text for a refusal code; unknown codes get the not-found text."""
        examples = f", par exemple {self.examples}" if self.examples else ""
        messages = {
            "empty_question": (
                f"Votre question est vide. Posez une question sur les {self.scope}{examples}."
            ),
            "prompt_injection": (
                f"Je réponds seulement aux questions sur les {self.scope} et je ne peux pas "
                "modifier mes règles."
            ),
            "unsupported_language": (
                "Cet assistant traite les questions en français seulement. "
                "This assistant only handles questions in French."
            ),
            "person_targeted": (
                "Je ne réponds à aucune demande qui vise une personne ou un petit groupe de "
                "personnes, ou qui permettrait de les identifier (dossier, paie, absences, "
                f"santé, discipline, coordonnées). Je peux toutefois expliquer les "
                f"{self.general_scope} : reformulez votre question sans viser personne. "
                f"Pour une demande qui concerne un dossier, écrivez au {self.contact}."
            ),
            "own_record": (
                "Je n'ai accès à aucun dossier individuel, y compris le vôtre (solde de "
                "vacances, paie, absences, évaluations). Pour votre dossier, écrivez au "
                f"{self.contact} ; pour consulter ou faire rectifier vos renseignements "
                f"personnels, écrivez au {self.privacy_contact}."
            ),
            "restricted_info": (
                f"Cette information est restreinte et ne figure pas dans les "
                f"{self.general_scope}. Adressez-vous à votre gestionnaire ou au "
                f"{self.contact}."
            ),
            "legal_advice": (
                "Je résume les politiques internes d'Alverno, mais je ne donne pas d'avis "
                "juridique. Pour connaître vos droits, consultez la CNESST ou un conseiller "
                "juridique."
            ),
            "output_pi": (
                "La réponse générée pouvait contenir un renseignement personnel ; elle a été "
                "retenue. Reformulez votre question de façon générale ou écrivez au "
                f"{self.contact}."
            ),
            "audit_unavailable": (
                "Le journal des accès est indisponible ; par prudence, aucune réponse n'est "
                "donnée. Signalez-le au soutien informatique."
            ),
        }
        not_found = (
            f"Je ne trouve pas cette information dans les {self.scope}. Je vous suggère de "
            f"contacter le {self.contact}."
        )
        return messages.get(code or "", not_found)


def hr_refusal_policy(
    *,
    hr_mailbox: str = "rh@alverno.example",
    privacy_mailbox: str = "confidentialite@alverno.example",
) -> RefusalPolicy:
    """The HR instance (§5.9); the mailboxes come from ``data/hr/contacts.yaml``."""
    return RefusalPolicy(
        scope="politiques RH d'Alverno",
        contact=f"service des ressources humaines ({hr_mailbox})",
        privacy_contact=(
            f"responsable de la protection des renseignements personnels ({privacy_mailbox})"
        ),
        general_scope="politiques RH générales",
        examples="sur les vacances ou les horaires des quarts",
    )


def redaction_note(types: Sequence[PIType]) -> str:
    """The note served when PI was removed from the question; empty when none was.

    Labels follow the order of :data:`PI_LABELS` and appear once each.
    """
    present = set(types)
    labels = [label for pi_type, label in PI_LABELS.items() if pi_type in present]
    if not labels:
        return ""
    return (
        f"Note : des renseignements personnels ({', '.join(labels)}) ont été retirés de votre "
        "question avant son traitement ; ils ne sont pas conservés."
    )
