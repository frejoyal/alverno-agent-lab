# Banc d'essai — mode complet (ollama)

| Modèle | porte | pièges refusés | fausses réponses | refus injustifiés | citations valides | faits clés | p50 | p95 | erreurs | jetons |
|---|---|---|---|---|---|---|---|---|---|---|
| `llama3:latest` | ❌ | 0,933 | 0,067 | 0,000 | 0,943 | 0,824 | 12,2 s | 24,3 s | 0,000 | 52 215 |

Mesures prises le 2026-11-12 sur 50 questions par modèle (35 répondables, 15 pièges). Recherche, identique pour tous les modèles : hit@1 0,971 · MRR 0,986 · rappel du contexte 0,990. Plongements `qwen3-embedding:0.6b`, reranker `none`, barrière `cosine` < 0,55. Porte : profil `full-ollama` de `evals/thresholds.yaml`. Généré par `alverno bench` ; ne pas modifier à la main.
