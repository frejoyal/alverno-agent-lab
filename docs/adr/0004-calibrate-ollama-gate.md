# ADR 0004 — Calibrer la barrière et BM25 sur une mesure réelle

- **Statut** : accepté
- **Date** : 2026-10-31

## Contexte

La première exécution réelle (Ollama, embeddings `qwen3-embedding:0.6b`, sans reranker, 50 questions) a révélé trois écarts avec les hypothèses de départ.

1. **Barrière cosinus trop basse.** Avec le seuil initial de 0,45, la recherche seule refusait à peine la moitié des pièges (refus correct 0,53). Meilleure similarité cosinus mesurée parmi les 5 extraits retenus : pièges hors sujet de 0,34 à 0,71, pièges « information absente » de 0,48 à 0,62, injections de 0,53 à 0,62, questions répondables 0,57 et plus.
2. **Pièges « information absente » inséparables.** Ces questions portent sur des sujets du corpus (un document qui existe, une valeur qui n'y figure pas). Aucun seuil cosinus ne les sépare des vraies questions sans refuser aussi des questions légitimes.
3. **Extrait coupé du contexte (q006).** Le bon document arrivait premier (hit@1 au niveau document), mais l'extrait qui contient la réponse (« RAL 7035 ») arrivait 7ᵉ après la fusion et n'était pas transmis au LLM. Cause : le titre du document est répété dans l'en-tête de chaque extrait. Une question qui nomme le document donnait un score BM25 à tous ses extraits, et la normalisation de longueur favorisait les plus courts. Les métriques au niveau document ne voyaient rien ; les deux modèles mesurés refusaient la question faute de citation valide.

## Décision

- **Barrière cosinus à 0,55** (`vector_no_match_threshold`) : elle refuse les pièges les plus éloignés sans refuser aucune question répondable (minimum mesuré : 0,569). La marge est mince ; il faut recalibrer si le modèle d'embeddings, le découpage ou le corpus change.
- **Profil `retrieval-ollama`** : les bornes de refus des pièges sont ramenées à ce que la recherche seule peut atteindre (refus correct ≥ 0,55, fausses réponses ≤ 0,45), et les bornes de recherche sont relevées (hit@1 ≥ 0,9, MRR ≥ 0,9, rappel du contexte ≥ 0,95). La vraie porte des pièges est le profil `full-ollama` (refus correct ≥ 0,9, fausses réponses ≤ 0,1), où le LLM et la vérification des citations refusent le reste.
- **BM25 sur la section et le corps** (`Chunk.lexical_text`), sans le titre ; les vecteurs et le reranker gardent l'en-tête complet (`Chunk.indexed_text`). **b = 0,3** au lieu de 0,75, pour réduire l'avantage des extraits très courts.
- **Nouvelle métrique `context_recall`** : part des faits clés (`must_include`) présents dans les extraits retenus, au niveau des extraits. Elle se calcule sans LLM, en mode `retrieval` comme en mode `full`, et figure dans les quatre profils de seuils. Un test de régression reproduit le mécanisme de q006.

## Mesures

Mode `retrieval`, Ollama, 35 questions répondables, 5 extraits retenus sauf mention contraire.

| Variante BM25 | hit@1 | MRR | rappel du contexte |
|---|---|---|---|
| titre + section + corps, b = 0,75 (avant) | 0,971 | 0,986 | 0,962 |
| section + corps, b = 0,75 | 0,914 | 0,957 | 0,990 |
| corps seul, b = 0,75 | 0,886 | 0,943 | 0,933 |
| titre + section + corps, b = 0,3 | 0,943 | 0,971 | 0,990 |
| **section + corps, b = 0,3 (retenue)** | **0,971** | **0,986** | **0,990** |
| avant, avec 7 extraits retenus | 0,971 | 0,986 | 0,990 |

Retenir 7 extraits donne le même rappel du contexte, mais ajoute deux extraits à chaque prompt (+40 % de contexte, donc plus de jetons et de latence). Une limite d'extraits par document a aussi été essayée : sans gain, elle est écartée.

## Conséquences

- La seule question encore incomplète au niveau du contexte est q027 : elle compare une spécification client à « l'instruction générale » sans la nommer, et l'instruction IT-210 n'est pas récupérée.
- Les seuils et le réglage ont été choisis sur le jeu de référence lui-même. Un jeu de validation séparé est prévu pour vérifier qu'ils généralisent.
- Tout changement de modèle d'embeddings, de découpage ou de corpus exige de remesurer la distribution cosinus avant de toucher au seuil.
