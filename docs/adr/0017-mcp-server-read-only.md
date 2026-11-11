# ADR 0017 — Module 5a : serveur MCP local, en lecture seule, limité au niveau 2 par défaut

- **Statut** : accepté le 2026-11-10, à l'approbation de l'[EFVP du module 5](../efvp/direction-module.md#7-avis-du-responsable-décision-et-approbation) par la présidente (dans ce labo, le responsable du dépôt dans ce rôle)
- **Date** : 2026-11-10

## Contexte

Le module 5 doit rendre les agents du labo réutilisables par d'autres outils ([feuille de route](../roadmap.md#module-5--orchestration-pour-la-direction-serveur-mcp-et-docker)). Le *Model Context Protocol* (MCP) est le protocole ouvert le plus répandu pour cela : un **serveur** déclare des outils typés, et un **client** MCP (une application de clavardage, un éditeur, un agent) les appelle au nom de son propre modèle de langage.

Quatre faits fixent la conception :

- **Le client décide où va le résultat.** Un outil MCP rend son résultat au client, qui le donne à son modèle. Si ce modèle est hébergé, le résultat quitte le poste et, souvent, le Québec. Le serveur ne peut ni voir ni contrôler ce trajet.
- **Le client peut agir sans humain.** Un modèle qui appelle des outils peut enchaîner les appels de lui-même. Un outil qui écrit ou qui approuve, exposé par MCP, contournerait l'approbation humaine des [ADR 0012](0012-ops-architecture-typed-tools.md) et [0013](0013-ops-human-approval-and-state.md).
- **Les niveaux de sensibilité ne sont pas égaux.** La recherche Qualité et les politiques RH sont au niveau 2 ([échelle du labo](../loi25.md)) : aucun renseignement personnel (RP), mais un traitement « local par défaut ». L'état d'une commande et le diagnostic d'une machine touchent des données de niveau 4 (libellé d'un compte qui peut être une entreprise individuelle, historique de la GMAO), selon l'[EFVP du module 3](../efvp/ops-module.md).
- **Un transport réseau exige une authentification.** Le transport HTTP de MCP ouvre un port ; sans authentification, n'importe quel processus de la machine ou du réseau pourrait appeler les outils. L'authentification est prévue au module 6.

Cet ADR est rédigé en tenant compte de la *Loi sur la protection des renseignements personnels dans le secteur privé* (RLRQ, c. P-39.1), état du droit au 1er novembre 2026 (texte consolidé à jour au 15 octobre 2026, date revue le 10 novembre 2026) ; **ceci n'est pas un avis juridique**. Les énoncés juridiques viennent du [registre RH](../hr-legal-sources.md) et du [registre du module 3](../ops-legal-sources.md).

## Décision

### SDK officiel, transport stdio seulement

- Le serveur utilise le SDK Python officiel du protocole, le paquet `mcp` (version 2.3.0 au 2026-11-10), épinglé par `uv.lock`. C'est la seule nouvelle dépendance du sous-module 5a.
- **Seul le transport stdio est offert** : le client lance `alverno mcp serve` comme sous-processus et lui parle par l'entrée et la sortie standard. Aucun port n'est ouvert, aucune adresse n'est écoutée. Le code du serveur n'importe ni `uvicorn` ni `starlette`, même si le SDK les installe ; un test le vérifie dans un interpréteur neuf.
- Le transport HTTP (*streamable HTTP*) est **reporté au module 6**, avec l'authentification. L'ajouter avant exigerait un nouvel ADR et une nouvelle version de l'EFVP.

### Lecture seule, par construction

- Le registre MCP ne contient **aucun outil d'écriture** : ni brouillon (`customer_reply`), ni demande de travail (`work_request`), ni approbation, ni reprise d'un fil interrompu. Le serveur n'importe pas `agents/ops/approval.py` ni le nœud `apply`.
- Un test structurel compte les outils exposés capables d'écrire (`exposed_write_tools`), dans chaque configuration ; la borne est 0.

### Outils exposés par défaut : niveau 2 seulement

| Outil | Entrée | Sortie | Moteur |
|---|---|---|---|
| `quality_search` | requête (texte, 500 caractères au plus) ; `k` de 1 à 8 | liste de passages : identifiant de passage, `doc_id`, titre, version, extrait | recherche hybride du module 1, sans modèle de langage |
| `quality_ask` | question (texte, 500 caractères au plus) | réponse et citations, ou refus avec son motif | graphe du module 1, inchangé |
| `hr_policy_ask` | question (texte, 500 caractères au plus) | réponse et citations, ou refus avec son motif | enveloppe RH du module 2, inchangée |

- Chaque outil appelle **la même fonction** que la commande correspondante de la ligne de commande (`alverno ask`, `alverno hr ask`). Le serveur n'ajoute aucune règle de filtrage ; il traduit les entrées et les sorties. La parité est mesurée sur les jeux de référence des modules 1 et 2 ([ADR 0020](0020-direction-evaluation-protocol.md)).
- Les entrées sont validées par des modèles pydantic stricts : texte trop long, champ inconnu ou type invalide donnent une erreur d'outil, sans appel au moteur.
- Le texte d'une question n'est **jamais** écrit dans un journal, un rapport ou un message d'erreur du serveur. Une erreur d'outil donne un code fermé (`invalid_input`, `backend_unavailable`, `internal_error`), jamais une trace Python.

### Outils du module 3 : fermés par défaut

- Deux outils en lecture seule peuvent être ouverts par l'option `ALVERNO_MCP_OPS=true`, **désactivée par défaut** :
  - `order_status` : entrée typée `order_id` (identifiant `CMD-…`), sortie : le texte du flux `order_status` du module 3 ;
  - `machine_diagnosis` : entrées typées `asset_id` (identifiant `EQ-…`) et symptôme choisi dans le vocabulaire fermé de la GMAO, sortie : le diagnostic du flux `diagnosis` du module 3.
- Quand l'option est activée, le serveur écrit au démarrage, sur la sortie d'erreur, un avertissement qui rappelle la règle du client local ; un test le vérifie.
- Les entrées sont des **identifiants typés**, pas du texte libre : le client ne peut pas y glisser de RP par accident. Les deux outils passent par l'enveloppe du module 3 (`OpsEnvelope`), ses filtres, sa garde et son journal.
- **L'option ne doit être activée qu'avec un client MCP dont le modèle tourne sur le poste.** Une sortie du module 3 peut contenir un RP de niveau 4 (le libellé d'un compte qui est une entreprise individuelle) ; la donner à un modèle hébergé hors du Québec serait une communication hors Québec qui exige d'abord une EFVP et une entente écrite (art. 17 ; clé `lprp_communication_hors_quebec`). Le labo n'a fait ni l'une ni l'autre pour un client hébergé : l'[EFVP du module 5](../efvp/direction-module.md) l'interdit tant qu'une nouvelle version ne l'autorise pas.

### Un client local par défaut

- Le serveur ne peut pas savoir où son client envoie les résultats. La règle est donc écrite dans la documentation et dans le champ `instructions` que le serveur envoie au client à l'initialisation : « outils d'Alverno réservés à un client dont le modèle tourne sur le poste ».
- Le README donne une seule configuration de client : un client local (l'inspecteur de développement du protocole, ou tout client MCP relié à Ollama). Aucune configuration pour un client hébergé n'est fournie.
- Même au niveau 2, sans RP, un client hébergé ferait sortir des procédures et des spécifications de clients, souvent couvertes par une entente de confidentialité ([loi25.md](../loi25.md)). Ce n'est pas une question de la Loi sur le privé, mais la règle « local par défaut » du niveau 2 s'applique.

### Journal des accès : un canal `mcp`

- Les appels de `hr_policy_ask`, `order_status` et `machine_diagnosis` sont consignés par le journal de leur module ([ADR 0008](0008-minimal-access-log.md), [ADR 0014](0014-ops-access-log.md)), comme en ligne de commande. Le champ `channel` reçoit une nouvelle valeur, `mcp`, à côté de `cli` et `eval`. Aucun autre champ ne change, et le texte de la question reste exclu.
- Les outils Qualité ne consignent rien, comme `alverno ask` : le corpus Qualité ne contient aucun RP.

### Paramètres et câblage

- `Settings` reçoit deux champs : `mcp_ops_tools: bool = False` (variable `ALVERNO_MCP_OPS`) et `mcp_max_input_chars: int = 500`. Aucune adresse ni aucun port.
- Nouveau paquet `src/alverno_lab/mcp_server/` : `server.py` (déclaration des outils), `schemas.py` (modèles d'entrée et de sortie), `adapters.py` (appels aux fonctions existantes). Il dépend de `pipeline`, `hr_pipeline` et `ops_pipeline` ; aucun module existant ne dépend de lui.
- Le traçage hébergé reste coupé : chaque appel passe par les mêmes fonctions que la ligne de commande, qui roulent sous `tracing_context(enabled=False)`.

## Solutions écartées

- **Transport HTTP lié à `127.0.0.1`** : tout processus local pourrait appeler les outils, sans authentification ; le gain sur stdio est nul pour un poste seul.
- **Exposer les outils d'écriture avec une confirmation du client** : la confirmation se ferait dans l'interface du client, hors du contrôle d'Alverno, et un modèle pourrait la contourner ; les garanties G3 de l'[EFVP du module 3](../efvp/ops-module.md) ne tiendraient plus.
- **Ouvrir les outils du module 3 par défaut** : la seule mesure qui empêcherait un RP de niveau 4 de partir vers un modèle hébergé serait le choix du client, invisible pour le serveur.
- **Écrire un serveur MCP maison sans SDK** : plus de code à maintenir, et des écarts au protocole plus probables ; le SDK officiel est maintenu par le projet du protocole.
- **Un filtrage propre au serveur MCP** : deux jeux de règles divergeraient ; le serveur réutilise les filtres des modules 1 à 3.

## Conséquences

Positives :

- Les agents deviennent réutilisables par tout client MCP, sans dupliquer leurs règles : la parité avec la ligne de commande est mesurée.
- Aucun port, aucune écriture et aucun RP de niveau 4 ne sont exposés dans la configuration par défaut.

Négatives :

- La règle « client local » repose sur la documentation et sur la discipline de l'utilisateur : le serveur ne peut pas la vérifier. C'est un risque résiduel de l'[EFVP du module 5](../efvp/direction-module.md).
- Le SDK installe un serveur web et ses dépendances, inutilisés en stdio ; la surface d'installation grandit, pas la surface d'exécution.
- Sans transport réseau, un client sur un autre poste ne peut pas utiliser le serveur avant le module 6.

Voir aussi l'[ADR 0018](0018-direction-orchestrator.md) (orchestrateur de la direction), l'[ADR 0019](0019-docker-local-deployment.md) (images Docker) et l'[ADR 0020](0020-direction-evaluation-protocol.md) (protocole d'évaluation du module 5).
