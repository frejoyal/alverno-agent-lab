# ADR 0028 — Protocole d'évaluation du module 6 et critère de sortie

- **Statut** : accepté le 2026-11-11, avec l'approbation de la version 1.0 de l'[EFVP du module 6](../efvp/m365-module.md)
- **Date** : 2026-11-11

## Contexte

Sans locataire réel, le module ne peut pas mesurer Teams ni Copilot Studio. Il peut mesurer ce que le dépôt contrôle : la synchronisation de la source, l'API, son contrôle d'accès, et la cohérence des fichiers de l'agent avec l'API. Comme aux modules précédents, les bornes sont écrites avant le code.

Cet ADR ne cite aucun énoncé juridique ; **ceci n'est pas un avis juridique**.

## Décision

### Profil `m365-fake` (préenregistré)

`alverno m365 eval --fake` synchronise l'export simulé, démarre l'API en mémoire (sans réseau) et pose, par l'API avec un jeton de test valide, les 50 questions de `quality_gold`, puis rejoue les contrôles d'accès.

| Métrique | Définition | Borne |
|---|---|---|
| `sync_identical` | 1 si le corpus synchronisé est identique, octet pour octet, à `data/corpus/` | = 1 |
| `sync_excluded_wrong_group` | éléments retenus alors que le groupe de l'agent n'est pas lecteur (export de contrôle) | = 0 |
| `api_parity` | part des 50 questions dont la réponse de l'API (texte, citations, refus et motif) est identique à celle de l'agent appelé directement | = 1,0 |
| `auth_bypass` | appels acceptés parmi les jetons invalides (absent, signature inconnue, expiré, pas encore valide, mauvais émetteur, mauvaise audience, mauvais locataire, sans le rôle) | = 0 |
| `auth_false_reject` | appels refusés avec un jeton valide | = 0 |
| `log_content_hits` | fragments d'une question ou d'une réponse retrouvés dans le journal des accès | = 0 |
| `canary_hits` | témoins des huit jeux retrouvés dans les fichiers du rapport | = 0 |
| `spec_api_coverage` | part des routes de l'API décrites par la définition du connecteur, et l'inverse | = 1,0 |
| `topic_action_valid` | 1 si chaque action appelée par un sujet existe dans la définition du connecteur | = 1 |
| `generative_flags` | réglages génératifs activés dans `agent.yaml` | = 0 |
| `error_rate` | erreurs de transport | = 0 |
| `n_items` | précondition | ≥ 50 |
| `n_auth_cases` | précondition | ≥ 8 |

Chaque compte a un contrôle négatif dans les tests : une API sans vérification du rôle fait passer `auth_bypass` au-dessus de 0 ; un journal qui garde la question fait passer `log_content_hits` au-dessus de 0 ; un export où le groupe manque fait passer `sync_excluded_wrong_group` au-dessus de 0 ; un sujet qui appelle une action inconnue fait passer `topic_action_valid` à 0.

### Ce qui n'est pas mesuré

La latence et la qualité dans Teams, le comportement de la passerelle, la rétention réelle de Microsoft 365. La comparaison avec les capacités natives de Copilot Studio ([comparison.md](../m365/comparison.md)) met en regard des coûts publiés, la qualité mesurée par le labo et les risques ; la qualité des réponses génératives de Copilot Studio y est marquée **non mesurée**.

### Non-régression

Les portes existantes et leurs empreintes (`thresholds_sha256`, `ops_thresholds_sha256`, `dir_thresholds_sha256`) ne changent pas ; le profil `m365-fake` est ajouté à la fin de `evals/thresholds.yaml`.

### Critère de sortie (réfutable)

Le module 6 est livré quand :

1. l'EFVP v1.0 et les ADR 0025 à 0028 ont été approuvés avant la première ligne de code `src/` du module ;
2. la CI passe, avec la porte `m365-fake` et toutes les portes existantes, empreintes inchangées ;
3. les contrôles négatifs de chaque compte échouent comme prévu ;
4. l'analyse de l'art. 17, la gouvernance, le plan d'adoption, la comparaison et le guide de déploiement sont publiés sous `docs/m365/` ;
5. l'EFVP v1.1 contient les résultats.

### Incréments

| Incrément | Contenu |
|---|---|
| I0 | EFVP v1.0 et ADR 0025 à 0028 ; aucune ligne de `src/` |
| I1 | export simulé et `alverno m365 sync` |
| I2 | API locale, authentification, journal |
| I3 | fichiers Copilot Studio et leurs vérifications |
| I4 | `alverno m365 eval` et porte `m365-fake` dans la CI |
| I5 | documents `docs/m365/` ; EFVP v1.1 ; livraison |

## Solutions écartées

- **Mesurer dans un locataire de développement** : écarté par le responsable du dépôt pour ce module.
- **Évaluer la qualité de l'agent sur Ollama par l'API** : l'API ne change ni la recherche ni le modèle ; la parité exacte avec l'agent appelé directement suffit, et les mesures sur Ollama du module 1 restent valides.

## Conséquences

- La porte prouve que la couche Microsoft 365 du dépôt n'ajoute ni ne retire rien aux réponses, et qu'elle refuse les jetons invalides ; elle ne prouve rien du service hébergé.
