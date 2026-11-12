# ADR 0022 — Matrice tâches × modèles locaux et mesures du matériel (module 4a)

- **Statut** : accepté le 2026-11-11, avec l'approbation de la version 1.0 de l'[EFVP du module 4](../efvp/bench-module.md)
- **Date** : 2026-11-11

## Contexte

Chaque module a publié un banc sur trois modèles (`gemma4:e4b`, `ministral-3:8b`, `llama3.2:3b`), avec sa propre porte. Personne n'a encore comparé les modèles d'une tâche à l'autre ni dit ce que coûte chaque modèle en matériel. Une PME qui n'a qu'un poste doit savoir si un petit modèle suffit pour une tâche, et quand un meilleur processeur graphique vaut la dépense.

Cet ADR ne cite aucun énoncé juridique ; l'analyse est dans l'EFVP du module 4 ; **ceci n'est pas un avis juridique**.

## Décision

### Tâches

| Tâche | Profil existant | Jeu | Ce qui est mesuré |
|---|---|---|---|
| T1 Qualité | `full-ollama` | `quality_gold` (50) | réponse citée, refus |
| T2 RH | `hr-full-ollama` | `hr_gold` (64) | réponse citée, refus, confidentialité |
| T3a Service | `svc-full-ollama` | `svc_gold` (48) | brouillon fidèle, approbation |
| T3b Entretien | `maint-full-ollama` | `maint_gold` (40) | diagnostic classé, citations |
| T5b Direction | `dir-full-ollama` | `dir_gold` (40) | sections, comptes fidèles |
| T0 Recherche | `retrieval-ollama`, `hr-retrieval-ollama` | `quality_gold`, `hr_gold` | modèle de plongements |

Chaque paire (tâche, modèle) lance l'évaluation existante, sans modification de l'agent, et garde son rapport complet. Aucun jeu réservé n'est utilisé : ils sont à usage unique et déjà mesurés.

### Modèles

- **Conversation** : `gemma4:e4b`, `ministral-3:8b`, `llama3.2:3b`, `gemma3:latest` et `llama3:latest` sur T1 à T5b ; `gemma4:26b` sur T1 seulement. Ce dernier pèse 18 Go, plus que la carte de 8 Go : sa mesure montre le prix d'un modèle qui déborde en mémoire vive, sans occuper le poste plusieurs heures par tâche.
- **Plongements** : `qwen3-embedding:0.6b` (modèle du labo), `bge-m3` et `nomic-embed-text` sur T0, avec le réglage `embed_num_ctx` du labo et sans préfixe de requête propre au modèle. Ce choix est publié comme limite.
- **Exclu** : `wizardcoder` (modèle de code).
- Ollama reste en version 0.24.0 ; aucun modèle n'est téléchargé. Un modèle ajouté plus tard entre par une ligne datée de l'[ADR 0024](0024-bench-evaluation-protocol.md), avant sa mesure.

### Mesures du matériel

Pour chaque exécution, la commande lit `/api/ps` d'Ollama sur le poste, à la fin de l'exécution, et note :

- `vram_gb` : mémoire du modèle sur la carte graphique (`size_vram`) ;
- `ram_spill_gb` : partie du modèle hors de la carte (`size` − `size_vram`) ;
- `context_length` : contexte chargé, pour vérifier que le réglage du labo (8192) est appliqué ;
- `tokens_per_s` : jetons générés divisés par le temps de génération, à partir des statistiques des appels ;
- latences p50 et p95 par item, comme dans les rapports existants.

Les paliers de matériel (8, 12, 16 et 24 Go de mémoire graphique) sont **estimés** : un modèle « tient » dans un palier quand `vram_gb + ram_spill_gb` mesuré, plus 1 Go de marge, ne dépasse pas ce palier. Chaque palier reçoit une fourchette de prix indicative au Canada, avec sa source et sa date ; aucun palier autre que 8 Go n'est mesuré.

### Index des plongements

Chaque modèle de plongements construit son index dans `<dossier du rapport>/index/<modèle>/`, jamais dans `.cache/`. L'index du labo reste celui de `qwen3-embedding:0.6b`.

### Commande

`alverno matrix --tasks T1,T2,... --models ... --repeats k --out reports/matrix-<date>` écrit un rapport par paire, une matrice `matrix.json`, `matrix.md` (français) et `matrix.en.md`. Le README reçoit un bloc `RESULTS-MATRIX` seulement si aucune exécution n'a d'erreur et si le balayage des témoins est vide, comme les autres bancs.

## Solutions écartées

- **Mettre à jour Ollama pour mesurer de nouveaux modèles** : changement du système du poste ; refusé pour ce module, noté comme mesure future.
- **Un nouveau jeu commun à toutes les tâches** : il faudrait de nouvelles bornes, alors que chaque tâche a déjà une porte préenregistrée.
- **Mesurer chaque paire à k = 3** : environ trois fois le temps du poste pour des paires qui échouent dès la première exécution ; k = 3 est gardé pour confirmer le modèle retenu.
- **Mesurer `gemma4:26b` sur toutes les tâches** : plusieurs heures par tâche sur ce poste, pour un modèle qu'aucune PME à 8 Go n'exploiterait.

## Conséquences

- Positives : une comparaison sur des portes déjà publiées, et une estimation du matériel par tâche.
- Négatives : les jeux ont servi à régler les modules et avantagent `gemma4:e4b` ; les paliers autres que 8 Go sont estimés ; les plongements sans préfixe peuvent sous-estimer `nomic-embed-text`.
