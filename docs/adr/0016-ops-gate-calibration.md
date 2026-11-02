# ADR 0016 — Module 3 : calibrer les bornes de service des profils `*-full-fake`

- **Statut** : accepté (partie 1 : 3a, moteurs simulés, 2026-11-02) ; la partie 2 (3b) sera ajoutée à l'étape I9
- **Date** : 2026-11-02

## Contexte

L'[ADR 0015](0015-ops-evaluation-protocol.md) a préenregistré toutes les bornes du module 3 avant la première exécution, sauf les **bornes de service des profils `svc-full-fake` et `maint-full-fake`**. Il les confie à cet ADR, à fixer **une seule fois**, après la première mesure `--fake`, par la règle de marge de l'[ADR 0011](0011-hr-gate-calibration.md). Les comptes de sécurité, les taux de contrôle et les préconditions de ces profils étaient déjà inscrits ; cet ADR n'y touche pas.

Comme dans l'ADR 0011, ces bornes sont fixées sur le jeu de référence lui-même. Elles protègent contre une régression du chemin simulé ; elles ne mesurent pas la qualité d'un modèle. La preuve hors échantillon reste le jeu réservé de l'ADR 0015, que cet ADR n'a ni ouvert ni mesuré.

Cet ADR ne contient aucun énoncé juridique ; ceci n'est pas un avis juridique.

## Décision

### Règle de marge (ADR 0015)

Une borne `min` est la valeur mesurée moins le poids d'un item du dénominateur de la métrique, arrondie vers le bas au centième ; une borne `max` est la valeur mesurée plus ce poids, arrondie vers le haut au centième ; la borne est ramenée dans [0 ; 1]. Les dénominateurs sont ceux du tableau des métriques de service de l'ADR 0015 : 1/26 pour `key_fact_recall` et `task_success`, 1/16 pour `draft_withheld_rate`, un texte présenté (réponse ou premier brouillon affiché) pour `status_fidelity`.

## Partie 1 : 3a, profil `svc-full-fake`

### Mesure

Mesure du 2026-11-02, avec `alverno service eval --mode full --fake --no-gate`, sur le code de l'étape I7 : rédacteur `ChatDrafter`, un appel `complete_json` par brouillon, sur `FakeChat` avec le répondeur `ops_draft_echo` (modèle `fake-ops`), qui reprend le bloc « Faits de la commande » de l'invite avec les marques `{salutation}` et `{signature}`.

- Jeu : `data/eval/svc_gold.jsonl`, 48 items (`dataset_sha256` `7ff8a91cb46e2daff303279746eecb46a0a2e68c38f95fe98e3d05a953aefafb`).
- Fixtures : `erp.yaml` `f6f7468dc544353bd05f0206109cbe721e2c018376639f81237032a8991272ba`, `gmao.yaml` `4393559e4fedef4d9c35c8a5d8488cf461a6ddb493143cca4b5ee2373acdcc8a`, `vocabulary.yaml` `fce003c1939c95325736a8afe9a58e97da2d167145d603e50230987bf0f22c50`.
- Empreintes des seuils au moment de la mesure : `thresholds_sha256` `243dcb39b800c96aabe89b796f74cb4856620dc57f08b616e1bc6ad17ae3b34a` (module 2, inchangée) et `ops_thresholds_sha256` `f1562ac4e6ff4c75649e57b9d71b6eeb1817f6d1583757cff207560ce4bd570c` (avant l'ajout des bornes de cet ADR).

Les rapports de calibration ne sont pas versionnés. La commande les reproduit, sauf les latences, les horodatages et `ops_thresholds_sha256`.

### Bornes retenues

| Métrique | Valeur mesurée | Dénominateur | Marge | Borne |
|---|---|---|---|---|
| `key_fact_recall` | 1,0 | 26 items répondables | 1/26 = 0,038462 | `min` **0,96** |
| `status_fidelity` | 1,0 (26 sur 26) | 26 textes présentés | 1/26 = 0,038462 | `min` **0,96** |
| `task_success` | 1,0 (26 sur 26) | 26 items répondables | 1/26 = 0,038462 | `min` **0,96** |
| `draft_withheld_rate` | 0,0 (0 sur 16) | 16 brouillons `customer_reply` | 1/16 = 0,0625 | `max` **0,07** |

Calcul : 1,0 − 0,038462 = 0,961538, arrondi vers le bas au centième : 0,96. 0,0 + 0,0625 = 0,0625, arrondi vers le haut au centième : 0,07.

Les autres métriques de la même mesure, qui ne sont pas calibrées : les 22 comptes de sécurité valent 0 ; les 7 taux de contrôle valent 1,0 ; `false_block_rate` vaut 0,0 ; les préconditions égalent les tailles prévues (48, 22, 8, 19, 5, 5, 3, 14). Informatif : 19 appels au modèle (16 `customer_reply` et 3 `auto_send`), aucun brouillon retenu par `verify` ni par une règle de la garde.

Après l'ajout de ces quatre bornes, `ops_thresholds_sha256` vaut `417cadd54e23ecc1a696e23d75b012f8397f1e47849d7d18d034169d67ad15ea`. Le registre des empreintes de l'ADR 0015 reçoit une ligne datée du 2026-11-02 ; la ligne du 2026-11-01 reste. `tests/test_thresholds_ratchet.py` épingle les quatre bornes. Elles ne bougent plus.

### Lecture

- **Ce que ces bornes valent.** `ops_draft_echo` reprend les faits par construction : 1,0 était attendu, et la marge d'un item n'autorise qu'une régression d'un item. Ces bornes détectent une régression du chemin complet (invite, `verify`, `compose`, garde, approbation) ; elles ne disent rien de la qualité d'un vrai modèle.
- **La vraie mesure** est `svc-full-ollama`, dont les bornes sont préenregistrées dans l'ADR 0015 (`key_fact_recall` ≥ 0,80, `status_fidelity` ≥ 0,90, `task_success` ≥ 0,75, `draft_withheld_rate` ≤ 0,19), puis la mesure unique du jeu réservé (étape I8).

### Barrières ajoutées à l'étape I7, avant la mesure

La revue adverse de l'étape I7 a montré qu'un modèle pouvait inventer un fait sans écrire de chiffre. Deux barrières ont été ajoutées **avant** la mesure ci-dessus :

- **Termes sans appui** (`verify`, `find_unsupported_terms`) : des listes fermées de nombres écrits en lettres, de mois et de jours, de liens et d'adresses électroniques, de mots d'engagement commercial (rabais, crédit, remboursement, gratuité et termes voisins) et de mots-outils anglais. Un terme de ces listes absent du bloc de faits retient le brouillon (`ungrounded`).
- **Quarantaine du brouillon** (garde, `quarantine_stop`) : un brouillon du modèle qui porte une instruction, une demande d'action ou un secret est retenu (`output_pi`), comme une modification de l'opérateur.

Ces listes sont des heuristiques. Avec un vrai modèle, elles peuvent retenir un brouillon correct (« neuf » au sens de « nouveau », « frais » au sens propre) ; `draft_withheld_rate` de `svc-full-ollama` le mesurera.

Risques résiduels, laissés à l'approbation humaine : une affirmation fausse qui n'emploie aucun terme de ces listes est servie ; un nom de mois présent dans les faits de la commande est considéré comme appuyé, même employé pour une autre date.

La règle d'écho de la garde reste désactivée en 3a ([ADR 0012](0012-ops-architecture-typed-tools.md), précisions de l'étape I6) : la mesure n'a retenu aucun brouillon par une règle de la garde, et aucun écho filtré n'a été ajouté.

## Partie 2 : 3b, profil `maint-full-fake`

À écrire à l'étape I9, après la première mesure `alverno maint eval --mode full --fake`, par la même règle : 1/16 pour `diagnosis_hit_at_1` et `diagnosis_hit_at_3`, 1/4 pour `frequency_trap_hit_at_1`, une citation pour `citation_validity_rate`. `ops_diagnose_echo` manque exprès les pièges de fréquence ; la partie 2 le consignera.

## Solutions écartées

- **Prendre les bornes de `svc-full-ollama`** pour `svc-full-fake` : elles décrivent un vrai modèle ; appliquées au répondeur simulé, elles laisseraient passer jusqu'à 6 items ratés sur 26 sans signal.
- **Fixer les bornes à 1,0 sans marge** : contraire à la règle écrite d'avance dans l'ADR 0015.
- **Calibrer avant d'ajouter les barrières de l'étape I7** : les bornes décriraient un autre système que celui qui est livré.
- **Recalibrer après une modification future** : les bornes ne bougent plus ; une régression se corrige dans le système.

## Conséquences

Positives :

- Le profil `svc-full-fake` est complet : une régression de plus d'un item sur le chemin complet fait échouer la porte de l'intégration continue.
- La mesure, les empreintes et le calcul sont publiés ; la calibration se refait à l'identique.

Négatives :

- Les bornes du chemin simulé ne prouvent rien sur la qualité d'un modèle : seuls `svc-full-ollama` et le jeu réservé le peuvent.
- Les listes fermées de `verify` peuvent retenir des brouillons corrects ; le coût sera mesuré, pas présumé.

Voir aussi l'[ADR 0011](0011-hr-gate-calibration.md) (règle de marge), l'[ADR 0012](0012-ops-architecture-typed-tools.md), l'[ADR 0015](0015-ops-evaluation-protocol.md) et l'[EFVP du module 3](../efvp/ops-module.md).
