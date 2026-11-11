# ADR 0019 — Module 5c : images Docker et `docker compose` pour un serveur de l'usine, sans port publié ni registre

- **Statut** : accepté le 2026-11-10, à l'approbation de l'[EFVP du module 5](../efvp/direction-module.md#7-avis-du-responsable-décision-et-approbation) par la présidente (dans ce labo, le responsable du dépôt dans ce rôle)
- **Date** : 2026-11-10

## Contexte

La feuille de route prévoit un déploiement reproductible sur un serveur de l'usine : les agents et Ollama dans des conteneurs, lancés par `docker compose` ([feuille de route](../roadmap.md#module-5--orchestration-pour-la-direction-serveur-mcp-et-docker)). Trois contraintes :

- **Le poste du labo n'a pas Docker.** Au 2026-11-10, Docker n'y est pas installé et WSL y est désactivé. L'installer est un changement du système qui revient au responsable du dépôt ; ce module ne l'exige pas.
- **Les journaux des accès sont de niveau 4.** Dans un conteneur, ils vivent dans un volume ; leur conservation (90 jours au plus) et leurs lecteurs doivent rester ceux des [ADR 0008](0008-minimal-access-log.md) et [0014](0014-ops-access-log.md).
- **Local d'abord.** Un conteneur qui peut joindre Internet pourrait envoyer des données hors du poste ; Ollama, lui, doit pouvoir télécharger ses modèles une première fois ([ADR 0001](0001-local-first.md)).

Cet ADR est rédigé en tenant compte de la *Loi sur la protection des renseignements personnels dans le secteur privé* (RLRQ, c. P-39.1), état du droit au 1er novembre 2026 (texte consolidé à jour au 15 octobre 2026, date revue le 10 novembre 2026) ; **ceci n'est pas un avis juridique**.

## Décision

### Une image pour les agents

- `Dockerfile` à la racine, en deux étapes : construction avec `uv sync --locked --no-dev` (aucun extra, donc aucun modèle de Hugging Face), puis exécution sur une image Python 3.13 minimale, **épinglée par empreinte** (`@sha256:…`).
- L'image contient le code, les corpus fictifs et les jeux de référence. Elle ne contient **aucun jeu réservé** : `.dockerignore` exclut `data/eval/*holdout*.jsonl`, `reports/`, `logs/`, `.cache/`, `.env` et `.git/`. Un test vérifie la liste.
- Utilisateur non privilégié (`alverno`, UID fixe), système de fichiers en lecture seule sauf les volumes, aucune instruction `EXPOSE`. Point d'entrée : `alverno`.

### `docker compose` : deux services, aucun port publié

| Service | Image | Réseaux | Volumes |
|---|---|---|---|
| `ollama` | `ollama/ollama:0.24.0`, épinglée par empreinte | `interne` et `sortie` | `ollama-models` |
| `alverno` | l'image ci-dessus, construite localement | `interne` seulement | `alverno-index`, `alverno-logs` |

- Le réseau `interne` est déclaré `internal: true` : **le conteneur des agents n'a aucune sortie vers Internet**. Il joint Ollama par `http://ollama:11434` (`ALVERNO_OLLAMA_URL`).
- Le réseau `sortie` sert seulement à Ollama, pour `ollama pull` des modèles nommés dans `config.py`, lancé à la main par l'exploitant. Un fichier de surcharge permet de le retirer une fois les modèles téléchargés.
- **Aucune section `ports`** : ni Ollama ni les agents ne sont joignables depuis l'hôte ou le réseau de l'usine. On utilise les agents par `docker compose run --rm -i alverno <commande>` ; le serveur MCP de l'[ADR 0017](0017-mcp-server-read-only.md) s'utilise de la même façon, en stdio.
- Processeur graphique : réservation `nvidia` dans `deploy.resources`, qui exige le NVIDIA Container Toolkit sur l'hôte. Sans lui, Ollama roule sur le processeur central, plus lentement ; la documentation le dit.
- `OLLAMA_CONTEXT_LENGTH` n'est pas fixé dans `compose` : les clients du labo fixent `num_ctx` à chaque appel ([ADR 0005](0005-bound-embedding-context.md)).

### Journaux et volumes

- `alverno-logs` contient les journaux des accès des modules 2, 3 et 5. La purge a lieu à chaque écriture du journal et par `alverno access purge`, comme sur le poste. Les lecteurs restent le RPRP et le soutien informatique sous son autorité ; sur un serveur, l'accès au volume suit les droits de l'hôte, ce que l'[EFVP du module 5](../efvp/direction-module.md) inscrit comme mesure à vérifier avant la mise en service.
- `alverno-index` contient l'index vectoriel ; il se construit par `docker compose run --rm alverno index` après le téléchargement des modèles.
- Aucun volume ne contient de jeu réservé ni de rapport d'évaluation.

### Vérification en CI seulement, aucune publication

- Un nouveau travail de la CI construit l'image, puis exécute **dans le conteneur, sans réseau** (`--network none`) les portes `--fake` des modules 1 et 5 et `alverno privacy eval`.
- Des contrôles structurels font échouer le travail si : l'utilisateur de l'image est `root` ; l'image déclare un port ; `docker compose config` publie un port ou relie `alverno` à un réseau non interne ; un fichier exclu par `.dockerignore` se trouve dans l'image.
- **L'image n'est poussée vers aucun registre** (ni Docker Hub, ni le registre de GitHub). La publier serait une décision du responsable du dépôt, avec un nouvel ADR.
- La CI n'exécute pas Ollama (aucun processeur graphique, aucun modèle) ; `docker compose up` avec Ollama est documenté, pas testé en CI.

## Solutions écartées

- **Publier le port d'Ollama ou d'un serveur MCP HTTP** : n'importe quel poste de l'usine pourrait appeler les modèles ou les outils, sans authentification (module 6).
- **Donner Internet au conteneur des agents** : rien dans les agents n'en a besoin ; une sortie ouverte serait un chemin de fuite.
- **Une seule image avec Ollama et les agents** : deux processus à superviser dans un conteneur, et la mise à jour d'Ollama forcerait la reconstruction des agents.
- **Pousser l'image vers un registre public** : la publication d'un artéfact exécutable est une décision distincte ; rien ne l'exige pour un déploiement local.
- **Installer Docker sur le poste du labo dans ce module** : changement du système hors du périmètre ; la CI suffit à vérifier l'image.

## Conséquences

Positives :

- Un serveur de l'usine peut exécuter les agents et Ollama avec une seule commande, sans port ouvert et sans sortie Internet pour les agents.
- L'image est vérifiée à chaque PR : construction, portes `--fake`, contrôles structurels.

Négatives :

- Le chemin Ollama + GPU dans `compose` n'est pas testé automatiquement ; une erreur n'apparaîtrait qu'au premier déploiement réel.
- Le travail Docker allonge la CI de plusieurs minutes.
- Les épinglages par empreinte doivent être mis à jour à la main, avec la note de version.

Voir aussi l'[ADR 0017](0017-mcp-server-read-only.md), l'[ADR 0018](0018-direction-orchestrator.md) et l'[ADR 0020](0020-direction-evaluation-protocol.md).
