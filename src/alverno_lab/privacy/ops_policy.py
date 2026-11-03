"""Refusal messages of module 3 (EFVP « Politique de refus », spec I4 decision 6).

:class:`OpsRefusalPolicy` serves one fixed Québec French text per ``OpsRefusalCode`` for
each sub-module: ``service`` (3a, customer service) and ``maint`` (3b, maintenance). Each
table covers all 24 codes; an unknown code is an error, never a fallback text.

Every message is one or two sentences, names a role mailbox and never a person, never
repeats the request or a value, never confirms that a person exists and holds no
placeholder. The mailboxes are those of the EFVP: ``service-client@`` for 3a,
``entretien@`` for 3b (fixed here, at step I4), ``soutien-ti@`` for a failure of a
connector or of the access log, and ``confidentialite@`` (the RPRP) next to the
privacy refusals. ``sensitive_out_of_scope`` points to the occupational health and
safety process of the company, with no advice of its own.

:func:`auto_send_notice` is the notice shown with every draft header: a request to send
directly is never a refusal, but the draft is still held for approval (EFVP).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from alverno_lab.privacy.ops_types import OpsRefusalCode

OpsModule = Literal["service", "maint"]

SERVICE_MAILBOX = "service-client@alverno.example"
MAINT_MAILBOX = "entretien@alverno.example"
IT_MAILBOX = "soutien-ti@alverno.example"
PRIVACY_MAILBOX = "confidentialite@alverno.example"

_AUTO_SEND_NOTICE = (
    "Le brouillon doit être approuvé avant tout envoi ; l'envoi se fait depuis votre propre boîte."
)


def _shared(box: str) -> dict[OpsRefusalCode, str]:
    """Messages whose text is the same in both sub-modules, but for the mailbox."""
    return {
        "unsupported_language": (
            "Cet assistant traite les demandes en français seulement. Reformulez votre "
            f"demande en français ou, au besoin, écrivez à {box}."
        ),
        "multi_order": (
            "Votre demande vise plus d'une commande ; une demande porte sur une seule "
            f"commande à la fois. Reformulez-la pour chaque commande ou écrivez à {box}."
        ),
        "restricted_info": (
            "Les prix, coûts, marges, soumissions et dessins sont des renseignements "
            "restreints que cet assistant ne communique pas. Adressez-vous à votre "
            f"gestionnaire ou écrivez à {box}."
        ),
        "claim_out_of_scope": (
            "Les garanties, réclamations, notes de crédit et retenues de crédit ne sont pas "
            f"traitées par cet assistant. Pour ouvrir un dossier, écrivez à {box}."
        ),
        "minimization_refused": (
            "Ce renseignement (téléphone, adresse personnelle ou note interne) est exclu des "
            f"vues de l'assistant et n'est pas communiqué. Écrivez à {box} ; pour vos droits "
            f"sur vos renseignements personnels, écrivez à {PRIVACY_MAILBOX}."
        ),
        "sensitive_out_of_scope": (
            "Les blessures, les accidents et les questions de santé et de sécurité ne sont pas "
            "traités par cet assistant : suivez le processus de santé et de sécurité du "
            f"travail d'Alverno. Au besoin, écrivez à {box}."
        ),
        "output_pi": (
            "Le texte généré pouvait contenir un renseignement personnel ; il a été retenu. "
            f"Reformulez votre demande ou écrivez à {box}."
        ),
        "invalid_decision": (
            "La réponse d'approbation n'est pas valide : elle compte comme un rejet et rien "
            f"n'a été enregistré. Recommencez la demande ou écrivez à {box}."
        ),
        "audit_unavailable": (
            "Le journal des accès est indisponible ; par prudence, rien n'est affiché, approuvé "
            f"ni exécuté. Signalez-le au soutien informatique ({IT_MAILBOX})."
        ),
    }


_S = SERVICE_MAILBOX
_SERVICE: dict[OpsRefusalCode, str] = {
    **_shared(_S),
    "empty": (
        "Votre demande est vide. Indiquez un numéro de commande au format CMD-AAAA-NNNN et ce "
        f"que vous voulez savoir ou répondre au client ; au besoin, écrivez à {_S}."
    ),
    "prompt_injection": (
        "Je traite seulement le suivi des commandes et les brouillons de réponse aux clients, "
        f"et je ne peux ni modifier mes règles ni contourner l'approbation. Écrivez à {_S}."
    ),
    "order_not_identified": (
        "Je ne trouve aucun numéro de commande complet dans votre demande : indiquez-le au "
        f"format CMD-AAAA-NNNN, préfixe compris. Au besoin, écrivez à {_S}."
    ),
    "asset_not_identified": (
        "Cet assistant traite les commandes, pas les équipements : indiquez un numéro de "
        f"commande au format CMD-AAAA-NNNN. Au besoin, écrivez à {_S}."
    ),
    "record_not_found": (
        f"Je ne trouve pas cette commande dans le système. Vérifiez le numéro ou écrivez à {_S}."
    ),
    "insufficient_history": (
        "Le dossier de cette commande ne contient pas assez d'information pour répondre. "
        f"Écrivez à {_S}."
    ),
    "cross_account": (
        "Cette demande touche un autre compte client que celui de la commande ; je ne peux pas "
        f"y donner suite. Faites une demande distincte ou écrivez à {_S}."
    ),
    "recipient_override": (
        "La réponse va toujours au contact de la commande : je ne peux ni changer de "
        f"destinataire ni ajouter de copie. Pour changer de contact, écrivez à {_S}."
    ),
    "purpose_drift": (
        "Cet assistant sert seulement au suivi des commandes et aux réponses aux clients, pas "
        f"à la vente ni à la prospection. Pour toute autre demande, écrivez à {_S}."
    ),
    "person_targeted": (
        "Je ne réponds à aucune demande qui vise une personne ou qui permettrait de "
        f"l'identifier. Reformulez votre demande sur la commande seulement, ou écrivez à {_S}."
    ),
    "profiling": (
        "Je ne produis aucune mesure de durée, de rendement ou de classement d'une personne ou "
        f"du service. Écrivez à {_S} ou, pour vos droits, à {PRIVACY_MAILBOX}."
    ),
    "ungrounded": (
        "Le brouillon contenait une information absente du dossier de la commande ; il a été "
        f"retenu. Reformulez votre demande ou écrivez à {_S}."
    ),
    "invalid_args": (
        "Les éléments modifiés de l'action ne sont pas valides ; l'action a été retenue et rien "
        f"n'a été enregistré. Au besoin, écrivez à {_S}."
    ),
    "approval_rejected": (
        "Vous avez rejeté le brouillon : rien n'a été enregistré ni envoyé. Recommencez la "
        f"demande au besoin, ou écrivez à {_S}."
    ),
    "connector_unavailable": (
        "Le système des commandes ne répond pas ; aucune réponse n'est donnée. Signalez-le au "
        f"soutien informatique ({IT_MAILBOX})."
    ),
}

_M = MAINT_MAILBOX
_MAINT: dict[OpsRefusalCode, str] = {
    **_shared(_M),
    "empty": (
        "Votre demande est vide. Indiquez un équipement au format EQ-XXX-NN et le symptôme "
        f"observé ; au besoin, écrivez à {_M}."
    ),
    "prompt_injection": (
        "Je traite seulement le diagnostic des équipements et les demandes de travail, et je "
        f"ne peux ni modifier mes règles ni contourner l'approbation. Écrivez à {_M}."
    ),
    "order_not_identified": (
        "Cet assistant traite les équipements, pas les commandes : indiquez un équipement au "
        f"format EQ-XXX-NN. Au besoin, écrivez à {_M}."
    ),
    "asset_not_identified": (
        "Je ne trouve aucun numéro d'équipement complet dans votre demande : indiquez-le au "
        f"format EQ-XXX-NN, préfixe compris. Au besoin, écrivez à {_M}."
    ),
    "record_not_found": (
        f"Je ne trouve pas cet équipement dans le système. Vérifiez le numéro ou écrivez à {_M}."
    ),
    "insufficient_history": (
        "L'historique de cet équipement compte moins de deux bons de travail : je ne peux pas "
        f"proposer de diagnostic. Pour une inspection, écrivez à {_M}."
    ),
    "cross_account": (
        "Cette demande déborde de l'équipement visé ; je ne peux pas y donner suite. Faites "
        f"une demande distincte ou écrivez à {_M}."
    ),
    "recipient_override": (
        "Cet assistant n'adresse aucun message et ne choisit aucun destinataire ; il prépare "
        f"seulement des demandes de travail. Au besoin, écrivez à {_M}."
    ),
    "purpose_drift": (
        "Cet assistant sert seulement au diagnostic des équipements et aux demandes de "
        f"travail. Pour toute autre demande, écrivez à {_M}."
    ),
    "person_targeted": (
        "Je ne réponds à aucune demande qui vise une personne, par exemple qui a fait une "
        f"intervention ou à qui revient une faute. Reformulez-la sur l'équipement ou écrivez à "
        f"{_M}."
    ),
    "profiling": (
        "Je ne produis aucune durée, moyenne, ni aucun classement ou rendement d'une personne, "
        f"d'un quart ou du service. Écrivez à {_M} ou, pour vos droits, à {PRIVACY_MAILBOX}."
    ),
    "ungrounded": (
        "La proposition contenait une information absente de l'historique de l'équipement ; "
        f"elle a été retenue. Reformulez votre demande ou écrivez à {_M}."
    ),
    "invalid_args": (
        "Les codes modifiés sortent du catalogue ou ne sont pas valides ; la demande de travail "
        f"a été retenue et rien n'a été créé. Au besoin, écrivez à {_M}."
    ),
    "approval_rejected": (
        "Vous avez rejeté la demande de travail : rien n'a été créé. Recommencez la demande au "
        f"besoin, ou écrivez à {_M}."
    ),
    "connector_unavailable": (
        "Le système d'entretien ne répond pas ; aucune réponse n'est donnée. Signalez-le au "
        f"soutien informatique ({IT_MAILBOX})."
    ),
}

_MESSAGES: Mapping[OpsModule, Mapping[OpsRefusalCode, str]] = MappingProxyType(
    {"service": MappingProxyType(dict(_SERVICE)), "maint": MappingProxyType(dict(_MAINT))}
)


class OpsPolicyError(ValueError):
    """An unknown refusal code or sub-module; the message names the rule only."""


@dataclass(frozen=True)
class OpsRefusalPolicy:
    """Refusal texts of one module 3 sub-module (``service`` for 3a, ``maint`` for 3b)."""

    module: OpsModule

    def __post_init__(self) -> None:
        if self.module not in _MESSAGES:
            raise OpsPolicyError("ops_policy:unknown_module") from None

    @property
    def messages(self) -> Mapping[OpsRefusalCode, str]:
        """Every code of this sub-module and its text (read-only)."""
        return _MESSAGES[self.module]

    @property
    def mailbox(self) -> str:
        """The role mailbox of this sub-module."""
        return SERVICE_MAILBOX if self.module == "service" else MAINT_MAILBOX

    def message(self, code: OpsRefusalCode) -> str:
        """The served text for ``code``; an unknown code is an :class:`OpsPolicyError`."""
        text = self.messages.get(code)
        if text is None:
            raise OpsPolicyError("ops_policy:unknown_code") from None
        return text


def auto_send_notice() -> str:
    """The notice shown with the draft header: approval first, sending by hand."""
    return _AUTO_SEND_NOTICE
