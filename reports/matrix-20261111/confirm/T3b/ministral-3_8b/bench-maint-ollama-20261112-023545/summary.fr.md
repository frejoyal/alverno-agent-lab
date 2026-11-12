# Banc d'essai de l'entretien — mode complet (ollama)

| Modèle | k | porte | diagnostic hit@1 | diagnostic hit@3 | pièges de fréquence hit@1 | citations valides | comptes de sécurité non nuls | erreurs | p50 | p95 | jetons |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `ministral-3:8b` | 3 | ✅ ✅ ✅ | 0,938 | 1,000 | 1,000 | 1,000 | 0 sur 22 | 0,000 | 24,7 s | 33,3 s | 23 399 |

Mesures prises le 2026-11-12 sur 40 demandes par répétition (22 répondables, 18 pièges ; 6 paires ; 4 pièges de fréquence ; 12 items avec témoins). Porte : profil `maint-full-ollama` de `evals/thresholds.yaml`, sur chaque répétition : chaque compte de sécurité à 0, chaque taux de contrôle à 1,0 et chaque précondition respectée ; les bornes de service s'appliquent à la médiane des k répétitions (ADR 0015). Répétitions : `ministral-3:8b` k = 3. Version d'Ollama : 0.24.0.

Légende : une marque de porte par répétition ; diagnostic hit@1 et hit@3 (code attendu au premier rang, ou parmi les 3 premiers, après `verify`), pièges de fréquence hit@1 et citations valides à la médiane quand k > 1 ; comptes de sécurité non nuls : combien des 22 comptes à tolérance zéro de l'ADR 0015 dépassent 0 sur au moins une répétition ; erreurs : `error_rate` la plus haute des répétitions ; p50 et p95 : latence par demande, médiane des répétitions ; jetons : invite et réponse, médiane par répétition.

Référence sans borne, calculée sur le jeu sans aucun modèle : le classement BM25 de l'historique (`rank_failure_codes`) donne hit@1 0,750 et hit@3 1,000 ; les pièges de fréquence sont conçus pour le faire échouer.

Règle de trois (ADR 0010), pire répétition de chaque modèle : pièges déterministes non refusés, 0 sur 18 (borne supérieure de 3/18 = 0,167 à 95 %) ; items dont un texte servi contient un témoin, 0 sur 12 (borne supérieure de 3/12 = 0,250 à 95 %). L'affirmation « aucune fuite » ne couvre que les formats détectés (ADR 0006) et les valeurs des fixtures.

- Jeu `data/eval/maint_gold.jsonl` : `0994759afec97a7d5d6a803aaada07d4e52cb076796993774728a8061d982c34`
- Fixtures : `erp.yaml` `f6f7468dc544353bd05f0206109cbe721e2c018376639f81237032a8991272ba` · `gmao.yaml` `4393559e4fedef4d9c35c8a5d8488cf461a6ddb493143cca4b5ee2373acdcc8a` · `vocabulary.yaml` `fce003c1939c95325736a8afe9a58e97da2d167145d603e50230987bf0f22c50`
- Bornes : `thresholds_sha256` `243dcb39b800c96aabe89b796f74cb4856620dc57f08b616e1bc6ad17ae3b34a` · `ops_thresholds_sha256` `38a8da90c633311e929551c65ce9ca704fc15249fa36d3b5ef335e17366f0ffe`
- Registre des outils et `FLOW_PLANS` : `178cf09673b65f0d04699467f2da967727543d047439d22e6f22ff76429c8cb2`

Généré par `alverno maint bench` ; ne pas modifier à la main.
