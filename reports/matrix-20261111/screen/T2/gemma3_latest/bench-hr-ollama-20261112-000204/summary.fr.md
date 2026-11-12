# Banc d'essai RH — mode complet (ollama)

| Modèle | porte | pièges de confidentialité refusés | motif exact | fuites | témoins persistés | refus injustifiés | citations valides | faits clés | p50 | erreurs | jetons |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `gemma3:latest` | ❌ | 1,000 | 1,000 | 0 | 0 | 0,722 | 1,000 | 0,222 | 1,9 s | 0,000 | 61 448 |

Mesures prises le 2026-11-12 sur 64 questions par modèle (36 répondables, 28 pièges, dont 20 pièges de confidentialité ; 16 paires). Fuites comptées sur les 13 questions avec témoins : 0 sur 13 (borne supérieure de 3/13 = 0,231 à 95 %). Recherche, identique pour tous les modèles : hit@1 0,972 · MRR 0,986 · rappel du contexte 0,972. Plongements `qwen3-embedding:0.6b`, reranker `none`, barrière `cosine` < 0,47. Porte : profil `hr-full-ollama` de `evals/thresholds.yaml`. Généré par `alverno hr bench` ; ne pas modifier à la main.
