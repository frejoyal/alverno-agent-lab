# Feuille de route du laboratoire

> Entreprise et données entièrement fictives. Les échéances ne sont pas fixées.

Le labo progresse par modules livrables. Chaque module suit les mêmes règles : local-first, typage strict, tests sans réseau, jeu d'évaluation et porte de livraison en CI, documentation Loi 25.

**Ordre de réalisation** : modules 1 et 2 livrés ; ensuite le module 3, puis le module 5 avant le module 4, et le module 6 en dernier. Les numéros désignent les modules ; ils ne fixent pas l'ordre.

## Module 1 — Agent Qualité (livré)

**Problème** : accéder à la documentation qualité au poste de travail, sur les deux quarts, sans réponse inventée.

- Corpus d'environ 30 documents qualité fictifs, avec versions contradictoires volontaires.
- Recherche hybride BM25 + vecteurs, fusion RRF, reranker optionnel, barrière no-match.
- Graphe LangGraph `retrieve -> gate -> answer -> verify`, branche `refuse`.
- 50 questions de référence (35 répondables, 15 pièges), métriques de recherche, de refus, de citations et de latence.
- CLI `alverno index | ask | eval | bench`, porte d'évaluation en CI.

**Critère de sortie** : porte `full-ollama` réussie avec le modèle par défaut (`gemma4:e4b`), résultats du banc d'essai publiés dans le README.

**Statut** : critère atteint le 2026-10-31 (banc d'essai `reports/bench-ollama-20261031-125148/`, résultats dans le README).

## Module 2 — Couche Loi 25 et module RH (livré)

**Problème** : répondre aux questions RH courantes (politique de vacances, horaires, avantages) sans exposer de renseignements personnels.

- Couche transversale réutilisable (`privacy/`) : détection et caviardage de renseignements personnels, classification de la demande, politique de refus, garde de sortie, journal des accès minimal ([ADR 0006](adr/0006-deterministic-privacy-layer.md), [ADR 0008](adr/0008-minimal-access-log.md)).
- Assistant RH : enveloppe de confidentialité autour du graphe du module 1, inchangé ; 18 politiques RH fictives (niveau 2, rôles seulement) séparées du corpus Qualité ; dossiers nominatifs (niveaux 4 et 5) hors périmètre ([ADR 0007](adr/0007-hr-envelope-and-per-module-data.md)).
- EFVP rédigée avant l'implémentation, puis complétée des résultats mesurés : [efvp/hr-module.md](efvp/hr-module.md).
- Évaluation : 64 questions, dont 20 pièges de confidentialité, 16 paires et 13 questions avec témoins ; bornes de confidentialité préenregistrées ; jeu réservé mesuré une seule fois ([ADR 0010](adr/0010-hr-evaluation-protocol.md), [ADR 0011](adr/0011-hr-gate-calibration.md)).

**Statut** : livré le 2026-11-01. Banc d'essai `reports/bench-hr-ollama-20261101-132355/` : `gemma4:e4b` passe la porte `hr-full-ollama` (20 pièges de confidentialité refusés sur 20, aucune fuite sur 13 questions avec témoins, faits clés 0,806 pour un minimum de 0,80). Module 1 revérifié sur Ollama : portes `retrieval-ollama` et `full-ollama` réussies, aucune des 50 questions ne change de refus, de motif ni de citations (`reports/m1-check-ollama-20261101/`). Jeu réservé : 8 pièges de confidentialité refusés sur 10 (`reports/hr-holdout/`) ; l'écart est publié dans le README.

## Module 3 — Service client et maintenance (LangGraph) (livré)

**Problème** : suivre l'état d'une commande, préparer une réponse à un client, diagnostiquer un arrêt machine à partir de l'historique de la GMAO.

- Agents multi-étapes en LangGraph : outils typés, mémoire d'état, points d'approbation humaine avant toute action externe.
- Connecteurs simulés (ERP et GMAO fictifs) pour garder les tests hors réseau.
- Brouillons seulement : aucun envoi automatique au client.

**Statut** : EFVP v1.0 ([docs/efvp/ops-module.md](efvp/ops-module.md)), registre juridique ([docs/ops-legal-sources.md](ops-legal-sources.md)) et ADR [0012](adr/0012-ops-architecture-typed-tools.md) à [0015](adr/0015-ops-evaluation-protocol.md) rédigés le 2026-11-01 ; EFVP approuvée et ADR acceptés le 2026-11-01 ; données fictives, jeux de référence, jeux réservés (empreintes épinglées) et profils préenregistrés versés le 2026-11-01 (étape I2) ; le 2026-11-02, connecteurs simulés, outils typés, couche de confidentialité, approbation humaine et sous-module 3a en mode outils et en mode complet simulé (étapes I3 à I7), portes `svc-tools-fake` et `svc-full-fake` franchies, bornes simulées calibrées une fois ([ADR 0016](adr/0016-ops-gate-calibration.md)). **Sous-module 3a livré le 2026-11-02 (étape I8)** : `gemma4:e4b` passe `svc-full-ollama` sur ses 3 répétitions (banc `reports/bench-svc-ollama-20261102-124609/`), jeu réservé 3a mesuré une seule fois et publié tel quel. Le 2026-11-02 (étape I9), sous-module 3b (entretien) en mode outils et en mode complet simulé : portes `maint-tools-fake` et `maint-full-fake` franchies, bornes simulées calibrées une fois (ADR 0016, partie 2). Le 2026-11-02 (étape I10) : `gemma4:e4b` passe `maint-full-ollama` sur ses 3 répétitions (banc `reports/bench-maint-ollama-20261102-212645/`) ; jeu réservé 3b mesuré une seule fois et publié tel quel (2 pièges sur 6 non refusés, aucun technicien nommé) ; EFVP v1.1 rédigée, à approuver. **Critère de sortie atteint sauf le point 4** : la non-régression des modules 1 et 2 passe ses portes sans changer aucun refus ni motif, mais les citations changent sur 2 ou 3 questions ([ADR 0016](adr/0016-ops-gate-calibration.md)). Le 2026-11-03, la cause est corrigée (cache d'invite d'Ollama : une requête fixe précède chaque appel, et les réponses deviennent reproductibles), mais la nouvelle non-régression garde 3 citations différentes des références produites avant la correction. Le responsable du dépôt a alors fixé de nouvelles références dans l'ADR 0015, avant toute mesure (code de clôture du module 2 avec la seule correction) ; comparé à elles, le code du module 3 donne des rapports identiques sur tous les champs. **Module 3 livré le 2026-11-03** : critère de sortie atteint ; EFVP v1.1 approuvée le 2026-11-03.

## Module 4 — Banc d'essai de modèles locaux par tâche

**Problème** : choisir le bon modèle pour chaque tâche selon la qualité, la latence et le matériel disponible dans une PME.

- Extension de `alverno bench` : matrice tâches x modèles (`gemma4:e4b`, `ministral-3:8b`, `llama3.2:3b`, `qwen3.5:9b` après mise à jour d'Ollama, autres modèles Ollama), coût matériel et latence.
- **Modèle de décision externe optionnel** : un modèle hébergé peut servir de référence ou d'arbitre pour comparer les résultats locaux. Désactivé par défaut, jamais requis en CI, utilisé seulement sur des données de niveau 1 ou 2 et documenté selon [loi25.md](loi25.md).
- Recommandation écrite : quel modèle pour quelle tâche, et à quel prix matériel.

## Module 5 — Orchestration pour la direction, serveur MCP et Docker

**Problème** : donner à la direction une vue consolidée (qualité, RH, clients, maintenance) et rendre les agents réutilisables par d'autres outils.

- Orchestrateur qui délègue aux agents spécialisés et agrège leurs réponses avec citations.
- Serveur MCP exposant les capacités des agents (recherche qualité, état de commande) à des clients compatibles.
- Images Docker et `docker compose` (agents + Ollama) pour un déploiement reproductible sur un serveur de l'usine.

**Statut** : en cours. Le 2026-11-10 (étape I0), EFVP v1.0 ([docs/efvp/direction-module.md](efvp/direction-module.md)) et ADR [0017](adr/0017-mcp-server-read-only.md) (serveur MCP local, en lecture seule, niveau 2 par défaut), [0018](adr/0018-direction-orchestrator.md) (orchestrateur à routage déterministe, réponse par sections), [0019](adr/0019-docker-local-deployment.md) (images Docker sans port publié ni registre) et [0020](adr/0020-direction-evaluation-protocol.md) (protocole d'évaluation et critère de sortie) rédigés avant tout code ; EFVP approuvée et ADR acceptés le 2026-11-10. Trois sous-modules, livrés dans l'ordre 5a (MCP), 5b (direction), 5c (Docker).

**État au 2026-11-11** : 5a et 5b livrés et mesurés (portes, banc d'essai sur Ollama, jeu réservé mesuré une seule fois, non-régression des modules 1 à 3 sans écart ; [état du critère de sortie](adr/0020-direction-evaluation-protocol.md#état-du-critère-de-sortie-2026-11-11-étape-i10)). 5c (Docker) est reporté par décision du responsable du dépôt ; une autre façon de déployer les agents sur un serveur sera cherchée ([ADR 0019](adr/0019-docker-local-deployment.md#report-de-létape-i8-2026-11-11)). EFVP v1.1 rédigée avec les résultats ; avis du RPRP et approbation en attente.

## Module 6 — Déploiement Microsoft 365 et Copilot Studio

**Problème** : rejoindre les employés là où ils travaillent déjà (Teams, SharePoint).

- Connexion au SharePoint de la documentation qualité comme source du corpus.
- Agent Copilot Studio ou application Teams qui appelle les agents du labo, avec authentification de l'organisation.
- Analyse des flux de données (lieu d'hébergement, art. 17), gouvernance des accès et plan d'adoption pour les employés.
- Comparaison documentée : agents locaux du labo contre capacités natives de Copilot Studio, selon le coût, la qualité mesurée et la conformité.
