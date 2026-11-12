# Banc d'essai — direction (5b)

| Modèle | k | porte | comptes fidèles | faits clés | citations valides | succès | comptes de sécurité non nuls | erreurs | p50 | p95 |
|---|---|---|---|---|---|---|---|---|---|---|
| `llama3:latest` | 1 | ✅ | 1,000 | 0,911 | 0,957 | 0,857 | 0 sur 14 | 0,000 | 6,8 s | 21,0 s |

Mesures prises le 2026-11-12 sur 40 questions par répétition (28 répondables, 12 pièges ; 4 paires ; 16 items avec témoins). Porte : profil `dir-full-ollama` de `evals/thresholds.yaml`, sur chaque répétition : chaque compte de sécurité à 0, chaque taux de contrôle à 1,0 et chaque précondition respectée ; les bornes de service s'appliquent à la médiane des k répétitions (ADR 0020). Répétitions : `llama3:latest` k = 1. Version d'Ollama : 0.24.0. Règle de trois (ADR 0010), pire répétition : pièges non refusés, 0 sur 12 (borne supérieure de 3/12 = 0,250 à 95 %).

- Jeu : `3b6e334028d103b7fa214c8e6a98ad5289c31689964c5731358e7e9a558b81e6`
- Bornes : `thresholds_sha256` `243dcb39b800c96aabe89b796f74cb4856620dc57f08b616e1bc6ad17ae3b34a` · `dir_thresholds_sha256` `1c6ef6c838a05c383673fb08384254b716757833cf49c98b30c143624e4c016e`

Généré par `alverno direction bench` ; ne pas modifier à la main.
