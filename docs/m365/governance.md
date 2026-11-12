# Gouvernance de l'agent Qualité dans Microsoft 365 (module 6)

> **Avertissement : ceci n'est pas un avis juridique.** Gouvernance proposée pour une entreprise fictive ; elle complète l'[EFVP du module 6](../efvp/m365-module.md) et la politique de gouvernance (clé `lprp_gouvernance`).

## Propriétaires

| Élément | Propriétaire (rôle) | Suppléant |
|---|---|---|
| Bibliothèque « Documentation qualité » et son contenu | Responsable de la qualité | Technicien qualité |
| Agent « Assistant Qualité Alverno » dans Copilot Studio | Responsable de la qualité | Soutien informatique |
| Environnement Power Platform, connecteur, passerelle | Soutien informatique | — |
| Poste qui exécute l'API et le modèle | Soutien informatique | — |
| Journal des accès de l'API, demandes d'accès | RPRP | Soutien informatique |

## Accès

| Groupe Entra ID | Membres | Droit |
|---|---|---|
| « Qualité — rédaction » | Équipe qualité | Modifier la bibliothèque |
| « Agent Qualité — lecture » (groupe de service) | Application de synchronisation seulement | Lire la bibliothèque (`Sites.Selected`) |
| « Assistant Qualité — utilisateurs » | Employés de la production et de la qualité | Rôle d'application `Quality.Ask` sur l'API ; accès à l'agent dans Teams |
| « Power Platform — administrateurs » | Soutien informatique | Administrer l'environnement et la stratégie de protection des données |

- L'accès se donne par groupe, jamais par personne.
- **Révision trimestrielle** des membres des groupes par le propriétaire, notée dans le registre de gouvernance.
- Départ d'un employé : retrait du compte Entra ID, donc du rôle, le jour du départ.

## Réglages qui ne changent pas sans nouvelle EFVP

- Réponses génératives, orchestration générative, sources de connaissances hébergées : **désactivées**.
- « Déplacer les données entre les régions » : **désactivé**.
- Connecteur personnalisé seul dans le groupe « Professionnel » de la stratégie de protection des données de l'environnement ; tous les autres connecteurs bloqués pour cet agent.
- API exposant l'agent Qualité seulement ; écoute sur `127.0.0.1`.

## Conservation

| Donnée | Durée proposée | Mécanisme |
|---|---|---|
| Conversations Teams avec l'agent | 90 jours | Stratégie de rétention Microsoft Purview pour les conversations avec l'agent |
| Journal d'activité de l'agent (Copilot Studio) | 90 jours | Réglage de l'environnement |
| Journal des accès de l'API | 90 jours | Purge automatique ([ADR 0008](../adr/0008-minimal-access-log.md)) |

Les durées sont celles du labo, à confirmer par le RPRP avant la mise en service.

## Incidents

Une question qui contient un RP n'est pas un incident en soi : l'API refuse d'y répondre. Une réponse servie à une personne sans le rôle, ou un document non autorisé dans la synchronisation, est un incident de confidentialité possible : le RPRP l'inscrit au registre et évalue le risque de préjudice (clés `lprp_incident_definition`, `lprp_incident_risque_prejudice`, `lprp_registre_incidents`).
