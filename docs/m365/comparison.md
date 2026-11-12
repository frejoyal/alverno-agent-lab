# Agents locaux du labo ou capacités natives de Microsoft ? (module 6)

Comparaison écrite le 2026-11-11 pour une entreprise fictive, selon trois critères : coût, qualité mesurée, conformité. Les prix sont indicatifs, avant taxes ; vérifier les prix au moment de l'achat.

## Les trois options

| Option | Où se fait l'inférence | Ce qu'il faut |
|---|---|---|
| **A. Labo + Copilot Studio classique** (ce module) | Sur le poste de l'usine (Ollama) | Poste avec carte graphique, passerelle de données locale, agent Copilot Studio sans IA générative |
| **B. Copilot Studio, réponses génératives sur SharePoint** | Service Azure OpenAI ; **aux États-Unis** pour un environnement Power Platform au Canada | Agent Copilot Studio, bibliothèque SharePoint comme source de connaissances |
| **C. Microsoft 365 Copilot** | Services de Microsoft | Licence par utilisateur |

## Coût

Hypothèse d'usage : 60 employés actifs, 3 questions par semaine chacun, environ 780 questions par mois.

| Option | Coût mensuel estimé | Base |
|---|---|---|
| A | environ 4 000 crédits Copilot (5 crédits par question qui appelle une action), soit environ 40 $ US au paiement à l'usage ; électricité du poste ; matériel amorti | Prix publiés par des tiers et vérifiés le 2026-11-03 : 0,01 $ US par crédit à l'usage, 200 $ US par mois pour 25 000 crédits ([CloudZero](https://www.cloudzero.com/blog/copilot-studio-pricing/), [Agent Comparison](https://agentcomparison.net/guides/copilot-studio-pricing-and-licensing-explained/)) ; carte graphique : voir la recommandation du module 4 |
| B | environ 9 400 crédits (2 pour une réponse générative, 10 pour l'ancrage sur les données du locataire, selon les mêmes sources), soit environ 94 $ US à l'usage, ou un bloc de 200 $ US | Mêmes sources ; le nombre de crédits par message dépend de la conception de l'agent |
| C | 60 × 28,50 $ CA = 1 710 $ CA par mois (facturation annuelle), en plus de la licence Microsoft 365 de base | [Page des prix de Microsoft Canada](https://www.microsoft.com/en-ca/microsoft-365-copilot/pricing), lue le 2026-11-11 |

## Qualité mesurée

| Option | Mesure | Résultat |
|---|---|---|
| A | Porte `full-ollama` du module 1 sur 50 questions (35 répondables, 15 pièges) | `gemma4:e4b` : 15 pièges refusés sur 15, citations valides 1,000, faits clés 0,948 (banc du 2026-10-31) ; la matrice du module 4 met ces chiffres à jour pour chaque modèle |
| B | — | **Non mesurée** : aucun locataire dans le labo |
| C | — | **Non mesurée** |

Le labo ne peut pas affirmer qu'une option hébergée répond mieux ou moins bien : seule l'option A passe par les portes du labo, avec ses pièges (« information absente », version périmée, injection).

## Conformité (lecture du labo, pas un avis juridique)

| Critère | A | B | C |
|---|---|---|---|
| Documents qui quittent le poste | aucun | la bibliothèque est indexée par le service | idem |
| Inférence hors du Canada | non | **oui** (Azure OpenAI aux États-Unis pour un environnement au Canada, ou impossible si le transfert entre régions est refusé) | selon les engagements de résidence de Microsoft 365 Copilot à la date de l'achat |
| Question et réponse conservées chez Microsoft | oui (Teams) | oui | oui |
| Pièges et refus mesurés par le labo | oui | non | non |
| Contrôle par l'entreprise du modèle et des versions | complet | aucun | aucun |

## Lecture

Pour une PME qui garde ses documents au Canada et veut une qualité mesurée, l'option A coûte le moins par mois et garde l'inférence sur place ; elle demande un poste à entretenir. L'option B est la plus simple à configurer, mais l'inférence se fait aux États-Unis pour un environnement canadien et sa qualité n'est pas mesurée ici. L'option C sert d'autres usages (courriels, réunions, documents) et ne se justifie pas pour le seul besoin de la documentation Qualité.
