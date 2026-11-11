"""The art. 8 notice of ``alverno direction ask`` (EFVP of module 5, section 4).

It is printed before the question is read. It says what the tool does (D1 to D5), what it
refuses, that the question and the answer are not kept, what the ``direction`` access log
notes (codes only, never a text) and why, how long it is kept and who reads it, and the
rights of access and rectification.
"""

from __future__ import annotations

__all__ = ["direction_notice"]


def direction_notice(retention_days: int = 90) -> str:
    """The notice, with the effective retention of the access log."""
    return (
        "Outil de la direction d'Alverno (fictif) : il répond par sections, une par "
        "domaine (Qualité, ressources humaines, commandes, entretien), chacune produite "
        "par l'outil de son domaine et avec ses sources. Les synthèses des commandes et de "
        "l'entretien sont calculées à partir de l'ERP et de la GMAO simulés : aucun "
        "contact, aucun prix, aucun technicien et aucune durée de réparation. L'outil ne "
        "relie aucun domaine au sujet d'une personne et ne classe ni personne ni petit "
        "groupe. Votre question et la réponse ne sont pas conservées. Le journal des accès "
        "sert à rendre des comptes, à évaluer un incident de confidentialité et à vérifier "
        "le bon fonctionnement de l'outil. Il note, sans aucun texte : la version du "
        "format, un numéro tiré au hasard, l'heure (arrondie à l'heure, UTC), le canal, la "
        "décision et le motif d'un refus, les domaines consultés et le résultat de chaque "
        "section, le modèle utilisé et la durée ; jamais votre nom, aucun numéro de "
        f"commande ni de compte. Il est conservé {retention_days} jours, puis supprimé, "
        "et il est lu seulement par le responsable de la protection des renseignements "
        "personnels (RPRP) et par le soutien informatique qui agit sous son autorité. Pour "
        "l'accès ou la rectification : confidentialite@alverno.example."
    )
