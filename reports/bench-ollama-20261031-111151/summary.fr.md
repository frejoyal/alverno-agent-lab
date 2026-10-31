# Banc d'essai — mode complet (ollama)

| Modèle | porte | pièges refusés | fausses réponses | refus injustifiés | citations valides | faits clés | p50 | p95 | erreurs | jetons |
|---|---|---|---|---|---|---|---|---|---|---|
| `gemma4:e4b` | ✅ | 1,000 | 0,000 | 0,000 | 1,000 | 0,962 | 30,1 s | 35,8 s | 0,000 | 52 608 |
| `ministral-3:8b` | ✅ | 1,000 | 0,000 | 0,000 | 0,983 | 0,838 | 11,2 s | 15,6 s | 0,000 | 51 718 |
| `llama3.2:3b` | ❌ | 0,867 | 0,133 | 0,086 | 0,963 | 0,733 | 13,0 s | 19,9 s | 0,000 | 53 155 |

Mesures prises le 2026-10-31 sur 50 questions par modèle (35 répondables, 15 pièges). Recherche, identique pour tous les modèles : hit@1 0,971 · MRR 0,986 · rappel du contexte 0,990. Plongements `qwen3-embedding:0.6b`, reranker `none`, barrière `cosine` < 0,55. Porte : profil `full-ollama` de `evals/thresholds.yaml`. Généré par `alverno bench` ; ne pas modifier à la main.
