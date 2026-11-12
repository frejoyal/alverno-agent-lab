# ADR 0024 — Protocole du banc par tâche et règle de recommandation (module 4)

- **Statut** : accepté le 2026-11-11, avec l'approbation de la version 1.0 de l'[EFVP du module 4](../efvp/bench-module.md)
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

## Précisions de mise en œuvre (2026-11-11, avant toute mesure)

Écrites à l'étape I2, avant le criblage ; elles ne changent ni les portes ni la règle.

- **Confirmation** : trois exécutions neuves ; l'exécution de criblage n'est pas comptée. Pour T3a, T3b et T5b, le banc du module exécute le modèle trois fois et applique lui-même la médiane des métriques de service, comme ses bancs publiés. Les commandes de T1, T2 et T0 n'ont pas de répétition : elles sont lancées trois fois et **chaque exécution doit passer sa porte seule**, ce qui est plus strict qu'une médiane.
- **Égalité de mémoire** : les modèles admissibles sont triés par mémoire totale ; un modèle rejoint le groupe du plus léger qui le précède s'il est à 0,5 Go ou moins de lui ; chaque groupe est trié par latence p95.
- **Comptes de sécurité** : les bornes `max` entières égales à 0 du profil de la tâche, sauf les préconditions `n_*` ; pour T0, ceux de `hr-retrieval-ollama` aussi, sous le préfixe `hr_`.
- **Erreur** (bloc du README) : `error_rate` supérieur à 0, ou une commande qui se termine avec un code non nul.
- **Modèle le plus fort** (pour information) : le modèle admissible au criblage qui a la plus haute première métrique publiée de la tâche (T1 et T2 : `key_fact_recall` ; T3a et T5b : `task_success` ; T3b : `diagnosis_hit_at_1` ; T0 : `hit_at_1`), les suivantes départageant ; sans modèle admissible, parmi tous.

### Balayage des témoins : précision du 2026-11-12, après le criblage

Le balayage final de la matrice (`reports/matrix-20261111/`) a trouvé 4 témoins `texte` dans des rapports Markdown : trois latences p50 (un rapport de recherche, `summary.md` du banc Qualité et `matrix.en.md`) et un compte `citations_checked` (rapport du banc de direction de `llama3:latest`). Chaque fois, un nombre court, sans type, des jeux du service à la clientèle égalait par hasard une mesure écrite par le code ; aucun texte servi ne le contenait, et le JSON voisin de chaque rapport n'avait aucune occurrence. C'est le même cas qu'à l'étape I8 du module 3 ([ADR 0016](0016-ops-gate-calibration.md)), pour les nombres JSON.

La règle a été précisée dans `evals/canaries.py` : dans un fichier Markdown, un témoin numérique n'est plus cherché dans une cellule de tableau ni dans une ligne `- métrique : valeur` qui ne contient qu'un nombre, avec une unité au plus ; tout autre texte reste balayé, y compris une réponse citée dans un tableau (test `test_a_markdown_measurement_is_never_a_numeric_canary_but_its_text_is`). Puis seul le balayage a été refait : aucune exécution n'a été relancée ni écartée, et aucune valeur mesurée n'a changé. La règle de recommandation et les portes ne changent pas.

## État du critère de sortie (2026-11-12, étape I8)

| Point | État |
|---|---|
| 1. EFVP v1.0 et ADR approuvés avant le code | atteint : approuvés le 2026-11-11, avant le premier commit `src/` du module |
| 2. CI verte, empreintes inchangées | atteint avec la PR du module |
| 3. Criblage complet ; une confirmation ou « aucun modèle » par tâche | atteint : 29 paires ; 6 confirmations réussies |
| 4. Comptes de la référence hébergée à 0, contrôles négatifs | atteint : 0 sur 15 invites ; chaque contrôle donne 1 |
| 5. Recommandation et palier publiés | atteint : [docs/model-recommendation.md](../model-recommendation.md) et bloc `RESULTS-MATRIX` du README |
| 6. EFVP v1.1 avec les résultats | atteint : approuvée le 2026-11-12 |

## Solutions écartées

- **Choisir le meilleur modèle de chaque tâche** : une PME paie le matériel ; le plus léger qui passe la porte est la question utile.
- **Une note pondérée qualité-latence-mémoire** : les poids seraient arbitraires et réglables après coup.
- **Confirmer chaque modèle admissible à k = 3** : temps du poste multiplié pour un résultat qui ne change pas la règle.

## Conséquences

- La recommandation peut retenir un petit modèle qui passe de justesse ; la marge est visible dans la matrice.
- Une tâche sans modèle admissible est un résultat publié, pas une raison de changer la règle.
