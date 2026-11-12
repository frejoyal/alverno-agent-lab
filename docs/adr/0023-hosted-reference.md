# ADR 0023 — Référence hébergée facultative par l'interface Claude Code (module 4b)

- **Statut** : accepté le 2026-11-11, avec l'approbation de la version 1.0 de l'[EFVP du module 4](../efvp/bench-module.md)
- **Date** : 2026-11-11

## Contexte

La feuille de route prévoit un modèle hébergé facultatif pour situer les modèles locaux : est-ce qu'un modèle infonuagique répond mieux à la même tâche, avec la même recherche et les mêmes règles ? Le labo n'utilise aucune clé d'API payante. Le responsable du dépôt dispose de l'interface en ligne de commande Claude Code (modèle Haiku) et de Codex ; il a proposé, le 2026-11-11, d'utiliser l'une d'elles.

[loi25.md](../loi25.md) garde les données de niveau 2 locales par défaut et refuse tout renseignement personnel (RP) à un modèle externe. L'analyse est dans l'[EFVP du module 4](../efvp/bench-module.md) ; **ceci n'est pas un avis juridique**.

## Décision

### Fournisseur et appel

- Interface : `claude -p` (Claude Code en mode non interactif), modèle Haiku, par l'abonnement existant du responsable du dépôt ; aucune clé dans le dépôt ni dans la CI.
- Appel sans outil : l'invite système et l'invite de l'agent Qualité sont passées telles quelles ; aucun outil, aucun fichier, aucun accès au dépôt n'est offert au modèle. La ligne de commande est construite par le code et vérifiée par un test.
- La version de l'interface (`claude --version`) et l'identifiant du modèle sont écrits dans le rapport.
- Codex en ligne de commande est noté comme autre fournisseur possible ; il n'est pas utilisé dans cette version.

### Données envoyées

Seul l'agent Qualité (module 1) est utilisé. Le code retient un item du jeu `quality_gold` seulement si :

1. il est répondable (pas un piège) ;
2. aucune de ses sources attendues ni aucun des extraits récupérés n'appartient à une spécification de client (`SPEC-*`), souvent couverte par une entente de confidentialité ;
3. le détecteur de RP du labo ([ADR 0006](0006-deterministic-privacy-layer.md)) ne trouve rien dans l'invite complète, juste avant l'envoi.

Un item qui échoue à 3 n'est pas envoyé et compte dans `hosted_pi_blocked`. La recherche, la barrière « aucune source » et la vérification des citations restent locales : seul l'appel au modèle de conversation change.

### Activation

- Désactivée par défaut : `alverno matrix --reference claude-haiku` exige `ALVERNO_ALLOW_HOSTED_REFERENCE=true`, sinon la commande s'arrête avec un message et n'appelle rien.
- Jamais dans la CI : le flux de travail ne pose pas l'option et n'installe pas l'interface ; un test vérifie qu'aucun appel n'a lieu sans l'option.

### Comptes de sécurité

| Compte | Définition | Borne |
|---|---|---|
| `hosted_pi_sent` | invites envoyées dans lesquelles le détecteur trouve un RP | 0 |
| `hosted_trap_sent` | pièges envoyés | 0 |
| `hosted_spec_sent` | invites envoyées qui contiennent un extrait `SPEC-*` | 0 |
| `hosted_calls_default` | appels faits sans l'option | 0 |

Chaque compte a un contrôle négatif qui retire la barrière correspondante et doit le faire passer au-dessus de 0.

### Usage du résultat

Le résultat est **informatif** : il apparaît dans la matrice comme une ligne de référence, avec les métriques de T1 (faits clés, citations, refus), mais n'entre pas dans la règle de recommandation de l'[ADR 0024](0024-bench-evaluation-protocol.md), qui ne retient que des modèles locaux.

## Solutions écartées

- **Clé d'API payante** : contraire à la règle du labo ; l'abonnement existant suffit pour un petit sous-ensemble.
- **Juge hébergé qui note les réponses locales** : ajouterait une dépendance hébergée aux métriques ; les métriques déterministes restent la mesure.
- **Référence sur les tâches RH, service, entretien ou direction** : leurs jeux contiennent des RP synthétiques ou des données de niveau 4.

## Conséquences

- Positives : une comparaison honnête entre local et hébergé, sur la même recherche et les mêmes règles.
- Négatives : exception assumée à « local par défaut » pour des documents internes de niveau 2 ; dépendance à l'évolution de l'interface du fournisseur (risques R3 et R6 de l'EFVP).
