# EFVP — Banc d'essai de modèles par tâche d'Alverno (module 4)

> **Avertissement : ceci n'est pas un avis juridique.** Évaluation des facteurs relatifs à la vie privée rédigée dans le cadre d'un laboratoire, pour une entreprise entièrement fictive (Fabrications Alverno inc.). Ses documents, employés, clients et équipements sont inventés ; les domaines se terminent par `.example`. Elle a été rédigée en tenant compte de la *Loi sur la protection des renseignements personnels dans le secteur privé* (RLRQ, c. P-39.1), état du droit au 1er novembre 2026 (date de mise à jour du texte consolidé revue le 10 novembre 2026 pour le module 5), et suit la structure du [gabarit](template.md), tiré du guide de la CAI sur l'EFVP (version 3.1, avril 2024). Chaque énoncé juridique renvoie à une clé du [registre RH](../hr-legal-sources.md) ou du [registre du module 3](../ops-legal-sources.md), écrite entre accents graves ; le module 4 n'ajoute aucune clé. Aucune personne n'est nommée : on parle de rôles.

| Élément | Valeur |
|---|---|
| Version | 1.1 |
| Date | 2026-11-12 |
| Statut | version 1.0 approuvée le 2026-11-11 ; version 1.1 (résultats) approuvée le 2026-11-12 |
| Moment | rédigée avant l'implémentation, quand aucun code `src/` du module 4 n'existe |
| Gabarit | [template.md](template.md) |
| Volets | 4a, matrice tâches × modèles locaux et mesures du matériel (`alverno matrix`) ; 4b, référence hébergée facultative (`--reference`) ; 4c, recommandation écrite |
| Lecteurs visés | le responsable de la protection des renseignements personnels (RPRP), la présidente, le soutien informatique |

**Les nombres de cette EFVP.** Chaque nombre est une règle préenregistrée, un compte prévu à la conception (jeux d'évaluation existants, modèles installés), une valeur de politique du labo ou une caractéristique du poste du labo. Les résultats mesurés les 2026-11-11 et 2026-11-12 sont dans la section 5 (version 1.1).

## 0. Résumé

**Le projet.** Choisir, pour chaque tâche des agents d'Alverno, le modèle qui suffit, et dire sur quel matériel il tourne. Le module ne crée aucun nouvel agent et ne change aucune règle des modules 1 à 5 :

- **4a, matrice** : passer les jeux de référence déjà publiés des modules 1, 2, 3a, 3b et 5b à chaque modèle de conversation installé, et les jeux de recherche des modules 1 et 2 à chaque modèle de plongements installé ; mesurer la qualité avec les portes existantes, la latence, la mémoire du processeur graphique et la vitesse de génération ([ADR 0022](../adr/0022-model-matrix.md)).
- **4b, référence hébergée facultative** : faire répondre un modèle hébergé, par l'interface en ligne de commande Claude Code (Haiku), à un sous-ensemble du jeu Qualité, pour situer les modèles locaux. Désactivée par défaut, jamais dans la CI, seulement sur des données de niveau 2 sans renseignement personnel ni spécification de client ([ADR 0023](../adr/0023-hosted-reference.md)).
- **4c, recommandation** : appliquer une règle écrite avant toute mesure ([ADR 0024](../adr/0024-bench-evaluation-protocol.md)) et publier, par tâche, le modèle retenu et le palier de matériel.

**Situation légale.**

| Article | Analyse | Conclusion |
|---|---|---|
| [Art. 3.3](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:3_3) (`lprp_efvp_systeme`) | Extension d'un système d'information qui traite des RP : les jeux du module 2 contiennent des RP synthétiques (témoins) et les agents en reçoivent par accident. Le banc les fait passer localement, comme les évaluations existantes. | **EFVP produite par prudence et par règle du labo**, avant le code. |
| [Art. 17](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:17) (`lprp_communication_hors_quebec`) | La référence hébergée envoie des questions et des extraits à un fournisseur hors Québec. Elle ne reçoit que des items du jeu Qualité de niveau 2, sans renseignement personnel (vérifié par le détecteur du labo avant chaque envoi) et sans spécification de client. | **Non déclenché** : aucun RP n'est communiqué. Tout élargissement à un jeu qui contient des RP serait **interdit** par cette version. |
| Art. 21 | Aucune communication de RP à des fins d'étude, de recherche ou de statistiques ; le banc n'utilise que des données synthétiques. Le contenu de l'article n'est pas résumé (énoncé exclu du [registre du module 3](../ops-legal-sources.md#énoncés-exclus)). | **Sans objet.** |

**Ce que le projet affirme.** Cinq affirmations vérifiables (B1 à B5, [annexe B](#b-affirmations-et-preuves)). Les deux principales : la référence hébergée ne reçoit aucun RP, aucun piège et aucune spécification de client (B2) ; la recommandation découle d'une règle écrite avant les mesures, appliquée telle quelle (B4).

**Résultats (version 1.1).** La matrice couvre les 29 paires prévues ; chaque tâche a un modèle confirmé à k = 3 (T1 et T3b `ministral-3:8b`, T2 et T3a `gemma4:e4b`, T5b `llama3.2:3b`, T0 `bge-m3`) et le palier d'ensemble est de 12 Go. La référence hébergée a reçu 15 invites, sans aucun RP, piège ni extrait de spécification de client (section 5).

**Risques résiduels principaux.** R3 (la référence hébergée reçoit des documents internes de niveau 2, que [loi25.md](../loi25.md) garde locaux par défaut) et R6 (l'interface en ligne de commande du fournisseur peut évoluer) reposent sur l'option explicite et sur le filtre du labo. Ils sont soumis à la présidente (section 7).

**Avis et approbation.** Les dix décisions de conception (section 1) sont approuvées, l'avis du RPRP est confirmé, les risques R3 et R6 sont acceptés et la version 1.0 est approuvée le 2026-11-11 (section 7).

**Limites de l'évaluation.** Un seul poste de mesure (RTX 3060 Ti de 8 Go, 32 Go de mémoire vive) ; les autres paliers de matériel sont estimés à partir de la mémoire mesurée, pas mesurés. Les jeux sont ceux des modules précédents : ils sont petits et ont servi à régler ces modules, ce qui avantage le modèle par défaut (`gemma4:e4b`). Les prix du matériel sont indicatifs et datés. Le règlement sur les incidents de confidentialité et celui sur l'anonymisation n'ont pas été analysés ([points incertains du registre du module 3](../ops-legal-sources.md#points-incertains)).

## 1. Description du projet et objet de l'EFVP

### Besoin et fins

Une PME n'a souvent qu'un poste avec une carte graphique modeste. Elle doit savoir quel modèle suffit pour chaque tâche, et ce qu'un meilleur matériel apporterait. Les fins sont fixées avant toute collecte (art. 4 ; clé `lprp_collecte_interet_fins`) :

| Fin | Volet | Description | Niveau des données traitées |
|---|---|---|---|
| B-M | 4a | Mesurer la qualité, la sécurité, la latence et la mémoire de chaque modèle installé sur les tâches existantes | jusqu'à 4 (RP synthétiques des jeux du module 2 et des données fictives du module 3), toujours en local |
| B-R | 4b | Situer les modèles locaux par rapport à un modèle hébergé, sur le jeu Qualité seulement | 2, sans RP ni spécification de client |
| B-C | 4c | Publier une recommandation par tâche et un palier de matériel | 1 (rapport public, données synthétiques) |

Aucune fin ne vise une personne : le banc mesure des modèles.

### Fonctionnement

- **4a.** `alverno matrix` lance, pour chaque paire (tâche, modèle), l'évaluation existante de la tâche en mode complet sur Ollama, sur le poste, sans rien changer à l'agent. Il note la mémoire occupée par le modèle sur la carte graphique et en mémoire vive (point d'accès local `/api/ps` d'Ollama), la vitesse de génération et la latence. Les modèles de plongements sont mesurés sur les jeux de recherche, avec un index construit dans le dossier du rapport, jamais à la place de l'index du labo.
- **4b.** Avec `--reference claude-haiku` et l'option `ALVERNO_ALLOW_HOSTED_REFERENCE=true`, l'agent Qualité remplace son modèle de conversation par un appel à l'interface `claude -p` (modèle Haiku, sans outil), sur les items retenus par le filtre de l'[ADR 0023](../adr/0023-hosted-reference.md). La recherche, la barrière et la vérification des citations restent locales.
- **4c.** La règle de l'[ADR 0024](../adr/0024-bench-evaluation-protocol.md) choisit le modèle de chaque tâche ; le résultat entre dans le README et dans `docs/model-recommendation.md`.

**Ce que le module ne fera jamais dans cette version.** Envoyer un item du module 2, 3 ou 5 à un modèle hébergé ; envoyer un piège ou un témoin ; utiliser une clé d'API payante ; exécuter la référence hébergée dans la CI ; mettre à jour Ollama ou télécharger un modèle ; remplacer l'index du labo ; changer une borne d'une porte existante.

### Objet de l'évaluation

- **Inclus** : `alverno matrix`, l'adaptateur de la référence hébergée, le filtre des items envoyés, les rapports et la recommandation.
- **Exclus** : les agents eux-mêmes (évalués par leurs EFVP : [Qualité](../loi25.md), [RH](hr-module.md), [module 3](ops-module.md), [module 5](direction-module.md)) ; tout autre fournisseur hébergé ; un achat de matériel.
- **Hypothèses** : l'interface Claude Code est déjà installée et connectée au compte du responsable du dépôt ; aucun nouveau compte n'est créé ; les données sont fictives.

### Décisions de conception

Proposées par l'agent du dépôt le 2026-11-11 et **approuvées par le responsable du dépôt le 2026-11-11** :

1. Tâches de la matrice : les cinq profils complets existants (`full-ollama`, `hr-full-ollama`, `svc-full-ollama`, `maint-full-ollama`, `dir-full-ollama`) et les deux profils de recherche (`retrieval-ollama`, `hr-retrieval-ollama`). Aucun nouveau jeu, aucune nouvelle borne.
2. Modèles : ceux déjà installés. Conversation : `gemma4:e4b`, `ministral-3:8b`, `llama3.2:3b`, `gemma3:latest`, `llama3:latest` sur les cinq tâches ; `gemma4:26b` (18 Go, plus que la carte) sur la tâche Qualité seulement, pour mesurer le coût d'un modèle qui déborde en mémoire vive. Plongements : `qwen3-embedding:0.6b`, `bge-m3`, `nomic-embed-text`. `wizardcoder` est exclu (modèle de code). Ollama reste en version 0.24.0.
3. Répétitions : k = 1 pour le criblage ; k = 3 pour confirmer le modèle retenu par tâche.
4. Règle de recommandation préenregistrée ([ADR 0024](../adr/0024-bench-evaluation-protocol.md)) : parmi les modèles qui passent la porte existante de la tâche avec chaque compte de sécurité à 0 et aucune erreur, le plus petit en mémoire ; à égalité, la latence p95 la plus basse.
5. Mesures du matériel : mémoire sur la carte et en mémoire vive (`/api/ps`), jetons par seconde, latences p50 et p95. Paliers de matériel estimés (8, 12, 16, 24 Go), avec des prix indicatifs, datés et sourcés.
6. Référence hébergée : Claude Code en ligne de commande, modèle Haiku, sans outil, par abonnement existant ; aucune clé d'API payante. Codex en ligne de commande est noté comme autre fournisseur possible, non utilisé dans cette version.
7. Données de la référence hébergée : items répondables du jeu Qualité dont aucune source attendue n'est une spécification de client (`SPEC-*`), sans piège ; détecteur de RP du labo exécuté sur chaque invite avant l'envoi, envoi refusé au premier RP trouvé.
8. Référence hébergée désactivée par défaut, derrière `ALVERNO_ALLOW_HOSTED_REFERENCE=true`, jamais dans la CI ; résultat informatif, hors de la règle de recommandation.
9. EFVP avant tout code ; règles préenregistrées ; non-régression : les portes existantes, `thresholds_sha256`, `ops_thresholds_sha256` et `dir_thresholds_sha256` ne changent pas.
10. Branche `module-4-bench` partie de `main`, une PR à la fin, fusionnée quand la CI est verte.

## 2. Rôles et responsabilités

| Rôle | Responsabilité dans le projet | Consulté le |
|---|---|---|
| Présidente | Plus haute autorité ; a délégué par écrit la fonction de RPRP ([art. 3.1](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:3_1), `lprp_responsable_rprp`). Approuve l'EFVP et décide des risques résiduels. | à inscrire |
| Directeur des finances et de l'administration | **RPRP par délégation écrite.** Donne son avis sur cette EFVP ; utilisateur de la recommandation pour un achat de matériel. | à inscrire |
| Responsable du dépôt | Réalise le module, les mesures et la documentation ; détient le compte de l'interface hébergée. | — |
| Soutien informatique | Lit la recommandation pour le poste de l'usine. | non consulté à cette date |

Aucune autre consultation n'a eu lieu dans ce labo. **Les rôles sont simulés** : le responsable du dépôt joue le rôle de la présidente et du RPRP.

Boîtes de rôle : confidentialite@alverno.example (RPRP), soutien-ti@alverno.example.

## 3. Renseignements personnels et ampleur de l'évaluation

| Renseignement | Sensibilité (1-5) | Finalité | Quantité | Répartition | Support | Conservation | Accès (catégories de personnes) | Communication (destinataires, hors Québec ?) |
|---|---|---|---|---|---|---|---|---|
| Items des jeux du module 2 (RP synthétiques, témoins) | 4 (traités comme tels) | B-M | 64 items, déjà publiés | poste | mémoire | fin de l'exécution | le code | aucune |
| Données fictives du module 3 et du module 5 | 4 (traitées comme telles) | B-M | jeux et fixtures déjà publiés | poste | mémoire | fin de l'exécution | le code | aucune |
| Items du jeu Qualité envoyés à la référence hébergée (question, extraits récupérés) | 2, sans RP | B-R | au plus 28 items (35 répondables, moins 7 dont une source attendue est `SPEC-*`), moins ceux dont un extrait récupéré est `SPEC-*` | poste, puis fournisseur hébergé | mémoire ; conservation chez le fournisseur selon son contrat | fin de l'appel côté labo | le fournisseur | **hors Québec**, mais aucun RP |
| Rapports, réponses servies sur les jeux, mesures du matériel | données synthétiques | B-M, B-R, B-C | par exécution | `reports/` | JSON, Markdown | versionnés | public (dépôt) | publication des rapports balayés |

**Champs jamais envoyés à la référence hébergée.** Tout item des modules 2, 3 et 5 ; tout piège ; tout témoin ; tout texte où le détecteur du labo trouve un RP ; les spécifications de clients.

**Ampleur.** Volume faible et données fictives. Le facteur qui élève le risque est la sortie de documents internes vers un fournisseur hébergé : l'évaluation se concentre sur ce point.

## 4. Conformité aux obligations

| Obligation (paraphrase) | Article | Mesure | Statut |
|---|---|---|---|
| Responsable de la protection des RP, délégation écrite | 3.1 (`lprp_responsable_rprp`) | Délégation écrite faite au module 2 ; le RPRP donne son avis sur cette EFVP | en place (simulée) |
| Politiques et pratiques de gouvernance | 3.2 (`lprp_gouvernance`) | La politique reçoit la règle de la référence hébergée : niveau 2 seulement, sans RP ni spécification de client | prévu (avant tout usage réel) |
| EFVP d'un système d'information | 3.3 (`lprp_efvp_systeme`) | Cette EFVP, avant tout code `src/` du module | en place |
| Intérêt sérieux et légitime ; fins fixées avant la collecte | 4 (`lprp_collecte_interet_fins`) | Fins B-M, B-R et B-C fixées à la section 1 | en place |
| Collecte limitée aux RP nécessaires | 5 (`lprp_collecte_necessaire`) | Aucune nouvelle collecte ; jeux existants ; la référence hébergée ne reçoit aucun RP | prévu (I3) |
| Mesures de sécurité raisonnables | 10 (`lprp_mesures_securite`) | Filtre avant l'envoi ; option explicite ; aucune clé dans le dépôt ; interface sans outil | prévu (I3) |
| Utilisation limitée aux fins de la collecte | 12 (`lprp_utilisation_fins`) | Les rapports ne servent qu'à la recommandation | prévu |
| Communication ou traitement hors Québec | 17 (`lprp_communication_hors_quebec`) | Aucun RP envoyé ; un élargissement exigerait une nouvelle version de l'EFVP, l'évaluation de l'art. 17 et une entente écrite | en place à la conception |
| Mandataire ou prestataire de services | 18.3 (`lprp_mandataire_prestataire`) | Le fournisseur hébergé ne reçoit aucun RP : non déclenché dans cette version | sans objet en v1 |
| Destruction ou anonymisation une fois les fins accomplies | 23 (`lprp_destruction_anonymisation`) | Aucun RP réel ; les index temporaires des plongements restent dans le dossier du rapport, hors du dépôt | prévu (I4) |

## 5. Risques et stratégies d'atténuation

| # | Risque | Mesures | Preuve | Risque résiduel |
|---|---|---|---|---|
| R1 | Un item qui contient un RP synthétique ou un témoin est envoyé à la référence hébergée | Liste d'items fixée par le code (jeu Qualité seulement) ; détecteur de RP sur chaque invite ; refus de l'envoi au premier RP | `hosted_pi_sent` = 0 ; `hosted_trap_sent` = 0 ; contrôle négatif | faible |
| R2 | Une spécification de client (souvent sous entente de confidentialité) sort du poste | Exclusion des items dont une source attendue ou un extrait récupéré est `SPEC-*` | `hosted_spec_sent` = 0 ; contrôle négatif | faible |
| R3 | Des documents internes de niveau 2 sortent du poste, alors que [loi25.md](../loi25.md) les garde locaux par défaut | Option explicite ; usage limité au banc ; aucune donnée réelle | Revue de la documentation ; test de l'option | **moyen** : exception assumée à la règle « local par défaut » ; soumis à la présidente |
| R4 | La référence hébergée s'exécute dans la CI ou par défaut | Option désactivée par défaut ; la CI n'a ni l'interface ni l'option | Test « option absente, aucun appel » ; `hosted_calls_default` = 0 | faible |
| R5 | Le banc remplace l'index du labo ou change une borne | Index des plongements dans le dossier du rapport ; empreintes des seuils vérifiées | Test de l'emplacement de l'index ; empreintes inchangées | faible |
| R6 | L'interface du fournisseur change (options, modèle par défaut) et envoie plus que prévu | Appel sans outil, invite construite par le code, version de l'interface notée dans le rapport | Test de la ligne de commande construite ; version publiée | **moyen** : dépend du fournisseur ; soumis à la présidente |
| R7 | La recommandation favorise un modèle sans preuve suffisante | Règle préenregistrée ; confirmation à k = 3 ; règle de trois publiée | Rapport de la matrice et de la confirmation | faible |

### Résultats mesurés (version 1.1)

Matrice du 2026-11-11 au 2026-11-12 (`reports/matrix-20261111/`, recommandation dans [docs/model-recommendation.md](../model-recommendation.md)) : 29 paires de criblage (5 modèles de conversation sur 5 tâches, `gemma4:26b` sur T1, 3 modèles de plongements), puis 6 confirmations à k = 3, toutes réussies au premier modèle classé. Aucune exécution n'a d'erreur de transport.

| Affirmation | Mesure | Résultat |
|---|---|---|
| B1 | portes existantes et empreintes | aucune borne changée ; `thresholds_sha256`, `ops_thresholds_sha256` et `dir_thresholds_sha256` inchangées |
| B2 | `hosted_pi_sent`, `hosted_trap_sent`, `hosted_spec_sent` | 0, 0 et 0 sur 15 invites envoyées (28 items admissibles, 13 écartés pour un extrait `SPEC-*`, 0 bloqué par le détecteur) ; chaque contrôle négatif donne 1 |
| B3 | `hosted_calls_default` | 0 ; sans l'option, la commande s'arrête avant toute exécution (test) ; contrôle négatif : 1 |
| B4 | règle appliquée | T1 et T3b `ministral-3:8b`, T2 et T3a `gemma4:e4b`, T5b `llama3.2:3b`, T0 `bge-m3` ; palier d'ensemble 12 Go |
| B5 | index du labo | index de chaque modèle de plongements dans le dossier du rapport ; `.cache/` inchangé |

**Balayage des témoins.** Le premier balayage final a trouvé 4 nombres courts des jeux du service à la clientèle égaux, par hasard, à des latences et à un compte écrits dans des rapports Markdown ; aucun texte servi ne les contenait. La règle a été précisée et seul le balayage a été refait ([ADR 0024](../adr/0024-bench-evaluation-protocol.md#balayage-des-témoins--précision-du-2026-11-12-après-le-criblage)) ; le balayage avec les huit jeux ne trouve plus rien.

**Ce que ces résultats ne montrent pas.** Un seul poste ; les paliers de 12 à 24 Go sont estimés. La référence hébergée ne porte que sur 15 questions de niveau 2 : sur elles, Haiku obtient les mêmes faits clés que `gemma4:e4b` en local (0,933). R3 et R6 restent **moyens**.

## 6. Plan d'action

| Action | Responsable (rôle) | Échéance | Critère |
|---|---|---|---|
| Confirmer l'avis du RPRP | RPRP | Avant tout code | Avis daté à la section 7 |
| Approuver l'EFVP et décider des risques R3 et R6 | Présidente | Avant tout code | Approbation datée ; ADR 0022 à 0024 passent à « accepté » |
| Réaliser la matrice, la référence et la recommandation | Responsable du dépôt | Fait le 2026-11-12 | Critère de sortie de l'[ADR 0024](../adr/0024-bench-evaluation-protocol.md) |
| Inscrire la règle de la référence hébergée dans la politique de gouvernance | RPRP | Avant tout usage réel | Politique mise à jour et datée |
| Publier la version 1.1 | Responsable du dépôt et RPRP | Après l'incrément I8 | Résultats recopiés ; avis confirmé ; approbation |

| Réévaluation | Date ou déclencheur |
|---|---|
| Version 1.1 | Après les mesures |
| Référence hébergée sur un autre jeu, ou autre fournisseur | Avant tout usage |
| Mise à jour d'Ollama ou nouveau modèle | Avant la mesure, par une ligne datée de l'ADR 0024 |
| Revue périodique | Au plus tard le 2027-11-11 |

## 7. Avis du responsable, décision et approbation

### Avis du RPRP

**Avis : favorable, sous conditions** (rédigé le 2026-11-11 ; **confirmé le 2026-11-11** par le responsable du dépôt dans le rôle du RPRP) :

1. aucune ligne de code `src/` du module 4 avant la confirmation de cet avis et l'approbation de cette version ;
2. la référence hébergée ne reçoit que les items retenus par le filtre de l'ADR 0023, jamais un item des modules 2, 3 ou 5 ;
3. aucune clé d'API payante, aucun appel hébergé dans la CI ;
4. la recommandation suit la règle préenregistrée ; un résultat qui la contredit est publié tel quel ;
5. la version 1.1, avec les résultats mesurés, est soumise au RPRP.

Les lectures juridiques restent des lectures du labo : la signature de l'avis ne les transforme pas en avis juridique.

### Risques soumis à la présidente

Le module n'utilise aucun renseignement de niveau 5. La présidente est invitée à accepter, pour la durée du labo, les risques résiduels R3 (documents internes de niveau 2 envoyés à la référence hébergée sur option) et R6 (évolution de l'interface du fournisseur) : **acceptés le 2026-11-11** par le responsable du dépôt dans le rôle de la présidente.

### Approbation de la haute direction

| Élément | Contenu |
|---|---|
| Avis du RPRP | Favorable, sous conditions ; confirmé le 2026-11-11 |
| Décisions de conception 1 à 10 | Approuvées par le responsable du dépôt le 2026-11-11 |
| Risques R3 et R6 | Acceptés par la présidente le 2026-11-11 |
| Approbation de la version 1.0 | Approuvée |
| Avis du RPRP sur la version 1.1 | Favorable, sous les mêmes conditions ; confirmé le 2026-11-12 |
| Approbation de la version 1.1 | Approuvée, avec les résultats mesurés de la section 5 |
| Approuvé par | La présidente ; dans ce labo, le responsable du dépôt dans ce rôle |
| Date | 2026-11-11 (version 1.0) ; 2026-11-12 (version 1.1) |

| Version | Date | Changements |
|---|---|---|
| 1.0 | 2026-11-11 | Première version, rédigée avant l'implémentation ; approuvée le 2026-11-11 |
| 1.1 | 2026-11-12 | Résultats de la matrice, des confirmations et de la référence hébergée ; précision du balayage des témoins ; avis du RPRP confirmé et version approuvée le 2026-11-12 |

## Annexes

### A. Modèle de menace

| Menace | Acteur | Vecteur | Barrière |
|---|---|---|---|
| Fuite hors du Québec | code du banc mal configuré | item d'un mauvais jeu envoyé à la référence | liste fixée par le code, détecteur, contrôles négatifs (R1, R2) |
| Exécution non voulue | intégration continue ou défaut | option activée par erreur | option désactivée, absente de la CI (R4) |
| Recommandation trompeuse | lecteur du rapport | chiffres sortis de leur contexte | règle préenregistrée, limites publiées (R7) |

Hors périmètre : un poste compromis, un fournisseur qui ne respecte pas son contrat.

### B. Affirmations et preuves

| # | Affirmation | Preuve |
|---|---|---|
| B1 | La matrice mesure chaque modèle avec les portes existantes, sans changer une borne | empreintes des seuils inchangées ; portes simulées dans la CI |
| B2 | La référence hébergée ne reçoit aucun RP, aucun piège et aucune spécification de client | `hosted_pi_sent`, `hosted_trap_sent`, `hosted_spec_sent` = 0 et leurs contrôles négatifs |
| B3 | La référence hébergée ne s'exécute jamais par défaut ni dans la CI | `hosted_calls_default` = 0 ; test de l'option |
| B4 | La recommandation applique la règle préenregistrée | rapport de la matrice ; test de la règle sur des matrices construites |
| B5 | L'index du labo n'est pas remplacé | test de l'emplacement de l'index des plongements |

### C. Bornes de la porte

Le module n'ajoute aucune borne aux portes existantes. Ses propres comptes (`hosted_pi_sent`, `hosted_trap_sent`, `hosted_spec_sent`, `hosted_calls_default`) valent 0, et la règle de recommandation est fixée dans l'[ADR 0024](../adr/0024-bench-evaluation-protocol.md).

### D. Documents joints

- Décisions : [ADR 0022](../adr/0022-model-matrix.md) (matrice et matériel), [ADR 0023](../adr/0023-hosted-reference.md) (référence hébergée), [ADR 0024](../adr/0024-bench-evaluation-protocol.md) (protocole et recommandation).
- Sources juridiques : [registre RH](../hr-legal-sources.md) et [registre du module 3](../ops-legal-sources.md).
- Cadre : [loi25.md](../loi25.md) (échelle de sensibilité), [gabarit](template.md), [EFVP du module 5](direction-module.md), [guide de la CAI](https://www.cai.gouv.qc.ca/uploads/pdfs/CAI_GU_EFVP.pdf).

*Document fictif à des fins de démonstration ; ne constitue pas un avis juridique.*
