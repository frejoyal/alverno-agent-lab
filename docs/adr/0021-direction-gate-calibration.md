# ADR 0021 — Calibration unique des profils `dir-tools-fake` et `dir-full-fake`

- **Statut** : accepté le 2026-11-11, dans le cadre de l'[ADR 0020](0020-direction-evaluation-protocol.md), sans nouvelle décision de conception
- **Date** : 2026-11-11

## Contexte

L'[ADR 0020](0020-direction-evaluation-protocol.md) a préenregistré, avant tout code du module 5, les comptes de sécurité, les taux de contrôle et les préconditions des quatre profils, les bornes de service de `dir-full-ollama` et la borne `count_fidelity` ≥ 1,0 de `dir-tools-fake`. Les autres bornes de service des profils `--fake` devaient être calibrées **une seule fois**, après la première mesure, avec la règle de marge de l'[ADR 0011](0011-hr-gate-calibration.md), comme l'a fait l'[ADR 0016](0016-ops-gate-calibration.md) au module 3.

Les sections Qualité et RH des profils `--fake` viennent des moteurs simulés des modules 1 et 2 (`FakeChat`, qui cite le premier extrait). Leurs faits clés et leur succès ne mesurent donc pas l'orchestrateur, mais ces moteurs : ils sont publiés et bornés pour qu'une régression se voie, pas pour juger la qualité des réponses.

Cet ADR ne cite aucun énoncé juridique ; il est rédigé en tenant compte du même cadre que l'ADR 0020 ; **ceci n'est pas un avis juridique**.

## Décision

### Mesure

Une exécution de chaque profil sur `data/eval/dir_gold.jsonl` (40 items), au commit de l'étape I5, le 2026-11-11 :

| Profil | `count_fidelity` | `key_fact_recall` | `citation_validity_rate` | `task_success` | Comptes de sécurité | Taux de contrôle |
|---|---|---|---|---|---|---|
| `dir-tools-fake` | 1,000 (20 sections) | 0,857 (28 items) | 1,000 (11 citations) | 0,821 (23 sur 28) | tous à 0 | tous à 1,0 |
| `dir-full-fake` | 1,000 (20 sections) | 0,857 (28 items) | 1,000 (11 citations) | 0,821 (23 sur 28) | tous à 0 | tous à 1,0 |

Les 5 items ratés sont tous des sections Qualité ou RH : 3 réponses du moteur simulé qui ne contiennent pas le fait attendu, 1 réponse qui en contient la moitié, et 1 refus `no_match` du module 1. Aucune section des commandes ou de l'entretien n'est ratée, et le routage est exact sur les 28 items répondables.

### Bornes

Règle de l'ADR 0011 : une borne `min` vaut la mesure moins 1/n, arrondie vers le bas au centième, où n est le dénominateur de la métrique.

| Profil | Métrique | Calcul | Borne |
|---|---|---|---|
| `dir-tools-fake` | `key_fact_recall` | 0,857 − 1/28 = 0,821 | `min` 0,82 |
| `dir-tools-fake` | `citation_validity_rate` | 1,000 − 1/11 = 0,909 | `min` 0,90 |
| `dir-tools-fake` | `task_success` | 0,821 − 1/28 = 0,786 | `min` 0,78 |
| `dir-full-fake` | `count_fidelity` | 1,000 − 1/20 = 0,950 | `min` 0,95 |
| `dir-full-fake` | `key_fact_recall` | 0,857 − 1/28 = 0,821 | `min` 0,82 |
| `dir-full-fake` | `citation_validity_rate` | 1,000 − 1/11 = 0,909 | `min` 0,90 |
| `dir-full-fake` | `task_success` | 0,821 − 1/28 = 0,786 | `min` 0,78 |

`count_fidelity` de `dir-tools-fake` reste à 1,0 (préenregistrée) : en mode outils, la section est le bloc de faits calculé par le code. Le rapport publie aussi `citations_checked`, le dénominateur de `citation_validity_rate`, sans borne.

### Registre

`dir_thresholds_sha256` passe de `ff5585ad…` à `1c6ef6c838a05c383673fb08384254b716757833cf49c98b30c143624e4c016e`. `thresholds_sha256` (`243dcb39…`) et `ops_thresholds_sha256` (`38a8da90…`) ne changent pas. La ligne est ajoutée au registre de l'ADR 0020.

Les bornes ne bougent plus : un recul fait échouer la porte, et l'on corrige le système.

## Solutions écartées

- **Fixer ces bornes à 1,0** : elles mesureraient les moteurs simulés des modules 1 et 2, qui ne visent pas 1,0 ; la porte échouerait sans défaut de l'orchestrateur.
- **Retirer les sections Qualité et RH des profils `--fake`** : la couverture des sections et le routage vers ces modules ne seraient plus mesurés dans la CI.

## Conséquences

- Les profils `--fake` peuvent échouer sur un recul d'un seul item de faits ou de succès.
- La qualité réelle des sections Qualité et RH se lit dans `dir-full-ollama`, dont les bornes sont préenregistrées.
