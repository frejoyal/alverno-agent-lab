# ADR 0024 — Protocole du banc par tâche et règle de recommandation (module 4)

- **Statut** : proposé le 2026-11-11, en attente de l'approbation de l'[EFVP du module 4](../efvp/bench-module.md)
- **Date** : 2026-11-11

## Contexte

Une recommandation choisie après avoir vu les chiffres peut toujours se justifier. La règle doit donc être écrite avant toute mesure, comme les bornes des modules précédents. Les portes de chaque tâche existent déjà ; ce module n'en ajoute aucune.

Cet ADR ne cite aucun énoncé juridique ; **ceci n'est pas un avis juridique**.

## Décision

### Admissibilité d'une paire (tâche, modèle)

Une exécution est **admissible** quand :

1. la porte du profil de la tâche (`evals/thresholds.yaml`, inchangé) passe ;
2. chaque compte de sécurité du profil vaut 0 ;
3. `error_rate` vaut 0 ;
4. pour T0, la porte de recherche du profil passe.

### Règle de recommandation (préenregistrée)

Pour chaque tâche :

1. **Criblage** : chaque modèle de l'[ADR 0022](0022-model-matrix.md) est exécuté une fois (k = 1).
2. **Classement** : les modèles admissibles sont classés par mémoire totale mesurée (`vram_gb + ram_spill_gb`), du plus petit au plus grand ; à égalité à 0,5 Go près, par latence p95 croissante.
3. **Confirmation** : le premier modèle du classement est exécuté deux fois de plus (k = 3 au total). Il est retenu si chacune des trois exécutions est admissible, les bornes de service s'appliquant à la médiane des trois, comme dans les bancs existants. Sinon, on passe au suivant du classement.
4. **Aucun modèle** : si aucun modèle n'est retenu, la tâche reçoit « aucun modèle local recommandé sur ce poste », publié tel quel.

Pour T0, la même règle s'applique aux modèles de plongements, avec la mémoire du modèle de plongements.

La règle ne choisit pas le meilleur modèle, mais le plus léger qui suffit. Le modèle le plus fort de chaque tâche est publié à côté, pour information.

### Palier de matériel

Le palier recommandé pour une tâche est le plus petit palier de l'ADR 0022 dans lequel tient le modèle retenu. La recommandation d'ensemble est le palier du modèle retenu le plus lourd parmi T1 à T5b.

### Mesures publiées

Pour chaque exécution : la porte, les métriques de service du profil, le nombre de comptes de sécurité non nuls, `error_rate`, les latences p50 et p95, `tokens_per_s`, `vram_gb`, `ram_spill_gb`. La règle de trois de l'[ADR 0010](0010-hr-evaluation-protocol.md) accompagne chaque compte nul de la confirmation.

### Règles d'exécution

- Rien d'autre ne tourne sur le processeur graphique pendant une mesure ; le journal d'Ollama est vérifié avant chaque série, et une exécution perturbée est refaite une fois, avec une note.
- Le plafond de génération (`num_predict` = 1024) et la requête de remise à zéro du cache d'invite s'appliquent à toutes les exécutions.
- Chaque dossier de rapport est balayé avec les témoins des huit jeux avant d'être versé.

### Intégration continue

La CI exécute les tests de la commande sur des matrices construites et sur un modèle simulé ; elle n'exécute ni Ollama ni la référence hébergée. Les portes existantes et leurs empreintes (`thresholds_sha256`, `ops_thresholds_sha256`, `dir_thresholds_sha256`) ne changent pas.

### Critère de sortie (réfutable)

Le module 4 est livré quand :

1. l'EFVP v1.0 et les ADR 0022 à 0024 ont été approuvés avant la première ligne de code `src/` du module ;
2. la CI passe (lint, types, couverture d'au moins 85 %, toutes les portes existantes, empreintes inchangées) ;
3. la matrice de criblage couvre chaque paire prévue par l'ADR 0022 et chaque tâche a une confirmation ou un « aucun modèle » ;
4. les quatre comptes de l'[ADR 0023](0023-hosted-reference.md) valent 0 sur l'exécution de la référence hébergée, et leurs contrôles négatifs échouent comme prévu ;
5. la recommandation et le palier sont publiés dans `docs/model-recommendation.md` et dans le README, tels que la règle les donne ;
6. l'EFVP v1.1 contient les résultats.

### Incréments

| Incrément | Contenu |
|---|---|
| I0 | EFVP v1.0 et ADR 0022 à 0024 ; aucune ligne de `src/` |
| I1 | gardes de test : documents, emplacement de l'index, empreintes |
| I2 | `alverno matrix` : exécution des profils, mesures du matériel, matrice et règle, sur modèle simulé |
| I3 | référence hébergée : adaptateur, filtre, comptes et contrôles négatifs |
| I4 | criblage sur Ollama (conversation et plongements) |
| I5 | confirmations à k = 3 |
| I6 | exécution de la référence hébergée |
| I7 | recommandation, paliers et prix indicatifs ; README |
| I8 | EFVP v1.1 ; livraison |

## Solutions écartées

- **Choisir le meilleur modèle de chaque tâche** : une PME paie le matériel ; le plus léger qui passe la porte est la question utile.
- **Une note pondérée qualité-latence-mémoire** : les poids seraient arbitraires et réglables après coup.
- **Confirmer chaque modèle admissible à k = 3** : temps du poste multiplié pour un résultat qui ne change pas la règle.

## Conséquences

- La recommandation peut retenir un petit modèle qui passe de justesse ; la marge est visible dans la matrice.
- Une tâche sans modèle admissible est un résultat publié, pas une raison de changer la règle.
