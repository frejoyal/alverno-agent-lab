# ADR 0026 — API locale de l'agent Qualité, avec jeton de l'organisation (module 6b)

- **Statut** : proposé le 2026-11-11, en attente de l'approbation de l'[EFVP du module 6](../efvp/m365-module.md)
- **Date** : 2026-11-11

## Contexte

Un agent Copilot Studio ou une application Teams ne lance pas de commande sur le poste : il appelle une API par HTTPS. Les agents du labo n'exposent aujourd'hui que la ligne de commande et un serveur MCP en stdio ([ADR 0017](0017-mcp-server-read-only.md)). Il faut une API qui garde les règles du labo : inférence locale, aucun port ouvert vers Internet, accès réservé aux employés autorisés de l'organisation.

L'analyse est dans l'[EFVP du module 6](../efvp/m365-module.md) ; **ceci n'est pas un avis juridique**.

## Décision

### Portée

L'API n'expose **que l'agent Qualité** (documents de niveau 2) :

- `POST /v1/quality/ask` : une question en français ; la réponse, ses citations (identifiants et titres des documents), ou un refus avec son motif ;
- `GET /v1/health` : l'état du service, sans authentification et sans contenu.

Les assistants RH, service à la clientèle, entretien et direction **ne sont pas exposés** : leurs données sont de niveau 3 ou plus, et leur passage par un service hébergé exigerait une autre analyse. Aucune route ne permet d'écrire.

### Écoute et transport

`alverno m365 serve` écoute sur `127.0.0.1` seulement ; une autre adresse est refusée au démarrage. Le passage vers Microsoft 365 se fait par la **passerelle de données locale** de Microsoft, qui établit une connexion sortante : aucun port entrant n'est ouvert sur le poste ou le pare-feu ([guide de déploiement](../m365/deployment-guide.md)).

### Authentification et autorisation

Chaque appel à `/v1/quality/ask` porte un jeton d'accès Microsoft Entra ID (JWT signé RS256). L'API le refuse (401) s'il manque, si sa signature ne correspond à aucune clé publiée du locataire, s'il est expiré ou pas encore valide, ou si son émetteur, son audience ou son locataire (`tid`) ne sont pas ceux configurés. Elle le refuse (403) s'il ne porte pas le rôle d'application `Quality.Ask`. Les clés publiques sont lues depuis un fichier JWKS configuré ; dans le labo, ce sont des **clés de test** générées par les tests, jamais celles d'un locataire réel.

### Ce que l'API garde

Le journal des accès reçoit une ligne par appel, selon le schéma de l'[ADR 0008](0008-minimal-access-log.md) : date, identifiant opaque de l'utilisateur (`oid` du jeton, haché avec un sel du poste), décision, motif de refus, nombre de citations, latence. **Ni la question ni la réponse ne sont journalisées.** Rien d'autre n'est conservé.

### Dépendances

`starlette`, `uvicorn` et `pyjwt[crypto]` sont déjà installés par le SDK MCP ; ils deviennent des dépendances déclarées, aux versions du fichier de verrouillage.

## Solutions écartées

- **Exposer l'API sur Internet (Azure App Service, tunnel public)** : ouvrirait un point d'entrée et sortirait l'inférence du poste.
- **Clé d'API partagée** : ne dit pas qui pose la question et ne se révoque pas par employé.
- **Exposer tous les agents** : les données RH, clients et d'entretien ne doivent pas transiter par un service hébergé dans cette version.

## Conséquences

- Positives : la même règle d'accès que le reste de Microsoft 365 (groupes et rôles Entra ID) ; aucun port entrant ; inférence locale.
- Négatives : la passerelle de données locale doit tourner sur un poste Windows de l'usine ; un arrêt du poste coupe l'agent dans Teams.
