# EFVP — Serveur MCP, orchestrateur de la direction et déploiement Docker d'Alverno (module 5)

> **Avertissement : ceci n'est pas un avis juridique.** Évaluation des facteurs relatifs à la vie privée rédigée dans le cadre d'un laboratoire, pour une entreprise entièrement fictive (Fabrications Alverno inc.). Ses clients, comptes, commandes, équipements, bons de travail et rôles sont inventés ; les domaines se terminent par `.example`. Elle a été rédigée en tenant compte de la *Loi sur la protection des renseignements personnels dans le secteur privé* (RLRQ, c. P-39.1), état du droit au 1er novembre 2026 (texte consolidé à jour au 15 octobre 2026 ; le 10 novembre 2026, LégisQuébec indiquait toujours cette date de mise à jour), et suit la structure du [gabarit](template.md), tiré du guide de la CAI sur l'EFVP (version 3.1, avril 2024). Chaque énoncé juridique renvoie à une clé du [registre RH](../hr-legal-sources.md) ou du [registre du module 3](../ops-legal-sources.md), écrite entre accents graves (par exemple `lprp_communication_hors_quebec`) ; le module 5 n'ajoute aucune clé. Aucune personne n'est nommée : on parle de rôles.

| Élément | Valeur |
|---|---|
| Version | 1.1 |
| Date | 2026-11-11 |
| Statut | version 1.0 approuvée le 2026-11-10 ; version 1.1 approuvée le 2026-11-11 |
| Moment | version 1.0 rédigée avant l'implémentation, quand aucun code `src/` du module 5 n'existait ; version 1.1 rédigée après les mesures du 2026-11-11 (portes, banc d'essai, jeu réservé, non-régression) |
| Gabarit | [template.md](template.md) |
| Sous-modules | 5a, serveur MCP (`alverno mcp serve`) ; 5b, orchestrateur de la direction (`alverno direction`) ; 5c, images Docker et `docker compose` |
| Lecteurs visés | le responsable de la protection des renseignements personnels (RPRP), la présidente, le soutien informatique |

**Les nombres de cette EFVP.** Chaque nombre est une borne préenregistrée, un compte prévu à la conception (jeux d'évaluation, données fictives), une valeur de politique du labo (k = 5, 90 jours) ou un effectif du profil fictif de l'entreprise. La version 1.1 ajoute les résultats mesurés le 2026-11-11, recopiés des rapports et regroupés à la section 5 ; chacun nomme son rapport.

## 0. Résumé

**Le projet.** Trois ajouts qui réutilisent les agents des modules 1 à 3, sans en changer les règles :

- **5a, serveur MCP** : rendre la recherche Qualité, l'agent Qualité et l'assistant RH utilisables par un client du *Model Context Protocol*, en lecture seule, par l'entrée et la sortie standard ([ADR 0017](../adr/0017-mcp-server-read-only.md)).
- **5b, orchestrateur de la direction** : répondre au comité de direction par sections (Qualité, RH, commandes, entretien), chacune produite sous l'enveloppe de son module ; les synthèses des commandes et de l'entretien sont calculées par le code ([ADR 0018](../adr/0018-direction-orchestrator.md)).
- **5c, Docker** : une image des agents et un fichier `docker compose` (agents + Ollama) pour un serveur de l'usine, sans port publié ni sortie Internet pour les agents ([ADR 0019](../adr/0019-docker-local-deployment.md)).

**Situation légale.**

| Article | Analyse | Conclusion |
|---|---|---|
| [Art. 3.3](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:3_3) (`lprp_efvp_systeme`) | Refonte d'un système d'information qui traite des RP : questions en texte libre (RP possibles par accident), synthèse des commandes (comptes, dont une entreprise individuelle), journaux des accès. | **EFVP requise** ; la voici, avant le code. |
| [Art. 17](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:17) (`lprp_communication_hors_quebec`) | Le serveur MCP rend ses résultats à un client choisi par l'utilisateur. Si le modèle de ce client est hébergé hors du Québec, un résultat qui contient un RP serait communiqué hors du Québec. Par défaut, les outils exposés sont de niveau 2, sans RP ; les outils du module 3, qui peuvent rendre un RP de niveau 4, sont fermés par défaut. L'orchestrateur n'est pas exposé par MCP. Les conteneurs n'ont aucune sortie Internet, sauf Ollama pour télécharger ses modèles. | **Non déclenché dans la configuration par défaut**, avec un client local. Un client hébergé, ou l'option `ALVERNO_MCP_OPS` avec un client hébergé, exigerait d'abord l'évaluation de l'art. 17 et une entente écrite : **interdit** par cette version. |
| Art. 21 | Aucune communication de RP à des fins d'étude, de recherche ou de statistiques ; les évaluations n'utilisent que des données synthétiques. Le contenu de l'article n'est pas résumé (énoncé exclu du [registre du module 3](../ops-legal-sources.md#énoncés-exclus)). | **Sans objet.** |

**Ce que le projet affirme.** Six affirmations vérifiables (G1 à G6, [annexe B](#b-affirmations-et-preuves)). Les trois principales : le serveur MCP n'expose **aucun** outil d'écriture et n'ouvre aucun port (G1) ; il rend, sur les jeux de référence des modules 1 et 2, exactement ce que rend la ligne de commande (G2) ; l'orchestrateur ne relie jamais deux domaines au sujet d'une personne et ne rend aucune donnée de technicien, aucun contact ni aucune durée de réparation (G4).

**Risques résiduels principaux.** R1 (client MCP hébergé choisi par l'utilisateur malgré la règle) et R2 (option `ALVERNO_MCP_OPS` activée avec un client hébergé) reposent sur la discipline de l'utilisateur : le serveur ne peut pas voir où va son résultat. R12 (droits sur le volume des journaux d'un serveur réel) dépend de l'hôte. Ces trois risques sont soumis à la présidente (section 7).

**Résultats (version 1.1).** Les sous-modules 5a et 5b sont réalisés et mesurés : toutes les portes réussissent, chaque compte de sécurité vaut 0 dans chaque exécution, et la non-régression des modules 1 à 3 ne montre aucun écart sur 202 items. Le jeu réservé de l'orchestrateur refuse ses 3 pièges, mais aussi 2 questions répondables sur 9 (section 5). **Le sous-module 5c (Docker) est reporté** par décision du responsable du dépôt le 2026-11-11 ([ADR 0019](../adr/0019-docker-local-deployment.md#report-de-létape-i8-2026-11-11)) : l'affirmation G6 et les risques R12 et R13 restent sans mesure.

**Avis et approbation.** Les onze décisions de conception ont été approuvées par le responsable du dépôt le 2026-11-10 (section 1). Avis du RPRP : **favorable, sous conditions**, confirmé le 2026-11-10 par le responsable du dépôt dans le rôle du RPRP. Approbation de la haute direction et acceptation des risques R1, R2 et R12 : **confirmées le 2026-11-10** (section 7). Version 1.1 : avis du RPRP sur les résultats et approbation de la haute direction **confirmés le 2026-11-11** par le responsable du dépôt dans ces deux rôles (section 7).

**Limites de l'évaluation.** Le serveur ne peut pas contrôler son client : l'évaluation couvre le serveur et la règle d'usage, pas un client précis. Le sous-module 5c n'est pas réalisé (version 1.1) : aucune image ni aucun fichier `compose` n'a été évalué. Les mesures porteront sur des données fictives et des connecteurs simulés ; les jeux sont petits (12 pièges de l'orchestrateur, 12 items réservés) et les bornes de la règle de trois resteront larges. Le règlement sur les incidents de confidentialité et celui sur l'anonymisation n'ont pas été analysés ([points incertains du registre du module 3](../ops-legal-sources.md#points-incertains)).

## 1. Description du projet et objet de l'EFVP

### Besoin et fins

Le comité de direction veut une vue d'ensemble sans ouvrir quatre outils, et les agents doivent pouvoir servir dans d'autres outils de travail. Les fins sont fixées avant toute collecte (art. 4 ; clé `lprp_collecte_interet_fins`) :

| Fin | Sous-module | Description | Niveau des données servies |
|---|---|---|---|
| X1 | 5a | Chercher des passages du corpus Qualité (`quality_search`) | 2 |
| X2 | 5a | Poser une question à l'agent Qualité (`quality_ask`) | 2 |
| X3 | 5a | Poser une question sur une politique RH (`hr_policy_ask`) | 2 |
| X4 | 5a | État d'une commande et diagnostic d'une machine (`order_status`, `machine_diagnosis`), **fermés par défaut** | 4 |
| D1 à D5 | 5b | Sections Qualité, RH, commandes, entretien, et le portrait commandes + entretien ([ADR 0018](../adr/0018-direction-orchestrator.md#fins-fixées-avant-la-collecte)) | 2 (D1, D2, D4) ; 4 (D3) |
| L | 5a, 5b | Journal des accès : détecter un usage anormal et répondre à une demande de la personne | 4 (règle du labo) |

Aucune fin ne prend une décision au sujet d'une personne ; aucune ne sert à évaluer un employé, un technicien ou un client.

### Fonctionnement

- **5a.** Un client MCP lance `alverno mcp serve` comme sous-processus. Le serveur liste trois outils (X1 à X3) ; il en ajoute deux (X4) si l'option `ALVERNO_MCP_OPS=true` est posée. Chaque outil appelle la même fonction que la commande correspondante de la ligne de commande et rend son résultat au client. Le serveur ne garde rien après l'appel.
- **5b.** Un membre de la direction tape une question dans `alverno direction ask`. Le filtre de la direction refuse les jointures sur une personne, le profilage, les informations restreintes, le niveau 5 et les questions hors des fins. Le routage choisit les fins ; chaque section passe ensuite par le filtre de son module. Les sections D3 et D4 sont calculées par le code à partir de projections fermées ; le modèle de langage peut en reformuler une, seule, et l'étape `verify` retire toute phrase dont un nombre ou un identifiant n'est pas dans le bloc de faits.
- **5c.** Sur un serveur, `docker compose` lance Ollama et les agents. Les agents n'ont aucune sortie Internet et aucun port n'est publié ; on les utilise par `docker compose run`.

**Ce que le module refuse.** Une question sur une personne ou un rôle unique croisée avec un autre domaine ; un classement, un rendement ou une durée rattachés à une personne ou à un groupe de moins de 5 personnes ; les prix, coûts et marges ; la santé, les accidents et les mesures disciplinaires.

**Ce que le module ne fera jamais dans cette version.** Écrire ou approuver quoi que ce soit par MCP ; ouvrir un port ; exposer l'orchestrateur par MCP ; relier deux domaines dans un même texte ; rendre une durée de réparation ; publier une image.

### Objet de l'évaluation

- **Inclus** : `alverno mcp serve`, `alverno mcp eval`, `alverno direction` (`ask`, `eval`, `bench`), les nouvelles projections de synthèse des connecteurs simulés, le journal `direction`, la nouvelle valeur `mcp` du champ `channel` des journaux RH et du module 3, l'image Docker et le fichier `docker compose`.
- **Exclus** : le client MCP lui-même (choisi par l'utilisateur ; voir R1), un transport HTTP, l'authentification, un connecteur réel, un modèle hébergé, une mise en service réelle.
- **Hypothèses** : les modules 1 à 3 gardent leurs règles et leurs EFVP ([EFVP RH](hr-module.md), [EFVP du module 3](ops-module.md)) ; les données sont fictives ; l'hôte d'un déploiement Docker est un serveur d'Alverno, au Québec.

### Décisions de conception

Approuvées par le responsable du dépôt le 2026-11-10, avant la rédaction de cette EFVP :

1. Trois sous-modules livrés dans l'ordre 5a, 5b, 5c, chacun avec ses portes.
2. SDK officiel `mcp`, transport stdio seulement ; HTTP au module 6, avec l'authentification ([ADR 0017](../adr/0017-mcp-server-read-only.md)).
3. Serveur MCP en lecture seule : aucun outil d'écriture ni d'approbation.
4. Outils de niveau 2 seulement par défaut ; outils du module 3 derrière `ALVERNO_MCP_OPS=true`, désactivée par défaut, avec l'analyse de l'art. 17 de cette EFVP.
5. Même garde et même journal qu'en ligne de commande ; canal `mcp` au journal, jamais le texte de la question.
6. Routage déterministe de l'orchestrateur ; aucun planificateur choisi par le modèle avant le module 4 ([ADR 0018](../adr/0018-direction-orchestrator.md)).
7. Réponse par sections, sans fusion par le modèle.
8. Refus des jointures sur une personne et des agrégats qui visent moins de 5 personnes.
9. Images construites et testées dans la CI seulement, sans publication sur un registre ; Docker n'est pas installé sur le poste du labo ([ADR 0019](../adr/0019-docker-local-deployment.md)).
10. EFVP avant tout code, jeux de référence, jeu réservé écrit par une session distincte et mesuré une seule fois, bornes préenregistrées, non-régression des modules 1 à 3 ([ADR 0020](../adr/0020-direction-evaluation-protocol.md)).
11. Branche `module-5-direction` partie de `main`, une PR à la fin.

## 2. Rôles et responsabilités

| Rôle | Responsabilité dans le projet | Consulté le |
|---|---|---|
| Présidente | Plus haute autorité ; a délégué par écrit la fonction de RPRP ([art. 3.1](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:3_1), `lprp_responsable_rprp` ; acte à l'annexe D de l'[EFVP RH](hr-module.md#d-documents-joints)). Commanditaire de 5b ; approuve l'EFVP et décide des risques résiduels. | 2026-11-10 (décisions 1 à 11) |
| Directeur des finances et de l'administration | **RPRP par délégation écrite.** Consulté dès le début du projet ([art. 3.3](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:3_3), `lprp_efvp_systeme`) ; donne son avis sur cette EFVP. Membre du comité de direction, donc aussi utilisateur de 5b. | 2026-11-10 (planification) |
| Directrice des ressources humaines | Utilisatrice de 5b ; relit les pièges qui visent les RH. | à inscrire |
| Directeur de production | Utilisateur de 5b ; commanditaire de 3b, dont les données alimentent D4. | à inscrire |
| Responsable du dépôt | Réalise le module, les évaluations et la documentation. | — |
| Soutien informatique | Exploite un éventuel serveur Docker ; gère les droits sur le volume des journaux ; lit le journal seulement sous l'autorité du RPRP. | non consulté à cette date |

Aucune autre consultation (représentants du personnel, conseiller juridique, clients) n'a eu lieu dans ce labo. **Les rôles sont simulés** : le responsable du dépôt joue le rôle de la présidente et du RPRP.

Boîtes de rôle : confidentialite@alverno.example (RPRP, accès et rectification), soutien-ti@alverno.example.

## 3. Renseignements personnels et ampleur de l'évaluation

| Renseignement | Sensibilité (1-5) | Finalité | Quantité | Répartition | Support | Conservation | Accès (catégories de personnes) | Communication (destinataires, hors Québec ?) |
|---|---|---|---|---|---|---|---|---|
| Question reçue par MCP (texte libre ; un RP peut y entrer par accident) | jusqu'à 4 | X1 à X3 | une par appel | poste ou serveur | mémoire du processus | fin de l'appel | le code ; jamais un journal ni une erreur | aucune par le serveur ; le client la détient déjà (hors périmètre, R1) |
| Résultats X1 à X3 (passages, réponses, refus) | 2 | X1 à X3 | un par appel | poste ou serveur | mémoire, puis le client | fin de l'appel côté serveur | l'utilisateur du client | au client MCP ; **local par défaut** ; un client hébergé est interdit par cette version |
| Résultats X4 (texte de l'état d'une commande, diagnostic) | 4 (libellé d'un compte personne physique ; identification indirecte par la commande, H3 du [registre du module 3](../ops-legal-sources.md#hypothèses-juridiques-du-plan-et-conclusions)) | X4 | un par appel | poste | mémoire, puis le client | fin de l'appel côté serveur | l'utilisateur du client local | **seulement un client local**, option désactivée par défaut (art. 17, `lprp_communication_hors_quebec`) |
| Question de direction (texte libre) | jusqu'à 4 | D1 à D5 | une par requête | poste ou serveur | mémoire | fin de la requête | le code | aucune |
| Section D3 (commandes : identifiants, comptes, dates, motifs) | 4 (traité comme tel, règle du labo) | D3, D5 | 40 commandes fictives | poste ou serveur | mémoire, terminal | fin de la requête | les 4 rôles du comité de direction (art. 20, `lprp_acces_besoin_fonctions`) | aucune |
| Section D4 (équipements, codes de défaillance, nombres de bons) | 2 (aucune personne, aucune durée) | D4, D5 | 10 équipements, 120 bons fictifs | poste ou serveur | mémoire, terminal | fin de la requête | le comité de direction | aucune |
| Sections D1 et D2 | 2 | D1, D2 | une par requête | poste ou serveur | mémoire, terminal | fin de la requête | le comité de direction | aucune |
| Journal `direction` (fins, résultats par section, codes) | traité comme 4 | L | une ligne par requête | `logs/access/` ou volume `alverno-logs` | JSONL quotidien | 90 jours au plus (règle du labo) | RPRP ; soutien informatique sous son autorité | aucune |
| Lignes des journaux RH et du module 3 avec `channel: mcp` | traité comme 4 | L | une ligne par appel X3 ou X4 | idem | idem | idem | idem | aucune |
| Rapports d'évaluation et de banc | données synthétiques | évaluation | par exécution | `reports/` | JSON, Markdown | versionnés | public (dépôt) | publication des rapports balayés ; aucun RP réel |

**Champs jamais servis.** Contacts des comptes (nom, courriel, téléphone), prix, coûts, marges, notes internes et visibles au client, champs des techniciens (nom, matricule, initiales, identifiant de connexion, quart), remarques des bons de travail, temps d'arrêt et durées de réparation. Les projections D3 et D4 ne les lisent pas ; les comptes de sécurité de l'[ADR 0020](../adr/0020-direction-evaluation-protocol.md) les cherchent quand même.

**Constat k = 5.** Le service d'entretien compte 4 personnes ([organigramme fictif](../../data/hr/org_roles.yaml)) ; le comité de direction aussi. Aucune durée, aucun résultat et aucune faute n'est attribué à l'un de ces groupes.

**Ampleur.** Volume faible (données fictives, quatre utilisateurs de 5b), mais deux facteurs élèvent le risque : le client MCP échappe au contrôle du serveur, et l'orchestrateur réunit des domaines qui, croisés, permettraient un profilage. L'évaluation se concentre sur ces deux points.

## 4. Conformité aux obligations

| Obligation (paraphrase) | Article | Mesure | Statut |
|---|---|---|---|
| Responsable de la protection des RP, délégation écrite, titre et coordonnées publiés | 3.1 (`lprp_responsable_rprp`) | Délégation écrite faite au module 2 ; le RPRP donne son avis sur cette EFVP | en place (simulée) |
| Politiques et pratiques de gouvernance (conservation et destruction, rôles, plaintes) | 3.2 (`lprp_gouvernance`) | La politique de gouvernance reçoit les règles du module 5 : client MCP local, option X4, journal `direction`, volume des journaux | prévu (avant la mise en service) |
| EFVP d'un système d'information ; RPRP consulté dès le début | 3.3 (`lprp_efvp_systeme`) | Cette EFVP, avant tout code `src/` du module ; RPRP consulté à la planification | en place |
| Remise des RP informatisés dans un format structuré d'usage courant (portabilité) | 3.3 (`lprp_efvp_portabilite`) | Aucun RP n'est recueilli auprès d'une personne pour être conservé ; les lignes du journal sont créées par l'outil. Lecture du labo, comme au module 3 : sans objet en v1, à confirmer par le RPRP | incertain |
| Incident de confidentialité : mesures, avis, registre | 3.5 à 3.8 (`lprp_incident_mesures_avis`, `lprp_incident_definition`, `lprp_incident_risque_prejudice`, `lprp_registre_incidents`) | Un résultat X4 donné à un client hébergé, ou une jointure servie, serait traité comme un incident ; le journal sert à le détecter, il ne remplace pas le registre | prévu (procédure du module 2) |
| Intérêt sérieux et légitime ; fins fixées avant la collecte | 4 (`lprp_collecte_interet_fins`) | Fins X1 à X4, D1 à D5 et L fixées à la section 1, avant le code | en place |
| Collecte limitée aux RP nécessaires | 5 (`lprp_collecte_necessaire`) | Projections fermées D3 et D4 ; entrées X4 typées (identifiants), sans texte libre ; aucun champ de technicien ni de contact | prévu (I3, I5) |
| Information de la personne lors de la collecte | 8 (`lprp_information_collecte`) | Avis affiché au lancement de `alverno direction ask` et envoyé au client MCP dans le champ `instructions` : ce que fait l'outil, que la question n'est pas conservée, ce que note le journal, 90 jours | prévu (I3, I5) |
| Technologie d'identification, de localisation ou de profilage | 8.1 (`lprp_technologie_profilage`) | Aucune fonction de profilage ; refus `person_join` et `profiling` ; aucune durée dans D4 | prévu (I5) |
| Paramètres de confidentialité les plus protecteurs par défaut (application incertaine) | 9.1 (`lprp_parametres_defaut`) | Application à un outil interne incertaine ; défauts protecteurs quand même, présentés comme un choix d'Alverno : stdio, outils X4 fermés, aucun port, aucune sortie Internet pour les agents | prévu |
| Mesures de sécurité raisonnables | 10 (`lprp_mesures_securite`) | Lecture seule ; stdio ; validation stricte des entrées ; erreurs sans contenu ; conteneur non privilégié, sans port ni sortie ; image sans jeu réservé ni secret ; dépendance épinglée | prévu (I3, I8) |
| Utilisation limitée aux fins de la collecte | 12 (`lprp_utilisation_fins`) | Le journal ne sert qu'à la fin L ; aucune analyse des requêtes de la direction | prévu |
| Décision fondée exclusivement sur un traitement automatisé | 12.1 (`lprp_decision_automatisee`) | Aucune décision au sujet d'une personne ; les sections sont une aide à la direction | sans objet (aucune décision) |
| Communication ou traitement hors Québec | 17 (`lprp_communication_hors_quebec`) | Client local par défaut ; X4 fermé ; orchestrateur non exposé ; agents sans sortie Internet ; client hébergé interdit sans nouvelle version de l'EFVP et entente écrite | en place à la conception ; R1 et R2 résiduels |
| Mandataire ou prestataire de services | 18.3 (`lprp_mandataire_prestataire`) | Non déclenché : aucun fournisseur ; un client MCP hébergé ou un hébergeur du serveur Docker le déclencherait | sans objet en v1 |
| Destruction ou anonymisation une fois les fins accomplies | 23 (`lprp_destruction_anonymisation`) | Aucune question ni réponse conservée ; purge du journal à 90 jours au plus, sur le poste comme dans le volume Docker | prévu (I5, I8) |

## 5. Risques et stratégies d'atténuation

| # | Risque | Mesures | Preuve | Risque résiduel |
|---|---|---|---|---|
| R1 | Un utilisateur relie le serveur MCP à un client dont le modèle est hébergé : les questions (avec un RP tapé par accident) et les résultats de niveau 2 sortent du poste et du Québec | Règle « client local » dans le champ `instructions`, le README et la politique de gouvernance ; aucune configuration de client hébergé fournie ; outils par défaut de niveau 2 | Revue de la documentation ; test du champ `instructions` | **Moyen** : le serveur ne voit pas son client ; soumis à la présidente |
| R2 | L'option `ALVERNO_MCP_OPS` est activée avec un client hébergé : un RP de niveau 4 (libellé d'une entreprise individuelle) sort du Québec sans EFVP ni entente (art. 17) | Option désactivée par défaut ; avertissement au démarrage quand elle est activée ; entrées typées ; interdiction écrite dans cette EFVP | `ops_tools_exposed_by_default` = 0 ; test de l'avertissement | **Moyen** : même limite que R1 ; soumis à la présidente |
| R3 | Un modèle appelle un outil d'écriture ou d'approbation par MCP, sans humain | Aucun outil d'écriture dans le registre MCP ; le serveur n'importe pas l'approbation | `exposed_write_tools` = 0 et son contrôle négatif | faible |
| R4 | Un processus local ou du réseau appelle les outils sans authentification | stdio seulement ; aucun port dans l'image ni dans `compose` | `network_listeners` = 0 ; contrôles structurels Docker | faible |
| R5 | Les règles du serveur MCP divergent de celles de la ligne de commande | Le serveur appelle les mêmes fonctions et n'ajoute aucun filtre | `parity_mismatches` = 0 sur 164 items | faible |
| R6 | Le texte d'une question (avec un RP) est écrit dans un journal, un rapport ou une erreur | Codes d'erreur fermés ; journaux sans texte | `log_content_hits` = 0 (5a et 5b) | faible |
| R7 | L'orchestrateur croise deux domaines au sujet d'une personne et crée un profil | Refus `person_join` ; une section par domaine ; jamais deux domaines dans une invite | `trap_refusal_rate` = 1,0 ; `cross_section_claims` = 0 | faible ; mesuré sur 4 pièges et le jeu réservé |
| R8 | Un agrégat désigne un petit groupe (entretien : 4 personnes) | Refus `profiling` ; aucune durée dans D4 | `duration_exposure` = 0 ; pièges `profiling` | faible |
| R9 | Une synthèse rend un contact, un champ de technicien, une note ou le libellé d'une personne physique | Projections fermées D3 et D4 | `contact_exposure`, `technician_identity_exposure`, `restricted_field_exposure`, `natural_person_label_exposure` = 0 | faible |
| R10 | Le modèle invente un nombre ou un lien de cause entre domaines, qui oriente une décision de la direction | Sections calculées par le code ; `verify` ; aucune fusion | `count_fidelity` (≥ 0,90 sur Ollama) ; `must_not_include_hits` = 0 | moyen : une reformulation peut encore être trompeuse sans nombre faux |
| R11 | Une injection plantée dans les données passe dans une section | Les projections D3 et D4 ne lisent ni notes ni remarques | `injection_echo_count` = 0 | faible |
| R12 | Sur un serveur réel, le volume des journaux est lisible par d'autres comptes, ou le conteneur des agents joint Internet | Réseau interne sans sortie ; utilisateur non privilégié ; droits du volume à vérifier par le soutien informatique avant la mise en service | Contrôles structurels de la CI ; vérification datée (section 6) | **moyen** sur un serveur réel : dépend de l'hôte ; soumis à la présidente |
| R13 | L'image contient un jeu réservé, un secret ou un journal | `.dockerignore` ; aucun registre | Test de la liste d'exclusion ; contrôle « fichier exclu absent de l'image » | faible |
| R14 | Une nouvelle dépendance (SDK `mcp` et ses dépendances) envoie des données ou ouvre un service | Version épinglée ; aucun import de serveur web ; vérification, à l'incrément I3, qu'aucun exportateur de télémétrie n'est installé ni activé | Test d'import dans un interpréteur neuf ; `network_listeners` = 0 | faible |

### Résultats mesurés (version 1.1)

Mesures du 2026-11-11 sur le poste du labo (RTX 3060 Ti, Ollama 0.24.0), recopiées des rapports ; bornes préenregistrées par l'[ADR 0020](../adr/0020-direction-evaluation-protocol.md) et, pour les profils simulés de l'orchestrateur, calibrées une seule fois par l'[ADR 0021](../adr/0021-direction-gate-calibration.md). Chaque dossier de rapport publié a été balayé avec les témoins des huit jeux (référence et réservés des modules 2, 3a, 3b et 5b) : aucun témoin trouvé. **Règle de trois** : 0 erreur sur n essais signifie que le vrai taux est sous 3/n avec 95 % de confiance.

**5a, serveur MCP** (porte `mcp-fake`, intégration continue). La porte réussit : `parity_mismatches` 0 sur 164 appels des jeux des modules 1 et 2 ; 16 pièges de protocole rejetés sur 16 avec le bon code, sans qu'aucun n'atteigne un agent ; `exposed_write_tools`, `ops_tools_exposed_by_default`, `network_listeners`, `log_content_hits`, `answer_leak_count`, `artifact_canary_hits` et `channel_mismatch` à 0. Règle de trois : 0 piège non rejeté sur 16, borne supérieure de 0,188.

**5b, portes simulées** (intégration continue). `dir-tools-fake` et `dir-full-fake` réussissent : comptes fidèles 1,000 (20 sections), faits clés 0,857, citations valides 1,000 (11), tâches réussies 0,821, 12 pièges refusés sur 12, chaque compte de sécurité à 0.

**5b, banc d'essai sur Ollama** (`reports/bench-dir-ollama-20261111-040705/`) :

| Modèle | Porte par répétition | Comptes fidèles | Faits clés | Citations valides | Tâches réussies | Comptes de sécurité non nuls |
|---|---|---|---|---|---|---|
| `gemma4:e4b`, k = 3 | passe, passe, passe | 1,000 | 0,911 | 1,000 | 0,821 | 0 sur 14 |
| `ministral-3:8b`, k = 1 | passe | 1,000 | 0,875 | 1,000 | 0,786 | 0 sur 14 |
| `llama3.2:3b`, k = 1 | passe | 1,000 | 0,857 | 1,000 | 0,821 | 0 sur 14 |

Les trois répétitions de `gemma4:e4b` donnent des réponses identiques. Les items ratés sont tous des sections Qualité ou RH dont la réponse ne contient qu'une partie des faits attendus : ils mesurent les moteurs des modules 1 et 2. Règle de trois, pire répétition : 0 piège non refusé sur 12, borne de 0,250 ; 0 item dont un texte servi contient un témoin, sur 16, borne de 0,188.

Deux bancs précédents du même jour sont versés tels quels, avec une note de provenance. Le premier comptait mal les sections en erreur : une section dont le modèle ne répond pas sert un texte fixe (« Section indisponible. »), et ces items ne comptaient pas dans `error_rate`. Le second a montré la cause de ces erreurs : une génération sans limite de longueur bloquait Ollama environ 15 minutes. Chaque réponse est depuis plafonnée à 1024 jetons (précisions de l'étape I7 de l'ADR 0020). Dans ces deux bancs aussi, chaque compte de sécurité valait 0.

**Jeu réservé, mesuré une seule fois** (`reports/dir-holdout/`), en mode outils (`--fake`) puis avec `gemma4:e4b` (informatif) ; les deux modes donnent les mêmes résultats :

| Items | Pièges refusés | Faux blocages | Tâches réussies | Comptes de sécurité | Écart publié |
|---|---|---|---|---|---|
| 12 (9 répondables, 3 pièges, 1 paire, 5 avec témoins) | 3 sur 3 | 2 sur 9 (`false_block_rate` 0,222) | 7 sur 9 | 0 | une vue des commandes refusée comme jointure sur une personne ; une question Qualité refusée comme hors champ |

Ces résultats ne servent pas à régler les règles, et le jeu réservé n'est jamais remesuré.

**Non-régression des modules 1 à 3** (`reports/m5-regression-ollama-20261111/`). Références produites juste avant la comparaison avec le code de clôture du module 3 (`619bd26`, index reconstruits), puis mêmes profils avec le code du module 5, `gemma4:e4b`, k = 1 : les 8 portes réussissent, aucun item n'est en erreur, et les 202 items (50, 64, 48 et 40) sont identiques sur tous les champs sauf la latence, réponses comprises. Le plafond de génération ne change donc aucune réponse des modules 1 à 3.

**Revue des risques résiduels.**

- **R7 (jointure sur une personne)** : 4 pièges `person_join` du jeu de référence refusés sur chaque exécution, et celui du jeu réservé aussi ; `cross_section_claims` 0. L'erreur du jeu réservé va dans le sens prudent : une question légitime refusée. Risque résiduel **faible**, inchangé.
- **R8 (petit groupe)** : pièges `profiling` refusés partout ; `duration_exposure` 0. **Faible**, inchangé.
- **R10 (nombre inventé)** : `count_fidelity` 1,000 pour chaque modèle et au jeu réservé ; `must_not_include_hits` 0. Les mesures ne couvrent pas une reformulation trompeuse sans nombre faux : risque résiduel maintenu à **moyen**.
- **R14 (dépendance)** : le SDK `mcp` charge sa pile web à l'import, mais n'ouvre aucun port (`network_listeners` 0) et seule l'API OpenTelemetry est installée, sans son SDK ([ADR 0017](../adr/0017-mcp-server-read-only.md)). **Faible**, confirmé.
- **R12 et R13 (Docker)** : sans mesure, 5c étant reporté ; aucun serveur n'est déployé. R12 reste accepté pour le labo ; les deux seront évalués avec le déploiement retenu.
- **R1 et R2 (client hébergé)** : inchangés ; ils reposent sur la règle d'usage, que le serveur rappelle dans son champ `instructions`.

## 6. Plan d'action

| Action | Responsable (rôle) | Échéance | Critère |
|---|---|---|---|
| Confirmer l'avis du RPRP | RPRP | Fait le 2026-11-10 | Avis daté à la section 7 |
| Approuver l'EFVP et décider des risques R1, R2 et R12 | Présidente | Fait le 2026-11-10 | Approbation datée ; ADR 0017 à 0020 passent à « accepté » |
| Écrire le jeu réservé `dir_holdout` (12 items) dans une session séparée, sans accès au code du filtre ni du routage | Responsable du dépôt | Fait à l'incrément I2, avant ce code | Empreinte inscrite au registre de l'[ADR 0020](../adr/0020-direction-evaluation-protocol.md) |
| Vérifier qu'aucun exportateur de télémétrie n'est installé ni activé par le SDK `mcp` | Responsable du dépôt | Fait à l'incrément I3 ([ADR 0017](../adr/0017-mcp-server-read-only.md)) | Test d'import et note datée |
| Documenter un client MCP local et la règle « client local » | Responsable du dépôt | Fait à l'incrément I4 | README et champ `instructions` |
| Réussir les portes et mesurer le jeu réservé | Responsable du dépôt | Fait le 2026-11-11, sauf le travail Docker (5c reporté) | Critère de sortie de l'ADR 0020 |
| Inscrire les règles du module dans la politique de gouvernance | RPRP | Avant la mise en service | Politique mise à jour et datée |
| Vérifier les droits du volume des journaux et l'absence de port sur le serveur réel | Soutien informatique | Avant la mise en service du déploiement retenu (5c reporté) | Vérification datée |
| Publier la version 1.1 | Responsable du dépôt et RPRP | Fait le 2026-11-11 : résultats recopiés ; avis confirmé et version approuvée | Résultats recopiés ; avis confirmé ; approbation |

| Réévaluation | Date ou déclencheur |
|---|---|
| Version 1.1 | Rédigée le 2026-11-11, après les mesures des incréments I7 et I9 |
| Déploiement sur un serveur (5c ou solution de remplacement) | Avant toute mise en œuvre, avec les risques R12 et R13 et l'affirmation G6 |
| Revue périodique | Au plus tard le 2027-11-10, puis chaque année |
| Transport HTTP ou authentification (module 6) | Avant tout développement |
| Client MCP hébergé, ou option `ALVERNO_MCP_OPS` avec un client autre que local | Avant tout usage, avec l'évaluation de l'art. 17 et une entente écrite |
| Outil d'écriture ou d'approbation par MCP | Avant tout développement |
| Orchestrateur exposé par MCP | Avant tout développement |
| Lien entre deux domaines (par exemple commandes touchées par une panne) | Avant tout développement |
| Durées ou temps d'arrêt dans une synthèse | Avant l'ajout (règle k = 5) |
| Publication d'une image sur un registre | Avant la publication |
| Connecteur réel, modèle hébergé, hébergeur du serveur Docker | Avant tout développement, avec l'examen des art. 17 et 18.3 |
| Planificateur choisi par le modèle (module 4) | Avant l'ajout à une porte |
| Changement d'effectif du service d'entretien ou du comité de direction | À la mise à jour de l'organigramme (règle k = 5) |
| Changement de la loi ou d'une interprétation marquée « incertain » | À la publication, avec la mise à jour des registres |

## 7. Avis du responsable, décision et approbation

### Avis du RPRP

**Avis : favorable, sous conditions** (rédigé le 2026-11-10, **confirmé le 2026-11-10** par le responsable du dépôt dans le rôle du RPRP) :

1. aucune ligne de code `src/` du module 5 avant la confirmation de cet avis et l'approbation de cette version ;
2. aucun client MCP hébergé, et l'option `ALVERNO_MCP_OPS` seulement avec un client local, tant qu'une nouvelle version de l'EFVP ne l'autorise pas ;
3. aucun outil d'écriture, aucun transport réseau et aucune exposition de l'orchestrateur par MCP sans nouvelle version de l'EFVP ;
4. aucune analyse par personne, par rôle unique ou par groupe de moins de 5 personnes, ni à partir du journal, ni dans une synthèse ;
5. toutes les actions « avant la mise en service » de la section 6 sont réalisées avant tout usage réel ;
6. la version 1.1, avec les résultats mesurés, est soumise au RPRP ;
7. si une borne de sécurité échoue, on corrige le système, jamais la borne, et le résultat négatif est publié tel quel.

**Version 1.1** : soumise au RPRP avec les résultats mesurés (condition 6) ; avis **favorable, sous les mêmes conditions**, confirmé le 2026-11-11 par le responsable du dépôt dans le rôle du RPRP. Les conditions 2, 3 et 5 restent ouvertes jusqu'à la mise en service.

Les lectures juridiques marquées « incertain » restent des lectures du labo : la signature de l'avis ne les transforme pas en avis juridique.

### Risques soumis à la présidente

Le module n'utilise aucun renseignement de niveau 5 : une question de ce type est refusée (`sensitive_out_of_scope`). La présidente est invitée à accepter, pour la durée du labo, les risques résiduels R1 (client hébergé choisi malgré la règle), R2 (option X4 avec un client hébergé) et R12 (droits de l'hôte sur un serveur réel) : **acceptés le 2026-11-10** par le responsable du dépôt dans le rôle de la présidente.

### Approbation de la haute direction

| Élément | Contenu |
|---|---|
| Avis du RPRP | Favorable, sous conditions ; confirmé le 2026-11-10 |
| Décisions de conception 1 à 11 | Approuvées par le responsable du dépôt le 2026-11-10 |
| Risques R1, R2 et R12 | Acceptés par la présidente le 2026-11-10 |
| Approbation de la version 1.0 | Approuvée |
| Avis du RPRP sur la version 1.1 | Favorable, sous les mêmes conditions ; confirmé le 2026-11-11 |
| Approbation de la version 1.1 | Approuvée, avec les résultats mesurés de la section 5 |
| Approuvé par | La présidente ; dans ce labo, le responsable du dépôt dans ce rôle |
| Date | 2026-11-10 (version 1.0) ; 2026-11-11 (version 1.1) |

| Version | Date | Changements |
|---|---|---|
| 1.0 | 2026-11-10 | Première version, rédigée avant l'implémentation ; approuvée le 2026-11-10 |
| 1.1 | 2026-11-11 | Après les mesures : portes simulées 5a et 5b, banc d'essai de l'orchestrateur sur Ollama, jeu réservé mesuré une seule fois, non-régression des modules 1 à 3 sans écart ; risques R7, R8, R10 et R14 revus ; 5c reporté (G6, R12 et R13 sans mesure) ; ADR 0021 et rapports joints (annexe D) ; avis du RPRP confirmé et version approuvée le 2026-11-11 |

## Annexes

### A. Modèle de menace

| Menace | Acteur | Vecteur | Barrière |
|---|---|---|---|
| Fuite hors du Québec par le client | utilisateur de bonne foi | client MCP hébergé | règle « client local », niveau 2 par défaut, X4 fermé (R1, R2) |
| Action sans humain | modèle du client | appel d'outil enchaîné | aucun outil d'écriture (R3) |
| Appel non autorisé | processus local ou poste du réseau | port ouvert | stdio, aucun port (R4) |
| Profilage par la direction | utilisateur légitime | question qui croise deux domaines | `person_join`, `profiling`, sections (R7, R8) |
| Injection indirecte | texte planté dans les données | note ou remarque | projections fermées (R11) |
| Fuite par l'image | lecteur d'une image | fichier copié par erreur | `.dockerignore`, aucun registre (R13) |
| Dépendance malveillante ou bavarde | chaîne d'approvisionnement | paquet du SDK | épinglage, test d'import (R14) |

Hors périmètre : un administrateur malveillant de l'hôte, un poste compromis, un client MCP modifié pour mentir sur son modèle.

### B. Affirmations et preuves

| # | Affirmation | Preuve |
|---|---|---|
| G1 | Le serveur MCP n'expose aucun outil d'écriture et n'ouvre aucun port | `exposed_write_tools` = 0 ; `network_listeners` = 0 ; contrôles négatifs |
| G2 | Le serveur MCP rend ce que rend la ligne de commande | `parity_mismatches` = 0 sur 164 items des jeux des modules 1 et 2 |
| G3 | Aucun outil du module 3 n'est exposé sans l'option, et aucune question n'est consignée | `ops_tools_exposed_by_default` = 0 ; `log_content_hits` = 0 |
| G4 | L'orchestrateur ne relie jamais deux domaines au sujet d'une personne et ne rend ni contact, ni champ de technicien, ni durée | `trap_refusal_rate` = 1,0 ; `cross_section_claims`, `contact_exposure`, `technician_identity_exposure`, `duration_exposure` = 0 |
| G5 | Les nombres des synthèses sont ceux des données | `count_fidelity` = 1,0 en mode outils ; ≥ 0,90 sur Ollama |
| G6 | L'image ne contient aucun jeu réservé, ne tourne pas en `root`, ne publie aucun port et n'est poussée nulle part | contrôles structurels du travail Docker de la CI |

### C. Bornes de la porte

Les bornes, les préconditions et les contrôles négatifs sont fixés dans l'[ADR 0020](../adr/0020-direction-evaluation-protocol.md) : profils `mcp-fake`, `dir-tools-fake`, `dir-full-fake` et `dir-full-ollama`. Les comptes de sécurité valent 0 et les taux de contrôle 1,0 dans chaque profil ; les bornes de service de `dir-full-ollama` sont préenregistrées.

### D. Documents joints

- Décisions : [ADR 0017](../adr/0017-mcp-server-read-only.md) (serveur MCP), [ADR 0018](../adr/0018-direction-orchestrator.md) (orchestrateur), [ADR 0019](../adr/0019-docker-local-deployment.md) (Docker, reporté), [ADR 0020](../adr/0020-direction-evaluation-protocol.md) (évaluation), [ADR 0021](../adr/0021-direction-gate-calibration.md) (calibration des profils simulés).
- Rapports (version 1.1) : `reports/bench-dir-ollama-20261111-040705/` (banc d'essai), `reports/bench-dir-ollama-20261111-005346/` et `reports/bench-dir-ollama-20261111-014457/` (bancs précédents, avec leur note de provenance), `reports/dir-holdout/` (jeu réservé), `reports/m5-regression-ollama-20261111/` (non-régression).
- Sources juridiques : [registre RH](../hr-legal-sources.md) et [registre du module 3](../ops-legal-sources.md), vérifiés le 1er novembre 2026 ; date de mise à jour du texte consolidé revue le 10 novembre 2026.
- Cadre : [loi25.md](../loi25.md) (échelle de sensibilité), [gabarit](template.md), [EFVP RH](hr-module.md), [EFVP du module 3](ops-module.md), [guide de la CAI](https://www.cai.gouv.qc.ca/uploads/pdfs/CAI_GU_EFVP.pdf).

*Document fictif à des fins de démonstration ; ne constitue pas un avis juridique.*
