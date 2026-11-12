# ADR 0027 — Agent Copilot Studio sans IA générative hébergée, publié dans Teams (module 6c)

- **Statut** : accepté le 2026-11-11, avec l'approbation de la version 1.0 de l'[EFVP du module 6](../efvp/m365-module.md)
- **Date** : 2026-11-11

## Contexte

Les employés d'Alverno travaillent dans Teams. Copilot Studio permet de publier un agent dans Teams et d'appeler une API par un connecteur personnalisé. Ses fonctions d'IA générative (réponses génératives, orchestration générative) utilisent toutefois le service Azure OpenAI ; pour un environnement Power Platform situé au Canada, Microsoft indique que ce service est hébergé **aux États-Unis**, et que les invites et les réponses peuvent y être envoyées si le transfert entre régions est permis ([page Microsoft Learn du 2026-10-14](https://learn.microsoft.com/en-us/power-platform/admin/geographical-availability-copilot), lue le 2026-11-11). Les données d'un environnement canadien sont stockées dans les régions Canada Central et Canada Est, avec des exceptions publiées (Microsoft Entra ID, nom et description de l'application, journaux d'activité dans la région de la boîte aux lettres de l'utilisateur) ([page Microsoft Learn sur l'emplacement des données](https://learn.microsoft.com/en-us/microsoft-copilot-studio/data-location), lue le 2026-11-11).

Le responsable du dépôt a choisi le volet dépôt seulement : l'agent est décrit par des fichiers versionnés et vérifiés par des tests, pas déployé.

L'analyse de l'art. 17 est dans l'[EFVP du module 6](../efvp/m365-module.md) ; **ceci n'est pas un avis juridique**.

## Décision

### L'agent

`integrations/copilot-studio/` contient :

- `connector/apiDefinition.swagger.json` : la définition OpenAPI 2.0 du connecteur personnalisé (format exigé par Power Platform), générée à partir des routes de l'API ([ADR 0026](0026-local-api-and-auth.md)) et vérifiée par un test ;
- `connector/apiProperties.json` : authentification OAuth 2.0 par Microsoft Entra ID, passage par la passerelle de données locale ;
- `agent/agent.yaml` : l'agent « Assistant Qualité Alverno », langue française, **réponses génératives et orchestration générative désactivées**, aucune source de connaissances hébergée ;
- `agent/topics/*.yaml` : sujets classiques (accueil, question Qualité, refus, fin), dont un seul appelle l'action du connecteur ;
- `environment.md` : les réglages de l'environnement Power Platform à appliquer, dont « Déplacer les données entre les régions » **désactivé** et le connecteur placé seul dans le groupe « Professionnel » d'une stratégie de protection des données.

### Ce qui reste local et ce qui sort

- **Reste sur le poste** : l'index, les documents, le modèle de langue, la réponse et sa vérification.
- **Transite et est conservé chez Microsoft** : le texte de la question et de la réponse, dans la conversation Teams et le journal d'activité de l'agent. L'environnement est créé dans la géographie Canada ; le journal d'activité suit la région de la boîte aux lettres de l'utilisateur. La même page de Microsoft range toutefois le canal Teams parmi les services externes qui **peuvent** transférer des données hors de la géographie choisie : le lieu exact de traitement n'est pas établi par ce dépôt et reste un risque à vérifier avant toute mise en service (EFVP, risque R2).
- **N'est pas envoyé au service Azure OpenAI** : aucune fonction générative n'est activée, et le transfert entre régions est désactivé.

### Garde-fous côté agent

Le sujet « Question Qualité » affiche, au premier message de la conversation, l'avis : l'agent répond à partir de la documentation Qualité seulement ; aucun renseignement personnel ne doit être écrit ; la question et la réponse restent dans l'historique de Teams. L'API refuse déjà toute question qui contient un renseignement personnel ([ADR 0006](0006-deterministic-privacy-layer.md)) ; ce refus est affiché tel quel.

## Solutions écartées

- **Réponses génératives de Copilot Studio sur la bibliothèque SharePoint** : simple à configurer, mais l'inférence sur le contenu se ferait aux États-Unis pour un environnement canadien, et la qualité ne serait pas mesurée par les portes du labo. Comparée sur papier dans [comparison.md](../m365/comparison.md).
- **Application Teams développée sur mesure (Bot Framework)** : plus de code à maintenir pour le même chemin de données.
- **Microsoft 365 Copilot (licence par utilisateur)** : comparé dans [comparison.md](../m365/comparison.md) ; hors de la réalisation.

## Conséquences

- Positives : l'agent rejoint les employés dans Teams avec l'inférence et la recherche locales ; le chemin des données est connu et limité.
- Négatives : les questions et les réponses sont conservées dans Microsoft 365 (au Canada) selon la rétention de Teams et du journal d'activité ; rien de ce dépôt ne peut le vérifier sans locataire réel.
