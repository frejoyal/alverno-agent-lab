# Analyse de l'art. 17 — Agent Qualité dans Teams (module 6)

> **Avertissement : ceci n'est pas un avis juridique.** Analyse rédigée pour un laboratoire, au sujet d'une entreprise fictive. Elle s'appuie sur la clé `lprp_communication_hors_quebec` du [registre RH](../hr-legal-sources.md) et sur des pages publiques de Microsoft lues le 2026-11-11, qui peuvent changer. Elle complète l'[EFVP du module 6](../efvp/m365-module.md).

## Ce que dit l'article (paraphrase du registre)

Avant de communiquer un RP à l'extérieur du Québec, l'entreprise réalise une EFVP qui tient compte, notamment, de la sensibilité du renseignement, de la fin de son utilisation, des mesures de protection (y compris contractuelles) et du régime juridique de l'État où il serait communiqué. La communication se fait seulement si l'évaluation montre que le renseignement bénéficierait d'une protection adéquate, et elle fait l'objet d'une entente écrite. La même règle vaut quand l'entreprise confie à quelqu'un hors du Québec la tâche de recueillir, d'utiliser, de communiquer ou de conserver un RP pour elle (clé `lprp_communication_hors_quebec`).

## Flux de données de l'agent

| # | Donnée | De | Vers | Lieu | Source |
|---|---|---|---|---|---|
| F1 | Question et réponse | employé | Teams, journal d'activité de l'agent | Microsoft 365 ; géographie Canada pour l'environnement ; journal d'activité dans la région de la boîte aux lettres de l'employé ; le canal Teams **peut** transférer hors de la géographie choisie | [Emplacement des données de Copilot Studio](https://learn.microsoft.com/en-us/microsoft-copilot-studio/data-location) |
| F2 | Question, jeton | Copilot Studio | passerelle de données locale, puis API sur le poste | transit par le service infonuagique de la passerelle, puis poste de l'usine | [Connecteurs personnalisés](https://learn.microsoft.com/en-us/connectors/custom-connectors/) |
| F3 | Réponse et citations | API | Copilot Studio, Teams | même chemin en sens inverse | idem |
| F4 | Identité de l'employé | Entra ID | Copilot Studio, API (jeton) | Entra ID peut stocker des données à l'échelle mondiale | page sur l'emplacement des données |
| F5 | Invites et réponses vers Azure OpenAI | — | — | **aucun flux** : fonctions génératives et transfert entre régions désactivés ; pour un environnement au Canada, Azure OpenAI est hébergé aux États-Unis | [Transfert entre régions](https://learn.microsoft.com/en-us/power-platform/admin/geographical-availability-copilot) (page du 2026-10-14) |

Les documents, l'index et le modèle de langue restent sur le poste : la passerelle ne transporte que la question et la réponse.

## Analyse par facteur

| Facteur | Lecture du labo |
|---|---|
| Sensibilité | Niveau 2 par conception ; un RP peut apparaître par accident dans une question (nom d'un collègue, numéro d'employé). L'API refuse alors de répondre, mais la question est déjà dans Teams. L'identifiant de l'employé (F4) est déjà traité par Microsoft pour l'usage de Teams. |
| Fin | Répondre à une question sur la documentation Qualité ; aucune autre fin. |
| Mesures de protection | Chiffrement en transit et au repos (engagements de Microsoft) ; jeton et rôle ; aucune fonction générative ; journal de l'API sans contenu ; rétention de Teams limitée par la gouvernance. Les mesures contractuelles sont celles du contrat de services de Microsoft et de son addenda sur la protection des données, **à vérifier par le RPRP**. |
| Régime juridique | Stockage principal au Canada (hors Québec : Toronto, ou Québec pour la région Canada Est) ; exceptions mondiales (Entra ID, nom de l'application). Le régime des États-Unis n'est sollicité que par les exceptions et par un éventuel transfert du canal Teams. |

## Conclusion du labo

- **Dans le labo** : aucune donnée réelle, aucun locataire ; l'article n'est pas déclenché.
- **Pour une mise en service** : la communication à Microsoft (prestataire au sens de l'art. 18.3, clé `lprp_mandataire_prestataire`) de la question, de la réponse et de l'identité de l'employé, avec un stockage en partie hors du Québec, **déclenche** l'art. 17. Le labo juge la protection **probablement adéquate** si, avant la mise en service : (1) le RPRP vérifie l'entente écrite avec Microsoft et les engagements de résidence au Canada ; (2) les fonctions génératives et le transfert entre régions restent désactivés ; (3) la rétention de Teams et du journal d'activité est fixée ; (4) l'avis aux employés est affiché. Cette lecture n'est pas un avis juridique.

## À vérifier avant la mise en service

- [ ] Version et date du contrat de services de Microsoft et de l'addenda sur la protection des données applicables au locataire.
- [ ] Géographie du locataire Microsoft 365 et de l'environnement Power Platform : Canada.
- [ ] Traitement du canal Teams pour Copilot Studio : lieu confirmé par Microsoft à la date de mise en service.
- [ ] « Déplacer les données entre les régions » : désactivé ; réponses et orchestration génératives : désactivées.
- [ ] Rétention de Teams et du journal d'activité : fixée et datée ([gouvernance](governance.md)).
