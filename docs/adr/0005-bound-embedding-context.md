# ADR 0005 — Borner le contexte du modèle de plongements

- **Statut** : accepté
- **Date** : 2026-10-31

## Contexte

Le banc d'essai du 2026-10-31 (`reports/bench-ollama-20261031-111151/`) donnait à `gemma4:e4b` une latence médiane de 30,1 s par question. Le journal du serveur Ollama a montré que ce temps venait surtout de rechargements de modèles, pas du calcul.

- `OllamaEmbedder` n'envoyait aucun `num_ctx`. Ollama appliquait donc le contexte par défaut du serveur (`OLLAMA_CONTEXT_LENGTH`, réglé à 131 072 jetons sur le poste de mesure), ramené au maximum du modèle `qwen3-embedding:0.6b` (32 768 jetons). Le modèle de plongements occupait alors 5,8 Gio, presque tout en mémoire vidéo.
- Sur une carte de 8 Go, il ne tenait pas en même temps que le modèle de clavardage : Ollama déchargeait l'un pour charger l'autre à chaque question (234 déchargements et 271 démarrages de modèle pendant le banc). Chaque chargement de `gemma4:e4b` prenait une vingtaine de secondes.

Le contexte utile est pourtant petit : un extrait fait au plus 1 200 caractères, et une question encore moins.

## Décision

- Nouveau réglage `embed_num_ctx` (2 048 jetons par défaut), envoyé dans `options.num_ctx` de chaque requête `POST /api/embed`. Le modèle de clavardage garde son propre `num_ctx` (8 192 jetons).
- Un test de régression vérifie que chaque requête d'embeddings porte ce contexte.

## Mesures

Avec 2 048 jetons, le modèle de plongements occupe 1,1 Gio. Les vecteurs de l'index sont identiques au bit près avec les deux contextes, et la recherche donne les mêmes résultats sur les 50 questions : la barrière calibrée par l'ADR 0004 reste valable.

| Modèle | p50 avant | p50 après | Rechargements pendant le banc, après |
|---|---|---|---|
| `gemma4:e4b` | 30,1 s | 6,1 s | aucun |
| `ministral-3:8b` | 11,2 s | 11,3 s | à chaque question |
| `llama3.2:3b` | 13,0 s | 3,1 s | aucun |

Avant : `reports/bench-ollama-20261031-111151/`. Après : `reports/bench-ollama-20261031-125148/`.

## Conséquences

- La latence publiée mesure maintenant le calcul pour `gemma4:e4b` et `llama3.2:3b`. Un refus prononcé avant le LLM (question RH visant une personne) passe de 4,5 s à 0,13 s.
- `ministral-3:8b` (7,8 Gio avec 8 192 jetons de contexte) et le modèle de plongements ne tiennent toujours pas ensemble sur cette carte : Ollama les recharge à tour de rôle à chaque question. Pistes non retenues pour l'instant : réduire le `num_ctx` du modèle de clavardage ou exécuter les plongements sur le processeur.
- À température 0, les sorties varient légèrement d'un banc à l'autre quand le placement des modèles en mémoire change : les faits clés de `gemma4:e4b` passent de 0,962 à 0,948, et `llama3.2:3b` refuse 4 vraies questions au lieu de 3. Mesurer cette variance sur plusieurs exécutions reste à faire.
- Un serveur réglé avec un grand `OLLAMA_CONTEXT_LENGTH` ne change plus l'empreinte mémoire du labo : les deux contextes sont fixés dans `Settings`.
