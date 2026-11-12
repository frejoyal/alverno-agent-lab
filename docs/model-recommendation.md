# Quel modèle pour quelle tâche, et sur quel matériel ? (module 4)

Recommandation produite le 2026-11-12 par `alverno matrix` ([rapport complet](../reports/matrix-20261111/matrix.md)), selon la règle écrite avant toute mesure ([ADR 0024](adr/0024-bench-evaluation-protocol.md)) : pour chaque tâche, **le modèle admissible le plus léger** (porte de la tâche passée, chaque compte de sécurité à 0, aucune erreur), confirmé par trois exécutions. Poste de mesure : RTX 3060 Ti de 8 Go, 32 Go de mémoire vive, Ollama 0.24.0, contexte de 8192 jetons.

## Recommandation par tâche

| Tâche | Modèle retenu | Mémoire mesurée | Palier | Confirmation (k = 3) |
|---|---|---|---|---|
| T1 Qualité | `ministral-3:8b` | 7,86 Go (5,30 sur la carte, 2,56 en mémoire vive) | 12 Go | 3 sur 3 ; faits clés 0,838, citations 0,966 |
| T2 RH | `gemma4:e4b` | 9,97 Go | 12 Go | 3 sur 3 ; faits clés 0,833, pièges de confidentialité 1,000 |
| T3a Service | `gemma4:e4b` | 9,97 Go | 12 Go | 3 sur 3 ; succès 1,000 |
| T3b Entretien | `ministral-3:8b` | 7,86 Go | 12 Go | 3 sur 3 ; diagnostic au 1er rang 0,938 |
| T5b Direction | `llama3.2:3b` | 3,16 Go | 8 Go | 3 sur 3 ; succès 0,821, comptes fidèles 1,000 |
| T0 Recherche | `bge-m3` | 1,08 Go | 8 Go | 3 sur 3 ; hit@1 0,943 |

**Palier d'ensemble : 12 Go de mémoire graphique** (le modèle retenu le plus lourd, `gemma4:e4b`, tient dans 12 Go avec 1 Go de marge).

Un palier « tient » quand la mémoire totale mesurée du modèle, plus 1 Go de marge, ne dépasse pas la mémoire de la carte ([ADR 0022](adr/0022-model-matrix.md)). Seul le palier de 8 Go est mesuré ; les autres sont estimés à partir de la mémoire mesurée.

## Lecture

- **Un seul modèle passe les cinq tâches : `gemma4:e4b`.** La règle choisit le plus léger par tâche, donc trois modèles différents ; une PME qui préfère n'en exploiter qu'un seul peut retenir `gemma4:e4b` partout, au prix de 2 Go de plus sur T1 et T3b. Ce choix d'exploitation n'est pas celui de la règle et n'est pas mesuré comme tel.
- **Sur la carte de 8 Go, tout déborde déjà.** `gemma4:e4b` garde 3,3 Go sur la carte et 6,7 Go en mémoire vive ; `ministral-3:8b` alterne avec le modèle de plongements et Ollama le recharge presque à chaque question : sa latence p95 atteint 34 s sur T1, contre 15,6 s pour `gemma4:e4b`. Les modèles tournent quand même, mais lentement.
- **Les petits modèles ne suffisent pas, sauf pour la direction.** `llama3.2:3b`, `gemma3:latest` et `llama3:latest` échouent aux portes Qualité, RH, service et entretien (faits clés trop bas, pièges non refusés ou succès des tâches insuffisant ; `gemma3:latest` a même un compte de sécurité non nul en entretien). Pour la direction, où les comptes viennent du code, `llama3.2:3b` passe et coûte 3 Go.
- **Le gros modèle n'apporte presque rien.** `gemma4:26b` (18,8 Go, dont 13,4 en mémoire vive) passe T1 avec les mêmes faits clés que `gemma4:e4b` (0,948), mais une latence médiane de 70 s.
- **Recherche.** `bge-m3` et `qwen3-embedding:0.6b` (le modèle du labo) occupent la même mémoire ; la règle départage par la latence p95 et retient `bge-m3`, alors que `qwen3-embedding:0.6b` a de meilleurs scores (hit@1 0,971 contre 0,943). L'index du labo n'est pas changé par ce module.

## Matériel et prix indicatifs

Prix lus le 2026-11-11, au Canada, avant taxes ; ils varient beaucoup et étaient, à cette date, nettement au-dessus de leur médiane des 12 derniers mois.

| Palier | Carte (exemple) | Prix indicatif | Source |
|---|---|---|---|
| 8 Go | RTX 5060 8 Go, neuve | environ 660 $ CA | [GPUPrix](https://gpuprix.com/gpus/geforce-rtx-5060) |
| 12 Go | RTX 5070 12 Go, neuve | environ 1 270 $ CA (1 259 à 1 349 $ chez un détaillant) | [GPUPrix](https://gpuprix.com/gpus/geforce-rtx-5070), [Canada Computers](https://www.canadacomputers.com/en/search?s=5070&t=1) |
| 16 Go | RTX 5060 Ti 16 Go, neuve | environ 1 170 $ CA | [GPUPrix](https://gpuprix.com/gpus/geforce-rtx-5060-ti-16gb) |
| 24 Go | RTX 3090 24 Go, d'occasion | environ 1 700 à 2 400 $ CA | [Kijiji](https://www.kijiji.ca/b-canada/rtx-3090/k0l0), [BestValueGPU](https://bestvaluegpu.com/en-ca/history/new-and-used-rtx-3090-price-history-and-specs/) |

**Conseil d'achat (lecture du labo).** Le palier recommandé est 12 Go ; à la date de lecture, la carte de 16 Go coûtait moins cher que celle de 12 Go et laisse de la marge pour un contexte plus long ou un modèle de plongements chargé en même temps. Un modèle de 18 Go comme `gemma4:26b` ne se justifie pas pour ces tâches.

## Référence hébergée (informative)

`claude -p` (modèle `claude-haiku-5-5`, interface 2.1.321, abonnement existant, aucune clé d'API), sur les items Qualité retenus par le filtre de l'[ADR 0023](adr/0023-hosted-reference.md) : 28 items admissibles, 13 écartés parce qu'un extrait récupéré venait d'une spécification de client, **15 invites envoyées**. Les quatre comptes (`hosted_pi_sent`, `hosted_trap_sent`, `hosted_spec_sent`, `hosted_calls_default`) valent 0, et chacun de leurs contrôles négatifs les fait passer à 1.

Sur ces 15 mêmes questions, avec la même recherche locale :

| Modèle | Faits clés | Citations valides | Refus injustifiés | Latence p50 |
|---|---|---|---|---|
| `claude-haiku-5-5` (hébergé) | 0,933 | 1,000 | 0 | 5,8 s |
| `gemma4:26b` | 0,967 | 1,000 | 0 | 80,0 s |
| `gemma4:e4b` | 0,933 | 1,000 | 0 | 8,7 s |
| `ministral-3:8b` | 0,867 | 0,960 | 0 | 26,6 s |
| `llama3.2:3b` | 0,867 | 0,979 | 0 | 4,3 s |
| `llama3:latest` | 0,867 | 0,875 | 0 | 14,8 s |
| `gemma3:latest` | 0,733 | 0,769 | 2 | 3,3 s |

Les modèles locaux sont pris dans leur exécution de criblage (k = 1), restreinte aux mêmes 15 questions. Sur ce sous-ensemble, le modèle hébergé ne répond pas mieux que `gemma4:e4b` en local. Le résultat est informatif : il n'entre pas dans la règle.

## Limites

Un seul poste mesuré ; les paliers de 12 à 24 Go sont estimés. Les jeux ont servi à régler les modules et avantagent `gemma4:e4b`, le modèle par défaut. Les modèles de plongements sont mesurés sans préfixe de requête propre à chacun, ce qui peut désavantager `nomic-embed-text`. La vitesse en jetons par seconde vient d'une sonde fixe de 128 jetons, pas des exécutions. La référence hébergée ne porte que sur 15 questions de niveau 2.
