# ADR 0018 — Module 5b : orchestrateur de la direction, routage déterministe et sections sans fusion

- **Statut** : accepté le 2026-11-10, à l'approbation de l'[EFVP du module 5](../efvp/direction-module.md#7-avis-du-responsable-décision-et-approbation) par la présidente (dans ce labo, le responsable du dépôt dans ce rôle)
- **Date** : 2026-11-10

## Contexte

Le comité de direction d'Alverno (présidente, directeur des finances et de l'administration, directrice des ressources humaines, directeur de production) veut une vue consolidée : où en sont les commandes, quelles machines tombent en panne, que disent les procédures Qualité et les politiques RH sur un sujet donné ([feuille de route](../roadmap.md#module-5--orchestration-pour-la-direction-serveur-mcp-et-docker)). Les modules 1 à 3 répondent déjà à chacune de ces questions, chacun avec ses données, ses filtres et son journal.

Un orchestrateur qui les réunit pose trois risques propres :

- **La jointure.** Chaque module, seul, protège ses RP. Réunir les RH et l'entretien au sujet d'une même personne, ou les commandes et un estimateur, créerait un profil qu'aucun module ne permet seul (art. 8.1 ; clé `lprp_technologie_profilage`).
- **Le résumé croisé.** Un modèle de langage qui fusionne trois réponses peut inventer un lien de cause entre elles (« le retard vient de la panne de la plieuse ») qu'aucune source ne porte, et perdre les citations.
- **L'agrégat qui désigne.** Le service d'entretien compte 4 personnes : un agrégat de durée de réparation pour ce service désigne en pratique ses membres. La règle k = 5 du labo l'interdit déjà au module 3.

Cet ADR est rédigé en tenant compte de la *Loi sur la protection des renseignements personnels dans le secteur privé* (RLRQ, c. P-39.1), état du droit au 1er novembre 2026 (texte consolidé à jour au 15 octobre 2026, date revue le 10 novembre 2026) ; **ceci n'est pas un avis juridique**. Les énoncés juridiques viennent du [registre RH](../hr-legal-sources.md) et du [registre du module 3](../ops-legal-sources.md).

## Décision

### Fins fixées avant la collecte

| Fin | Question type | Domaine | Moteur |
|---|---|---|---|
| D1 `quality_policy` | « Que dit la procédure sur l'étalonnage des pieds à coulisse ? » | Qualité | graphe du module 1 |
| D2 `hr_policy` | « Quelle est la politique de télétravail des cadres ? » | RH | enveloppe RH du module 2 |
| D3 `orders_overview` | « Quelles commandes sont en retard, et pourquoi ? » | commandes | ERP simulé du module 3, nouvelle projection de synthèse |
| D4 `maintenance_overview` | « Quelles machines ont eu le plus d'arrêts correctifs ce trimestre ? » | entretien | GMAO simulée du module 3, nouvelle projection de synthèse |
| D5 `consolidated` | « Portrait de la semaine : commandes et machines » | commandes et entretien | D3 puis D4 |
| L | journal des accès | — | [ADR 0014](0014-ops-access-log.md), nouveau préfixe `direction` |

Toute autre fin est refusée. Aucune fin ne prend une décision au sujet d'une personne (art. 12.1 ; clé `lprp_decision_automatisee`).

### Le code route, le modèle rédige

- Comme au [module 3](0012-ops-architecture-typed-tools.md), **le routage est déterministe** : des règles lexicales sur la question choisissent la ou les fins. Le modèle de langage ne choisit jamais un domaine, un outil ni un argument. Un planificateur choisi par le modèle reste un essai du module 4, hors porte.
- Le filtre de la direction passe **avant** le routage. Il refuse avec un code fermé :
  - `person_join` : la question vise une personne, ou croise deux domaines au sujet d'une personne ou d'un rôle (« les arrêts de la plieuse pendant le quart de tel technicien ») ;
  - `profiling` : classement, rendement, durée ou délai attribué à une personne, à un rôle ou à un groupe de moins de 5 personnes (règle k = 5 du labo), y compris le délai moyen de réparation du service d'entretien ;
  - `restricted_info` : prix, coûts, marges, soumissions (niveau 3, hors des projections) ;
  - `sensitive_out_of_scope` : santé, accident, mesure disciplinaire (niveau 5) ;
  - `out_of_scope` : aucune fin reconnue.
- Une question qui passe le filtre de la direction passe ensuite, pour chaque domaine, par **le filtre de son propre module**, inchangé. Un refus d'un module devient un refus de sa section, avec le motif du module ; les autres sections sont servies.

### Des sections, jamais une fusion

- La réponse est une suite de **sections**, une par domaine routé, dans un ordre fixe (Qualité, RH, commandes, entretien). Chaque section porte ses citations ou ses identifiants de source, et l'en-tête de la section nomme son domaine.
- Aucun texte ne relie deux sections. Le modèle ne reçoit jamais deux domaines dans la même invite. Une phrase de cause entre domaines est donc impossible par construction, et l'oracle d'évaluation la cherche quand même ([ADR 0020](0020-direction-evaluation-protocol.md)).
- D1 et D2 rendent la réponse de leur module, telle quelle.
- D3 et D4 sont des **synthèses calculées par le code** à partir de projections nouvelles et fermées :
  - D3 (`OrdersOverview`) : nombre de commandes par statut ; liste des commandes en retard (identifiant, compte, date promise, date révisée, motif du retard). Le compte est désigné par son libellé, sauf un compte qui est une personne physique (`natural_person: true`), désigné par son identifiant seulement. Aucun contact, aucune note, aucun prix ;
  - D4 (`MaintenanceOverview`) : nombre de bons correctifs par équipement et par code de défaillance, sur une période. Aucun champ de technicien, aucune remarque, **aucune durée** (ni temps d'arrêt ni délai de réparation, à cause de la règle k = 5).
- En mode complet, le modèle rédige le paragraphe d'une seule section à partir du bloc de faits de cette section ; l'étape `verify` retire toute phrase dont un nombre ou un identifiant n'est pas dans le bloc. En mode outils, un gabarit écrit chaque section sans modèle.

### Mêmes enveloppes, même journal

- Chaque section est produite sous l'enveloppe de son module, avec son journal : le module 2 consigne D2, le module 3 n'a rien à consigner pour D3 et D4, qui n'appellent pas ses flux, mais le journal `direction` les consigne.
- Le journal `direction` suit l'[ADR 0014](0014-ops-access-log.md) : une ligne par requête, avec la version, l'identifiant de ligne, l'heure arrondie, le canal (`cli` ou `eval`), les fins routées, le résultat par section et le code de refus. Jamais de texte, d'identifiant de commande ni de compte. Conservation : 90 jours au plus (règle du labo).
- Point d'entrée : `alverno direction ask` (question lue sur l'entrée standard) et `alverno direction eval`. **L'orchestrateur n'est pas exposé par MCP** en v1 : ses sections D3 et D4 contiennent des données de niveau 4.

### Câblage

- Nouveau paquet `src/alverno_lab/agents/direction/` : `route.py` (filtre et routage), `overview.py` (projections et synthèses D3 et D4), `graph.py` (graphe LangGraph `screen → route → sections → verify → assemble`), `prompts.py`.
- Les connecteurs simulés reçoivent deux méthodes de lecture, `orders_overview()` et `maintenance_overview(period)`, qui rendent les projections ci-dessus ; les projections du module 3 ne changent pas.
- Aucune nouvelle dépendance.

## Solutions écartées

- **Un résumé final par le modèle** : il fusionnerait les domaines et pourrait inventer des liens de cause ; il perdrait aussi la trace de la source de chaque phrase.
- **Le modèle choisit les agents (planificateur)** : la sécurité dépendrait de la fiabilité d'un petit modèle ; l'essai reste au module 4.
- **Exposer l'orchestrateur par MCP** : D3 et D4 enverraient des données de niveau 4 au modèle du client ([ADR 0017](0017-mcp-server-read-only.md)).
- **Inclure les temps d'arrêt dans D4** : avec 4 personnes à l'entretien, un temps d'arrêt par période devient un rendement de groupe ; la règle k = 5 l'interdit.
- **Croiser les commandes et l'entretien (« commandes touchées par une panne »)** : aucune donnée fiable ne relie un bon de travail à une commande dans les simulateurs ; le lien serait une inférence. Reporté, avec une nouvelle version de l'EFVP.

## Conséquences

Positives :

- Une question de direction reçoit les réponses des quatre domaines sans qu'aucune règle des modules 1 à 3 ne soit contournée : chaque section passe par son filtre.
- Les sections D3 et D4 sont calculées, donc exactes par construction en mode outils ; le mode complet ne peut qu'en reformuler une à la fois.

Négatives :

- La réponse est une juxtaposition, pas une analyse : la direction tire elle-même les liens entre domaines.
- Le routage lexical manquera des formulations ; le jeu réservé le mesurera, et le résultat sera publié tel quel.
- Le refus `person_join` bloquera aussi des questions légitimes qui nomment un rôle unique (« le directeur de production ») ; c'est un faux blocage accepté, mesuré par `false_block_rate`.

Voir aussi l'[ADR 0017](0017-mcp-server-read-only.md), l'[ADR 0019](0019-docker-local-deployment.md) et l'[ADR 0020](0020-direction-evaluation-protocol.md).

## Précisions de l'étape I2 (2026-11-10)

Ajoutées après l'acceptation, avant tout code `src/` du module 5, pour fixer ce que les jeux `dir_gold` et `dir_holdout` attendent. Elles ne changent aucune décision ci-dessus.

- **Portée lue dans la question.** Un compte est reconnu par son identifiant (`CLI-` suivi de trois chiffres) et un équipement par le sien (`EQ-`, trois lettres, deux chiffres) ; un libellé ou un nom courant (« la plieuse ») ne filtre rien. Le compte filtre D3, l'équipement et la période filtrent D4.
- **Période de D4.** Une année (« en 2025 »), un trimestre (« T3 2026 », « troisième trimestre 2026 ») ou un mois (« octobre 2026 »), bornes comprises. Sans période, D4 couvre les 12 mois qui finissent à la date `as_of` de la GMAO simulée (du 2025-10-31 au 2026-10-30).
- **Commande en retard.** Une commande dont le statut n'est pas `expediee` et dont la date révisée est postérieure à la date promise.
- **Comptes de D3** : `orders.total`, `orders.status.<statut>` (statuts présents seulement), `orders.late`. **Comptes de D4** : `maintenance.corrective` (bons de type `correctif` ouverts dans la période), `maintenance.asset.<équipement>` et `maintenance.failure.<code>` (valeurs non nulles seulement).
- **Schéma d'un item** : `id`, `family`, `question`, `expected_outcome`, `expected_reason`, `expected_purposes` (dans l'ordre fixe des sections), `scope` (`account_id`, `asset_id`, `period` avec `start` et `end`), `pair_id`, `expected_counts` (sous-ensemble des comptes de l'oracle, avec au moins les totaux des fins routées), `must_include`, `must_not_include` et `canaries` (valeurs telles quelles des fixtures).
- **Questions D1 et D2 de `dir_gold`** : reprises telles quelles des jeux de référence des modules 1 et 2 (`q004`, `q008`, `q010`, `q013`, `q015`, `q016` ; `h005`, `h006`, `h015`, `h023`, `h024`, `h027`), avec leurs faits.
- Les identifiants de `dir_gold` vont de `d001` à `d041`, sans `d017` (retiré avant la première version du fichier pour tenir les 6 items D3 du plan).

## Précisions de l'étape I5 (2026-11-11)

Ajoutées avec la première version du code ; elles ne changent aucune décision ci-dessus.

- **Bloc de faits.** Les sections D3 et D4 sont d'abord un bloc de faits écrit par le code (`agents/direction/overview.py`) : un compte par ligne, sous la forme « libellé : nombre », puis la liste des commandes en retard ou les nombres de bons par équipement et par code de défaillance. Le mode outils sert le bloc seul. En mode complet, le modèle reçoit ce seul bloc, jamais la question ni un autre domaine, et écrit un paragraphe placé au-dessus du bloc ; `verify` garde une phrase seulement si chacun de ses nombres et de ses identifiants figure dans le bloc.
- **Sections Qualité et RH.** Elles viennent de `QualityAgent.ask` et de `HrAgent.ask` (canal `cli` ou `eval`), dans les deux modes ; avec `--fake`, ce sont les moteurs simulés de ces modules. Un refus du module devient le refus de sa section, avec son motif ; la section est alors comptée comme non servie.
- **Refus de la direction.** Le texte servi est un message fixe, le même pour les cinq codes ; il ne répète rien de la question et renvoie au RPRP.
- **Journal `direction`.** `privacy/direction_access_log.py` : version, identifiant de ligne, heure arrondie, canal, décision, motif d'un refus, fins routées et résultat de chaque section (`served`, `refused` ou `error`), modèle et durée ; aucun autre champ n'est accepté. Le préfixe `direction` rejoint `hr`, `service` et `maint` dans `logs/access/`, et `alverno access purge` le purge aussi.
- **Commandes.** `alverno direction ask` (question lue sur l'entrée standard, avis de l'art. 8 sur la sortie d'erreur) et `alverno direction eval`.
