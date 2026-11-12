# Plan d'adoption de l'Assistant Qualité dans Teams (module 6)

Plan proposé pour une entreprise fictive de 118 employés, sur deux quarts (jour et soir) ; il suppose une mise en service approuvée selon l'[EFVP du module 6](../efvp/m365-module.md).

## Étapes

| Étape | Durée | Qui | Fin de l'étape |
|---|---|---|---|
| 0. Préalables | 2 semaines | Soutien informatique, RPRP | Liste de vérification du [guide de déploiement](deployment-guide.md) remplie ; avis aux employés validé |
| 1. Pilote | 4 semaines | 8 employés : 4 de la production (2 quarts), 2 de la qualité, 1 de l'expédition, 1 superviseur | Critères du pilote atteints |
| 2. Élargissement | 4 semaines | Production et qualité, par quart | Taux d'utilisation et satisfaction mesurés |
| 3. Exploitation | continue | Responsable de la qualité | Revue trimestrielle |

## Formation (30 minutes, par quart)

1. Ce que l'agent fait : il répond à partir des procédures et cite le document ; il dit quand il ne sait pas.
2. Ce qu'il ne fait pas : RH, clients, commandes, entretien ; aucune décision à la place d'une personne.
3. Ce qu'on n'écrit pas : aucun nom, numéro d'employé ou renseignement sur un collègue.
4. Vérifier la citation avant d'agir sur une tolérance ou une valeur.
5. Signaler une réponse fausse : bouton de rétroaction ou courriel à qualite@alverno.example.

## Critères du pilote (fixés avant le pilote)

| Mesure | Cible |
|---|---|
| Questions par participant par semaine | au moins 3 |
| Réponses jugées utiles (pouce levé sur les réponses notées) | au moins 75 % |
| Réponses fausses signalées et confirmées | 0 sur une valeur de tolérance ou de sécurité |
| Questions refusées pour un RP | moins de 5 % (sinon, renforcer la formation) |

Les mesures sont agrégées, jamais par employé (clé `lprp_technologie_profilage`).

## Communication

- Message du responsable de la qualité dans le canal de l'usine avant le pilote.
- Affiche au babillard de chaque quart : à quoi sert l'agent, ce qu'il ne faut pas écrire, qui joindre.
- Avis au premier message de l'agent (fins, conservation dans Teams, RPRP : confidentialite@alverno.example).

## Risques d'adoption

| Risque | Réponse |
|---|---|
| Confiance excessive dans une réponse | Citation obligatoire ; formation au point 4 ; refus quand la source manque |
| Peu d'usage sur le quart de soir | Rappel par le superviseur ; mesure par quart, non par personne |
| Poste de l'API éteint | Surveillance du service ; message d'indisponibilité clair dans l'agent |
