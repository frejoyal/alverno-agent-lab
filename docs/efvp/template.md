# Gabarit d'évaluation des facteurs relatifs à la vie privée (EFVP)

> **Avertissement : ceci n'est pas un avis juridique.** Gabarit du laboratoire `alverno-agent-lab`, pour une entreprise entièrement fictive (Fabrications Alverno inc.). Il a été rédigé en tenant compte de la *Loi sur la protection des renseignements personnels dans le secteur privé* (RLRQ, c. P-39.1) et du guide de la Commission d'accès à l'information (CAI), état du droit au 31 octobre 2026. Une entreprise réelle fait valider son EFVP par son responsable de la protection des renseignements personnels (RPRP) et, au besoin, par un conseiller juridique.

**Source de la structure.** Les sections suivent la structure de rapport que recommande la section 5.2 du guide de la CAI, *Réaliser une évaluation des facteurs relatifs à la vie privée : Guide d'accompagnement à la démarche et à sa documentation*, version 3.1, avril 2024 ([PDF du guide](https://www.cai.gouv.qc.ca/uploads/pdfs/CAI_GU_EFVP.pdf)). La CAI offre aussi un *Modèle générique de rapport d'EFVP* (DOCX), facultatif et à adapter, présenté avec le guide ([annonce de la CAI](https://www.cai.gouv.qc.ca/actualites/evaluation-facteurs-relatifs-vie-privee-guide-plus-convivial)). Les intitulés et les consignes ci-dessous sont une adaptation en nos mots : lisez le guide pour la démarche complète, qui va de la question « l'EFVP est-elle requise ? » jusqu'à sa mise à jour.

**Quand utiliser ce gabarit.** Selon la CAI, une entreprise privée doit réaliser une EFVP dans trois situations :

- un projet d'acquisition, de développement ou de refonte d'un système d'information ou de prestation électronique de services qui implique des renseignements personnels (RP) ([art. 3.3](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:3_3)) ;
- une communication de RP à l'extérieur du Québec, y compris le fait de confier hors Québec la collecte, l'utilisation, la communication ou la conservation de RP, par exemple un hébergement infonuagique ([art. 17](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:17)) ;
- une communication de RP sans consentement à des fins d'étude, de recherche ou de statistiques (art. 21).

Dans le labo, [AGENTS.md](../../AGENTS.md) exige en plus une EFVP documentée **avant l'implémentation** de tout module qui traitera des RP. L'ampleur de l'évaluation est proportionnée à la sensibilité, à la finalité, à la quantité, à la répartition et au support des RP (art. 3.3).

**Mode d'emploi.**

- Copiez ce fichier dans `docs/efvp/<module>.md` et gardez **exactement** les intitulés de niveau 2 (`##`), dans le même ordre : `tests/test_hr_docs.py` le vérifie pour l'EFVP du module RH ; ajoutez votre fichier à la liste `DOCS` de ce test.
- Remplacez chaque consigne « À documenter » par le contenu du projet. Ajoutez des sous-sections de niveau 3 au besoin.
- Désignez des rôles, jamais des personnes. N'utilisez que des données fictives et le domaine `alverno.example`.
- Ne recopiez jamais le texte d'une loi ni du guide : paraphrasez et mettez un lien. Tout énoncé juridique doit venir d'une source vérifiée et datée ([sources juridiques RH](../hr-legal-sources.md)).
- Avant les mesures, n'inscrivez aucun résultat chiffré : seulement des bornes, des comptes de conception et des valeurs légales ou de politique. Les résultats mesurés entrent dans une version ultérieure de l'EFVP.
- Évitez les affirmations absolues sur la conformité ou une approbation par un organisme public. Préférez « rédigé en tenant compte de … ».
- Statuts permis dans les tableaux : **en place**, **prévu** (avec le jalon), **sans objet** (avec le motif), **incertain** (avec ce qui reste à vérifier).

## 0. Résumé

À documenter :
- le projet en deux ou trois phrases, et la situation légale qui motive l'EFVP : art. 3.3, puis votre analyse de l'application de l'art. 17 (communication ou traitement hors Québec) et de l'art. 21 (recherche ou statistiques) ;
- les principales affirmations du projet et la preuve qui les soutiendra (renvoi aux annexes) ;
- les risques résiduels principaux, l'avis du RPRP et l'état de l'approbation ;
- les limites de l'évaluation (ce qui n'est pas couvert, ce qui reste à mesurer).

| Élément | Valeur |
|---|---|
| Module ou système | |
| Version de l'EFVP, date, statut | |
| Situation légale (art. 3.3) | |
| Art. 17 applicable ? Motif | |
| Art. 21 applicable ? Motif | |
| Avis du RPRP | |
| Approbation de la haute direction | |

## 1. Description du projet et objet de l'EFVP

À documenter :
- le besoin d'affaires, les utilisateurs visés et les fins fixées **avant** toute collecte (art. 4) ;
- le fonctionnement du système en mots simples : ce qu'il fait, ce qu'il refuse, ce qu'il ne fera jamais ;
- l'objet précis de l'évaluation (ce qui est inclus, ce qui est exclu) et les hypothèses retenues ;
- les décisions de conception qui touchent la vie privée, avec un lien vers les ADR.

## 2. Rôles et responsabilités

À documenter :
- le RPRP (par défaut la personne qui a la plus haute autorité, ou son délégué par écrit, art. 3.1) et la **date à laquelle il a été consulté**, dès le début du projet (art. 3.3) ;
- le commanditaire, la personne qui approuve au nom de la haute direction, et les équipes qui réalisent et exploitent le système ;
- les consultations faites (sécurité, TI, représentants du personnel, conseiller juridique) et leur date ;
- les coordonnées de contact pour l'exercice des droits (boîtes de rôle seulement).

| Rôle | Responsabilité dans le projet | Consulté le |
|---|---|---|
| | | |

## 3. Renseignements personnels et ampleur de l'évaluation

À documenter :
- l'inventaire des RP, y compris ceux qui peuvent entrer **par accident** (texte libre, journaux, historique) ;
- le parcours des RP à chaque étape du cycle de vie : collecte, utilisation, communication, conservation, destruction ;
- la sensibilité selon l'échelle 1 à 5 du labo ([loi25.md](../loi25.md)) et l'ampleur qui en découle ;
- les journaux, traces et rapports produits par le système, qui sont aussi des supports de RP ;
- pour chaque renseignement, les catégories de personnes qui y ont accès dans l'entreprise et les destinataires d'une communication, en précisant si elle sort du Québec (art. 8 et 17).

| Renseignement | Sensibilité (1-5) | Finalité | Quantité | Répartition | Support | Conservation | Accès (catégories de personnes) | Communication (destinataires, hors Québec ?) |
|---|---|---|---|---|---|---|---|---|
| | | | | | | | | |

## 4. Conformité aux obligations

À documenter :
- pour chaque obligation, la mesure prise par le projet et son statut ;
- le motif de chaque « sans objet » et ce qui reste à vérifier pour chaque « incertain » ;
- les principes de protection retenus au-delà de la loi (minimisation, paramètres par défaut protecteurs, refus explicite).

| Obligation (paraphrase) | Article | Mesure | Statut |
|---|---|---|---|
| Responsable de la protection des RP, délégation écrite, titre et coordonnées publiés | 3.1 | | |
| Politiques et pratiques de gouvernance (conservation et destruction, rôles, plaintes) | 3.2 | | |
| EFVP d'un système d'information ; RPRP consulté dès le début | 3.3 | | |
| Remise des RP informatisés dans un format structuré d'usage courant (portabilité) | 3.3 | | |
| Incident de confidentialité : mesures, avis, registre | 3.5 à 3.8 | | |
| Intérêt sérieux et légitime ; fins fixées avant la collecte | 4 | | |
| Collecte limitée aux RP nécessaires | 5 | | |
| Information de la personne lors de la collecte | 8 | | |
| Technologie d'identification, de localisation ou de profilage | 8.1 | | |
| Paramètres de confidentialité les plus protecteurs par défaut (application incertaine) | 9.1 | | |
| Mesures de sécurité raisonnables | 10 | | |
| Utilisation limitée aux fins de la collecte | 12 | | |
| Décision fondée exclusivement sur un traitement automatisé | 12.1 | | |
| Communication ou traitement hors Québec | 17 | | |
| Mandataire ou prestataire de services | 18.3 | | |
| Destruction ou anonymisation une fois les fins accomplies | 23 | | |

## 5. Risques et stratégies d'atténuation

À documenter :
- les risques pour les personnes concernées (pas seulement pour l'entreprise), numérotés R1, R2, … ;
- pour chaque risque, les mesures, la **preuve** qui démontrera qu'elles fonctionnent (métrique de la porte, test, revue) et le risque résiduel ;
- les menaces retenues et celles qui sont hors périmètre (modèle de menace en annexe).

| # | Risque | Mesures | Preuve | Risque résiduel |
|---|---|---|---|---|
| R1 | | | | |

## 6. Plan d'action

À documenter :
- ce qui doit être vrai avant la mise en service, avec un critère vérifiable pour chaque action ;
- le responsable (rôle) et l'échéance de chaque action, et la gestion des risques résiduels acceptés ;
- les **dates de réévaluation** et les **déclencheurs** d'une nouvelle version de l'EFVP (changement de portée, de fournisseur, de technologie ou de loi).

| Action | Responsable (rôle) | Échéance | Critère |
|---|---|---|---|
| | | | |

| Réévaluation | Date ou déclencheur |
|---|---|
| | |

## 7. Avis du responsable, décision et approbation

À documenter :
- l'avis du RPRP (favorable, favorable sous conditions, défavorable), ses motifs et ses conditions ;
- la décision et l'approbation de la haute direction, avec la date, y compris les décisions de gestion que l'EFVP lui soumet ;
- l'historique des versions de l'EFVP.

| Élément | Contenu |
|---|---|
| Avis du RPRP | |
| Décision de la haute direction | |
| Date d'approbation | |

| Version | Date | Changements |
|---|---|---|
| | | |

## Annexes

À documenter :
- le modèle de menace et le tableau des affirmations du projet, avec leur preuve ;
- les politiques pertinentes, par exemple [POL-002](../../data/corpus/POL-002.md) (classification de l'information) ;
- les rapports d'évaluation ou de sécurité (par exemple un test d'intrusion) et, s'il y a lieu, les ententes de communication ;
- les ADR et les sources juridiques utilisées, avec leur date de vérification.

*Document fictif à des fins de démonstration ; ne constitue pas un avis juridique.*
