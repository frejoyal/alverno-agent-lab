"""The art. 8 notice of ``alverno maint ask`` (EFVP of module 3, section 4).

It is printed before the request is read. It says what the tool does (M1 and M2), that
it names and evaluates no one, that it writes nothing without approval and sends
nothing, that the request and the diagnosis are not kept and that a pending work request
is lost when the command ends, what the access log notes (the ADR 0014 fields, never a
text) and why, how long it is kept and who reads it, the rights of access, rectification
and withdrawal of consent, and the reminder to close or clear the terminal.
"""

from __future__ import annotations

__all__ = ["MAINT_NOTICE", "maint_notice"]


def maint_notice(retention_days: int = 90) -> str:
    """The notice, with the effective retention of the access log."""
    return (
        "Entretien d'Alverno (fictif) : cet outil aide à diagnostiquer l'arrêt d'une "
        "machine à partir de l'historique de cette machine dans la GMAO simulée, ou "
        "prépare une demande de travail corrective faite de codes et de numéros. Il parle "
        "de la machine seulement : il ne nomme et n'évalue personne. Il n'écrit rien sans "
        "votre approbation et n'envoie rien : vous approuvez, modifiez les codes ou "
        "rejetez la demande de travail. Votre demande et le diagnostic ne sont pas "
        "conservés ; une demande de travail en attente est perdue si la commande se "
        "termine. Le journal des accès sert à rendre des comptes, à évaluer un incident "
        "de confidentialité et à vérifier le bon fonctionnement de l'outil. Il note, sans "
        "aucun texte : la version du format, le module (service à la clientèle ou "
        "entretien), un numéro tiré au hasard pour chaque ligne et un autre qui relie "
        "les lignes d'une même demande de travail, l'heure (arrondie à l'heure, UTC), le "
        "canal, le flux, l'événement et le motif d'un refus ou d'une retenue, le nom des "
        "outils appelés, les numéros d'équipement et de bons de travail lus (le champ du "
        "compte client reste vide en entretien), les types et "
        "le nombre de renseignements personnels retirés de la demande (jamais leur "
        "valeur), les codes de la quarantaine, l'action préparée et l'empreinte de ses "
        "arguments, l'indication qu'aucun envoi n'a eu lieu, le tour de modification, "
        "votre décision, le modèle utilisé et la durée ; jamais votre nom. Il est "
        f"conservé {retention_days} jours, puis supprimé, et il est lu "
        "seulement par le responsable de la protection des renseignements personnels "
        "(RPRP) et par le soutien informatique qui agit sous son autorité. Vous pouvez "
        "consulter ou faire rectifier vos renseignements personnels, et retirer votre "
        "consentement à l'utilisation des renseignements recueillis, auprès du RPRP ; "
        "cesser d'utiliser l'outil arrête toute nouvelle collecte, sans valoir retrait. "
        "Fermez ou effacez le terminal après usage : le texte y reste affiché. Pour toute "
        "question : confidentialite@alverno.example."
    )


# The notice with the default retention (90 days, the maximum).
MAINT_NOTICE = maint_notice()
