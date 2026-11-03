"""The art. 8 notice of ``alverno service ask`` (EFVP of module 3, section 4).

It is printed before the request is read. It says what the tool does (S1 and S2), that it
sends nothing, that the request and the draft are not kept and that a pending draft is
lost when the command ends, what the access log notes (the ADR 0014 fields, never a
text) and why, how long it is kept and who reads it, the rights of access, rectification
and withdrawal of consent, and the reminder to close or clear the terminal.
"""

from __future__ import annotations

__all__ = ["SERVICE_NOTICE", "service_notice"]


def service_notice(retention_days: int = 90) -> str:
    """The notice, with the effective retention of the access log."""
    return (
        "Service à la clientèle d'Alverno (fictif) : cet outil répond sur l'état d'une "
        "commande ou prépare un brouillon de réponse au contact de cette commande, à "
        "partir de l'ERP simulé. Il n'envoie rien : vous relisez le brouillon, vous "
        "l'approuvez, le modifiez ou le rejetez, et vous l'envoyez vous-même. Votre demande "
        "et le brouillon ne sont pas conservés ; un brouillon en attente est perdu si la "
        "commande se termine. Le journal des accès sert à rendre des comptes, à évaluer un "
        "incident de confidentialité et à vérifier le bon fonctionnement de l'outil. Il "
        "note, sans aucun texte : la version du format, le module (service à la clientèle "
        "ou entretien), un numéro tiré au hasard pour chaque ligne et un autre qui relie "
        "les lignes d'un même brouillon, l'heure (arrondie à l'heure, UTC), le canal, le "
        "flux, l'événement et le motif d'un refus ou d'une retenue, le nom des outils "
        "appelés, les numéros de commande lus, le compte en portée, les types et le "
        "nombre de renseignements personnels retirés de la demande (jamais leur valeur), "
        "les codes de la quarantaine, l'action préparée et l'empreinte de ses arguments, "
        "l'indication qu'aucun envoi n'a eu lieu, le tour de modification, votre "
        "décision, le modèle utilisé et la durée ; jamais votre nom. Il est conservé "
        f"{retention_days} jours, puis supprimé, et il est lu "
        "seulement par le responsable de la protection des renseignements personnels "
        "(RPRP) et par le soutien informatique qui agit sous son autorité. Vous pouvez "
        "consulter ou faire rectifier vos renseignements personnels, et retirer votre "
        "consentement à l'utilisation des renseignements recueillis, auprès du RPRP ; "
        "cesser d'utiliser l'outil arrête toute nouvelle collecte, sans valoir retrait. "
        "Fermez ou effacez le terminal après usage : le texte y reste affiché. Pour toute "
        "question : confidentialite@alverno.example."
    )


# The notice with the default retention (90 days, the maximum).
SERVICE_NOTICE = service_notice()
