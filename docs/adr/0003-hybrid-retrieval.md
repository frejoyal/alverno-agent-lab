# ADR 0003 — Recherche hybride BM25 + vecteurs avec fusion RRF

- **Statut** : accepté
- **Date** : 2026-10-31

## Contexte

La documentation qualité mélange deux types de requêtes :

- **termes exacts** : identifiants (`QP-007`, `SPEC-…`), normes (`CSA W59`), nuances (`316L`), valeurs et unités (`µm`, `mm`) ;
- **reformulations** en langage d'atelier : « petit creux le long du cordon » pour « caniveau », « la peinture pèle » pour « défaut d'adhérence ».

Une recherche vectorielle seule rate souvent les identifiants et les valeurs ; BM25 seul rate les reformulations. Les modèles d'embeddings locaux de petite taille accentuent l'écart. Une réponse fondée sur le mauvais extrait peut mener à une tolérance erronée.

## Décision

- Deux index : `BM25Index` (rank-bm25, tokenizer français simple : minuscules, accents conservés, courte liste de mots vides) et `VectorIndex` numpy (similarité cosinus, embeddings `qwen3-embedding:0.6b` normalisés L2).
- Chaque extrait est préfixé d'un en-tête contextuel léger « titre — section » pour les vecteurs et le reranker ; BM25 indexe seulement la section et le corps (ADR 0004).
- Fusion par **Reciprocal Rank Fusion** (`rrf_fuse`, k = 60) sur les `top_k` = 20 résultats de chaque index. Fonction pure, testée unitairement.
- **Reranker optionnel** (`CrossEncoderReranker`, extra `rerank`, modèle multilingue `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`) ; `NoopReranker` sans l'extra ; une fois l'extra installé, le réglage par défaut `reranker = auto` active le cross-encoder.
- Score final : 0,45 × score de reranking normalisé + 0,55 × score RRF normalisé, + 0,2 si l'extrait est premier dans les deux index.
- **Barrière no-match** : si le meilleur score de reranking est sous `no_match_threshold` (sans reranker : la meilleure similarité cosinus sous `vector_no_match_threshold`, ADR 0004), le nœud `gate` refuse la question (« aucune source ») sans appeler le LLM.
- On garde `final_k` = 5 extraits pour le LLM.

## Options écartées

- **Vecteurs seuls** : simple, mais faible sur les identifiants et les valeurs numériques.
- **Base vectorielle externe** (serveur dédié) : inutile pour quelques centaines d'extraits ; numpy suffit et reste transparent. À revoir au module 5 si le volume grandit.
- **Reranking LLM** : trop lent sur un poste sans GPU puissant pour un gain incertain.

## Conséquences

- Meilleure robustesse sur les deux types de requêtes, mesurée par hit@1, hit@3 et MRR dans le jeu de référence.
- Deux index à maintenir ; le manifeste du `VectorIndex` (modèle, empreinte du corpus) empêche d'utiliser un index périmé.
- Les pondérations (0,45 / 0,55, bonus 0,2) et le seuil no-match sont des hyperparamètres : toute modification doit être justifiée par une exécution des évaluations.
