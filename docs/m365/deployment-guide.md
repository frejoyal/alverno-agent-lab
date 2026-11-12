# Guide de déploiement dans un locataire réel (module 6)

Ce dépôt **ne déploie rien** : le responsable du dépôt a choisi le volet dépôt seulement. Ce guide décrit ce qu'un soutien informatique ferait dans un vrai locataire, une fois l'[EFVP du module 6](../efvp/m365-module.md) approuvée et la liste de vérification de l'[analyse de l'art. 17](article-17.md) remplie. Les noms de menus suivent la documentation de Microsoft lue le 2026-11-11 ; ils peuvent changer.

## 1. Microsoft Entra ID

1. Inscrire l'application **« Alverno Qualité API »** : un identifiant d'application (`ALVERNO_M365_AUDIENCE`), un rôle d'application `Quality.Ask` attribuable aux utilisateurs et aux groupes.
2. Attribuer le rôle au groupe « Assistant Qualité — utilisateurs ».
3. Inscrire l'application **« Alverno Qualité synchronisation »** avec la permission d'application `Sites.Selected` ; un administrateur accorde ensuite la lecture sur le seul site Qualité.

## 2. Poste de l'usine

1. Ollama et le modèle retenu par la recommandation du module 4.
2. `alverno m365 sync`, puis `alverno index`, selon un horaire (par exemple chaque nuit).
3. `alverno m365 serve` comme service Windows, sur `127.0.0.1`, avec `ALVERNO_M365_TENANT_ID`, `ALVERNO_M365_AUDIENCE` et le fichier des clés publiques du locataire (`ALVERNO_M365_JWKS_PATH`, mis à jour selon un horaire).
4. Installer la **passerelle de données locale** de Microsoft sur ce poste, avec un compte de service ; aucun port entrant à ouvrir.

## 3. Power Platform et Copilot Studio

1. Créer un environnement dans la géographie **Canada**.
2. Dans « Fonctionnalités d'IA générative » de l'environnement : **ne pas cocher** « Déplacer les données entre les régions ».
3. Importer le connecteur personnalisé à partir de `integrations/copilot-studio/connector/` ; choisir la passerelle de données locale ; hôte `127.0.0.1`.
4. Créer une stratégie de protection des données : le connecteur seul dans le groupe « Professionnel », les autres connecteurs bloqués pour l'environnement.
5. Créer l'agent à partir de `integrations/copilot-studio/agent/` ; vérifier que les réponses génératives, l'orchestration générative et les sources de connaissances sont **désactivées**.
6. Publier dans Teams, réservé au groupe « Assistant Qualité — utilisateurs ».

## 4. Microsoft Purview

- Stratégie de rétention de 90 jours pour les conversations avec l'agent ([gouvernance](governance.md)).
- Étiquettes de confidentialité de la bibliothèque : « Général » et « Restreint — client ».

## 5. Liste de vérification avant la mise en service

- [ ] EFVP approuvée ; liste de l'[analyse de l'art. 17](article-17.md) remplie et datée.
- [ ] Un jeton sans le rôle `Quality.Ask` est refusé (403) ; un jeton d'un autre locataire est refusé (401).
- [ ] Une question qui contient un nom de collègue est refusée par l'agent.
- [ ] « Déplacer les données entre les régions » est décoché ; aucune fonction générative n'est active.
- [ ] Le journal des accès de l'API ne contient ni question ni réponse.
- [ ] Rétention de Teams et du journal d'activité fixée.
- [ ] Avis aux employés affiché au premier message ; formation du [plan d'adoption](adoption-plan.md) donnée au pilote.
