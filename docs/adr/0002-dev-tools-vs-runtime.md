# ADR 0002 — Outils de développement distincts du runtime

- **Statut** : accepté
- **Date** : 2026-10-31

## Contexte

Le projet est développé avec l'aide d'assistants de code par abonnement : Claude Code, Codex et Antigravity. Ces abonnements donnent accès à des modèles puissants, et il serait tentant de les utiliser aussi comme moteur d'inférence de l'agent ou comme juge dans la CI.

Or :

- les conditions d'utilisation de ces abonnements visent l'usage interactif par la personne abonnée, pas l'alimentation d'une application publique ou d'un pipeline automatisé ;
- un jeton OAuth personnel placé dans la CI d'un dépôt public pourrait être exposé (journaux, *forks*, demandes de fusion) et donnerait accès au compte de la personne ;
- un tiers qui clone le dépôt n'a pas ces abonnements : les résultats ne seraient pas reproductibles ;
- le contenu du corpus serait envoyé à un service hébergé, contrairement à l'ADR 0001.

## Décision

1. Les abonnements Claude Code, Codex et Antigravity servent **uniquement au développement** : écrire et réviser le code, la documentation et les tests sur le poste du développeur.
2. Le **runtime public** (CLI, agent, évaluations, banc d'essai) tourne sur **Ollama** en local, avec des modèles à poids ouverts.
3. **Jamais de jeton OAuth** ni d'identifiant d'abonnement dans la CI publique ni dans le dépôt. Un fichier `.env` local, ignoré par git, peut redéfinir les réglages de `Settings` (préfixe `ALVERNO_`) ; aucun réglage n'exige de secret.
4. La CI n'utilise aucun modèle : faux embedder déterministe et faux LLM (`HashEmbedder`, `FakeChat`).
5. Les instructions destinées aux assistants de code se trouvent dans `AGENTS.md` ; `CLAUDE.md` y renvoie, pour qu'un seul fichier fasse foi quel que soit l'outil.

## Conséquences

- Résultats reproductibles par n'importe qui avec Ollama.
- Respect des conditions d'utilisation des outils de développement.
- Surface d'attaque réduite : aucun secret à protéger en CI.
- En contrepartie, on renonce à un « juge LLM » puissant dans la CI. Les métriques retenues (hit@k, MRR, validité des citations, rappel de faits clés, refus sur pièges) sont déterministes ou vérifiables sans juge. Un modèle de décision externe pourra être évalué plus tard, en option et hors CI (module 4 de la feuille de route).
