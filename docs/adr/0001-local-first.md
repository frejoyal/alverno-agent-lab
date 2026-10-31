# ADR 0001 — Exécution local-first avec Ollama

- **Statut** : accepté
- **Date** : 2026-10-31

## Contexte

Le labo simule une PME manufacturière québécoise (fictive) dont la documentation contient du savoir-faire interne, des spécifications clients couvertes par des ententes de confidentialité et, pour les modules futurs, des renseignements personnels. Le dépôt est public : toute personne qui le clone doit pouvoir reproduire les résultats sans compte, sans clé et sans frais. La Loi 25 encadre la communication de renseignements personnels hors Québec (art. 17).

## Décision

- Toute l'inférence (clavardage et embeddings) passe par **Ollama** sur `http://localhost:11434` par défaut. `Settings` refuse toute adresse autre que `localhost`, `127.0.0.1` ou `::1`, sauf si `ALVERNO_ALLOW_REMOTE_OLLAMA=true` est posé explicitement ; un avertissement est alors affiché sur la sortie d'erreur. Les modèles infonuagiques d'Ollama (`:cloud`, `-cloud`), servis par l'adresse locale mais exécutés hors du poste, exigent la même option.
- Modèles par défaut : `gemma4:e4b` pour le clavardage (le meilleur des modèles mesurés, voir les résultats du README), `qwen3-embedding:0.6b` pour les embeddings ; `ministral-3:8b` et `llama3.2:3b` sont aussi mesurés au banc d'essai, et `qwen3.5:9b` reste un candidat (il exige une version d'Ollama plus récente que 0.24).
- Le code dépend de protocoles (`ChatModel`, `Embedder`, `Reranker`) ; un autre fournisseur pourrait être ajouté, mais jamais sur le chemin par défaut.
- Aucune clé d'API n'est requise pour installer, tester, évaluer ou utiliser le projet.
- Toute option qui enverrait du contenu hors du poste doit être désactivée par défaut, faire l'objet d'un ADR et être documentée dans `docs/loi25.md`.

## Conséquences

Positives :

- Aucune communication de données hors du poste ; base saine pour les modules qui traiteront des renseignements personnels.
- Reproductible par un tiers ; coût marginal nul.
- Répond directement à la réticence des PME à envoyer leurs données dans des services hébergés.

Négatives :

- Qualité et vitesse limitées par le matériel local ; les modèles de 3 à 9 milliards de paramètres (de `llama3.2:3b` à `qwen3.5:9b`) sont moins performants que les grands modèles hébergés. D'où l'importance des évaluations et de la barrière de refus.
- Premier démarrage plus long (téléchargement des modèles).
- La CI ne peut pas exécuter les vrais modèles : elle utilise des substituts déterministes (voir ADR 0002 et `docs/architecture.md`).
