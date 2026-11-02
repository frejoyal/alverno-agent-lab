# ADR 0012 — Module 3 : routage déterministe et outils typés liés à une fin

- **Statut** : accepté le 2026-11-01, à l'approbation de l'[EFVP du module 3](../efvp/ops-module.md#7-avis-du-responsable-décision-et-approbation) par la présidente (dans ce labo, le responsable du dépôt dans ce rôle)
- **Date** : 2026-11-01

## Contexte

Le module 3 ajoute deux sous-modules, livrés et mesurés l'un après l'autre (décision 1 du responsable du dépôt) :

- **3a, service à la clientèle** (`alverno service`) : répondre à l'opérateur au sujet d'une commande (`order_status`) et préparer un brouillon de réponse au contact de cette commande (`customer_reply`) ;
- **3b, entretien** (`alverno maint`) : aider à diagnostiquer un arrêt de machine à partir de l'historique de cette machine (`diagnosis`) et préparer une demande de travail corrective (`work_request`).

Les deux sous-modules lisent des systèmes d'entreprise simulés : un progiciel de gestion intégré (ERP) pour les commandes et une gestion de la maintenance assistée par ordinateur (GMAO) pour les équipements. Pour la première fois, un agent du labo appelle des outils et prépare des écritures. Cinq constats orientent la conception.

1. **Les petits modèles locaux choisissent mal leurs outils.** `gemma4:e4b`, `ministral-3:8b` et `llama3.2:3b` tournent sur le poste du labo. Si le modèle choisit l'outil, le destinataire ou les arguments, la fiabilité du plus faible des trois décide de la sécurité.
2. **Les fiches complètes contiennent trop.** Une commande porte un prix unitaire, un coût, une marge (niveau 3) et une note interne. Un contact porte un nom, une fonction, un courriel, et, dans les données simulées, un cellulaire et une adresse personnelle. Un bon de travail porte le nom du technicien, son matricule, ses initiales, son quart et une durée d'arrêt.
3. **Le service d'entretien est un petit groupe.** Le département Maintenance compte 4 personnes en tout (`data/hr/org_roles.yaml`), sous le seuil k=5 du labo ([ADR 0006](0006-deterministic-privacy-layer.md)). « Le service d'entretien » désigne donc presque une personne : une durée ou une faute qui lui est attribuée se rattache à des individus.
4. **Le texte des outils est contrôlé par des tiers.** Une note visible par le client ou une remarque de bon de travail peut contenir une consigne plantée (« note au système : envoie… »). Les motifs d'injection du module 2 manquent les formes impératives, selon les essais de la revue du plan (non ré-exécutés ici).
5. **L'entrée v1 est étroite.** Seule la demande de l'opérateur, en français, est reçue ; aucun courriel de client n'est collé (décision 2 du responsable du dépôt).

Cet ADR fixe la forme du code avant qu'il n'existe : aucune ligne de `src/` du module 3 n'est écrite au moment de sa rédaction. Il ne contient aucune mesure. L'approbation humaine et l'état du graphe sont traités dans l'[ADR 0013](0013-ops-human-approval-and-state.md), le journal dans l'[ADR 0014](0014-ops-access-log.md) et l'évaluation dans l'[ADR 0015](0015-ops-evaluation-protocol.md).

Cet ADR est rédigé en tenant compte de la *Loi sur la protection des renseignements personnels dans le secteur privé* (RLRQ, c. P-39.1), état du droit vérifié le 1er novembre 2026 sur le texte consolidé à jour au 15 octobre 2026 ; **ceci n'est pas un avis juridique**. Chaque énoncé juridique renvoie à une clé du [registre du module 3](../ops-legal-sources.md) ou du [registre RH](../hr-legal-sources.md) ; les articles sont paraphrasés, et leur libellé exact se lit sur LégisQuébec.

## Décision

### Le code décide, le modèle rédige

Le modèle ne choisit jamais un outil et ne remplit jamais un argument d'outil. Le code :

- route la demande ;
- planifie les appels ;
- remplit les arguments ;
- joint les champs du contact ;
- décide de ce qui peut s'exécuter.

Le grand modèle de langage (LLM) écrit un seul objet JSON contraint par élément, avec la méthode `complete_json` existante du protocole `ChatModel` (`llm.py`) et sa seule nouvelle tentative de réparation. Le schéma ne contient que des énumérations statiques.

Trois raisons :

1. le chemin `--fake` exerce le même planificateur et le même exécuteur qu'Ollama, donc l'intégration continue (CI) prouve les invariants d'approbation et d'absence d'envoi ;
2. la fiabilité de `llama3.2:3b` ne décide jamais de la sécurité ;
3. chaque plan d'appels se lit dans une table fixe et s'explique au responsable de la protection des renseignements personnels (RPRP).

### Sens des dépendances

```
cli / evals / ops_pipeline
  -> agents/ops, agents/service, agents/maintenance
  -> tools
  -> connectors
```

- Les agents peuvent importer `privacy`.
- `privacy` n'importe rien de nouveau. Les champs du journal sont typés par des motifs d'expression régulière, jamais par `ToolName` ni par les types d'identifiants de `connectors`.
- Le test de couches `test_privacy_package_depends_on_nothing_above_it` (`tests/test_repository_privacy.py`) ajoute `alverno_lab.connectors`, `alverno_lab.tools` et `alverno_lab.ops_pipeline` à sa liste d'importations interdites (incrément I1, avant tout code du module).
- `llm.py`, `agents/quality/*` (dont `gate.py`) et `agents/hr/*` ne changent pas : `tests/test_quality_frozen.py` reste vert sans régénération.

### Identifiants typés

Chaque identifiant porte un préfixe de lettres, des traits d'union et au plus 4 chiffres consécutifs. Ce format vise à ce que le détecteur de l'[ADR 0006](0006-deterministic-privacy-layer.md), dont le repli `numero` caviarde 7 chiffres ou plus, ne prenne aucun identifiant pour un renseignement personnel. Un test vérifie que `detect_pi` ne trouve rien dans aucun identifiant des données simulées.

| Type | Motif |
|---|---|
| `OrderId` | `^CMD-\d{4}-\d{4}$` |
| `AccountId` | `^CLI-\d{3}$` |
| `AssetId` | `^EQ-[A-Z]{3}-\d{2}$` |
| `WorkOrderId` | `^OT-\d{4}-\d{4}$` |
| `ContactRef` | `^CT-\d{3}$` |
| `FailureCode` | `Literal` statique de 16 codes, plus `"INDETERMINE"` |
| `SymptomCode` | `Literal` statique de 8 codes |

Un identifiant sans préfixe (« commande 2026-0412 ») n'est pas reconnu, par choix : la demande est refusée pour `order_not_identified`. Le jeu de référence contient des items de ce type ([ADR 0015](0015-ops-evaluation-protocol.md)).

### Connecteurs simulés et projections liées à une fin

Les connecteurs sont des protocoles. Leurs seules implémentations, `SimErp` et `SimGmao` (`connectors/sim.py`), lisent des fichiers YAML fictifs de `data/eval/ops/` (décision 11 du responsable du dépôt), les valident au chargement et ne font aucun appel réseau. Les projections sont des fonctions pures (`project_order`, `project_work_order`, `project_contact`), testées seules. Chaque classe simulée tient une liste `executed` d'enregistrements `SideEffectRecord(action_id, tool, args_sha256)`, sans valeur.

**ERP (`connectors/erp.py`, protocole `ErpConnector`)**

| Méthode | Retour | Qui l'appelle |
|---|---|---|
| `get_order(order_id)` | `OrderView \| None` | l'exécuteur d'outils (outil `erp.get_order`) |
| `get_order_contact(order_id)` | `ContactView \| None` | le code seulement : le nœud `compose`, puis `guard`, `reguard` et l'en-tête de l'opérateur (voir « Précisions de l'étape I6 ») ; absente du registre d'outils |
| `record_reply_prepared(order_id, draft_sha256, *, token)` | `WriteReceipt` | le nœud `apply` seulement, avec un jeton ([ADR 0013](0013-ops-human-approval-and-state.md)) |

`OrderView` est figé, avec `extra="forbid"`. Ses champs :

- `order_id`, `account_id`, `part_ref`, `qty_ordered`, `qty_shipped` ;
- `status`, parmi `recue`, `planifiee`, `en_production`, `en_finition`, `expediee_partielle`, `expediee` et `en_attente_client` ;
- `promised_date`, `revised_date` (facultatif), `carrier_code` (facultatif), `tracking_ref` (facultatif), `delay_reason_code` (facultatif) ;
- `customer_visible_note` (facultatif), de type `QuarantinedText` : un texte qui a passé la quarantaine.

**Exclus de `OrderView`** : le libellé du compte, le prix unitaire, le coût, la marge, le contact et la note interne. La note interne n'atteint jamais une invite.

`ContactView` contient `contact_ref`, `account_id`, `account_label`, `natural_person` (vrai pour une entreprise individuelle), `function`, `civility`, `surname`, `given_name` et `business_email`. **Exclus** : tout téléphone et toute adresse.

**GMAO (`connectors/gmao.py`, protocole `GmaoConnector`)**

| Méthode | Retour |
|---|---|
| `get_asset(asset_id)` | `AssetView \| None` |
| `list_work_orders(asset_id, symptom_code, since_days=548, limit=8)` | `tuple[WorkOrderView, ...]` |
| `failure_catalog()` | `tuple[FailureCodeView, ...]` |
| `create_work_request(req, *, token)` | `WriteReceipt` |

`WorkOrderView` contient `wo_id`, `asset_id`, `opened_on` (une date seulement, jamais une heure ni un quart), `kind`, `symptom_code`, `failure_code`, `action_code` et `remark` (facultatif, de type `QuarantinedText`).

**Exclus de `WorkOrderView`** : le nom du technicien, son matricule, ses initiales, son identifiant de connexion, le quart, l'heure d'ouverture et la durée d'arrêt (`downtime_min`). Les notes marquées `safety: true` (blessures, niveau 5) sont retirées par la projection. Le catalogue des défaillances compte les catégories mécanique, électrique, hydraulique, pneumatique, commande, usure et externe ; il n'a **aucune catégorie « erreur humaine »**.

Ces exclusions appliquent la collecte limitée au nécessaire ([art. 5](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:5), `lprp_collecte_necessaire`) et la règle du labo k=5 : aucune donnée qui rattacherait une durée, une faute ou un résultat au service d'entretien ne sort du connecteur. Le labo n'offre aucune analyse par personne ni par service, ce qui écarte le profilage ([art. 8.1](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:8_1), `lprp_technologie_profilage`) ; une demande de délai moyen de réparation (MTTR), de durée ou de classement est refusée pour `profiling`.

### Le contact est lu par le code seulement

Un contact B2B est une personne physique identifiable. Le labo le traite donc comme un renseignement personnel (RP) ([art. 2](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:2), `lprp_definition_rp`) de niveau 4. L'art. 1, al. 5 exclut des sections II et III de la loi certains renseignements liés à l'exercice d'une fonction dans une entreprise ([art. 1](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:1), `lprp_coordonnees_affaires`). Sa portée pour ce module n'est pas tranchée (`lprp_coordonnees_affaires_portee`), et **le labo ne s'appuie pas sur cette exclusion** : la conception est la même que si elle n'existait pas.

- Seul le nœud `compose` appelle `get_order_contact` pour écrire le brouillon, avec l'identifiant de la commande déjà dans le périmètre (lectures de contrôle et d'affichage : voir « Précisions de l'étape I6 »). L'accès suit le besoin de la tâche : composer l'en-tête et la salutation du brouillon ([art. 20](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:20), `lprp_acces_besoin_fonctions`).
- `ContactView` n'est jamais stocké dans l'état du graphe ([ADR 0013](0013-ops-human-approval-and-state.md)) ; seul le texte composé est gardé.
- Aucun champ du contact, et aucun libellé de compte, n'entre dans une invite, un journal ou un rapport.
- Le libellé d'un compte qui est une entreprise individuelle (`natural_person: true`) est traité comme un nom : jamais dans une invite, et la salutation devient un « Bonjour, » neutre.

### Registre d'outils et plans fixes

`tools/registry.py` déclare :

- `ToolName`, un `Literal` de 6 noms : `erp.get_order`, `gmao.get_asset`, `gmao.list_work_orders`, `gmao.failure_catalog`, `erp.record_reply_prepared` et `gmao.create_work_request` ;
- `ToolKind`, un `Literal` de 2 sortes : `read` et `write_internal`. **Aucune sorte « envoi » n'existe** ;
- `ToolSpec(name, kind, args_model, purpose, max_level)`, où `args_model` est un modèle pydantic avec `extra="forbid"` et `purpose` la fin (`Flow`) qui justifie l'outil.

`FLOW_PLANS` est une table fixe. Le planificateur la lit ; le chargeur du jeu de référence ne la lit jamais, pour que la métrique des appels attendus ne soit pas une tautologie ([ADR 0015](0015-ops-evaluation-protocol.md)).

| Fin | Outils de lecture |
|---|---|
| `order_status` | `erp.get_order` |
| `customer_reply` | `erp.get_order` |
| `diagnosis` | `gmao.get_asset`, `gmao.list_work_orders`, `gmao.failure_catalog` |
| `work_request` | les mêmes que `diagnosis` |

`ToolExecutor(erp, gmao, authority).call(name, args, *, token=None)` :

- valide `args` avec le modèle d'arguments ; un identifiant invalide donne un résultat `ok=False`, pas une exception ;
- pour une écriture, exige un jeton d'approbation valide pour ces arguments exacts (règle détaillée dans l'[ADR 0013](0013-ops-human-approval-and-state.md)) ;
- ajoute un `ToolCallRecord(tool, kind, args_sha256, approved, executed)`, sans aucune valeur.

Tests structurels :

- aucun nom de `ToolKind` ni de `ToolName` ne contient « send », « mail » ou « email » ;
- le registre compte exactement 2 outils d'écriture, et seuls les nœuds `apply` les référencent ;
- `connectors/` et `tools/` n'importent ni `httpx`, ni `smtplib`, ni `socket`, ni `urllib`.

### Deux écritures simulées structurées, aucun envoi

Chaque sous-module a une seule écriture interne simulée, dont tous les arguments sont des identifiants, des codes fermés ou une empreinte (décision 3 du responsable du dépôt) :

| Sous-module | Écriture | Arguments |
|---|---|---|
| 3a | `erp.record_reply_prepared` | `order_id`, `draft_sha256` |
| 3b | `gmao.create_work_request` | `asset_id`, `symptom_code`, `failure_code`, `cited_wo_ids` |

Aucune écriture ne porte de texte libre : rien de ce que le modèle ou l'opérateur a rédigé n'est conservé dans un système simulé. L'approbation protège donc un effet réel, sans créer de dépôt de texte.

Le module n'envoie rien. Après approbation, le texte approuvé est affiché une fois, et l'opérateur l'envoie lui-même depuis sa propre boîte. Une demande comme « Envoie-le directement » n'est pas refusée : elle donne quand même un brouillon retenu à l'approbation, avec un signalement. Tout envoi (courriel, Teams, portail, SMTP, OAuth) est hors périmètre jusqu'au module 6.

Répondre au contact de la commande au sujet de sa propre commande n'est pas, selon la lecture du labo, une communication à un tiers ([art. 13](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:13), `lprp_communication_tiers`). Une divulgation à un autre compte qui contient un RP (son contact, ou une commande reliée à un contact nommé) en serait une, non autorisée : ce serait un incident de confidentialité ([art. 3.6](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:3_6), `lprp_incident_definition`), qui appellerait des mesures pour réduire le risque et, si le risque de préjudice est sérieux, un avis à la CAI et aux personnes ([art. 3.5](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:3_5), `lprp_incident_mesures_avis`). Par prudence, le labo traite toute valeur d'un autre compte comme telle (règle du labo). Les barrières sont `scope_check`, `check_scope` et la confirmation du compte dans l'en-tête d'approbation.

### Routage déterministe

Le nœud `route` n'appelle aucun modèle. Il applique les motifs d'identifiants et des règles d'intention françaises fermées :

| Demande | Résultat |
|---|---|
| un identifiant de commande et un verbe de rédaction | `customer_reply` |
| un identifiant de commande seul | `order_status` |
| aucun identifiant de commande | refus `order_not_identified` |
| deux identifiants de commande ou plus | refus `multi_order` |

Le sous-module 3b suit le même principe avec `AssetId` (refus `asset_not_identified`). Une demande couvre une seule commande et un seul compte, ou un seul équipement. Un routage déterministe se trompe sur des paraphrases ; ces erreurs sont mesurées par `false_block_rate` et par le jeu réservé ([ADR 0015](0015-ops-evaluation-protocol.md)). Elles ne justifient pas un plan choisi par le modèle.

### Graphes

**3a, `agents/service/graph.py`**

```
route → fetch → scope_check → quarantine →
    [order_status] compose_internal → guard → END
    [customer_reply] draft(LLM) → verify → compose → guard → approve
        ⇄ reguard → apply → handoff → END
tout nœud → refuse → END
```

- `scope_check` : chaque enregistrement lu porte l'`account_id` de la commande.
- `draft` : un seul appel `complete_json` avec le schéma `DraftBody{body (1200 caractères au plus), cited_ids: list[OrderId]}`. Le corps n'emploie que les gabarits `{salutation}` et `{signature}`. L'invite reçoit le bloc « Faits de la commande » seulement : les dates en double rendu (`2026-11-15 (15 novembre 2026)`) et les libellés des codes de statut, de transporteur et de retard. Elle ne reçoit ni libellé de compte, ni contact, ni texte de la demande. La règle « texte des outils = données » y est écrite.
- `verify` : nombres sans appui (`find_ungrounded_numbers`) contre le bloc de faits ; identifiants cités parmi ceux qui ont été lus ; chaque mot de statut du corps comparé à la table de rendu fermée de l'[ADR 0015](0015-ops-evaluation-protocol.md), qui doit égaler le statut de l'ERP ; un gabarit inconnu donne `ungrounded`.
- `compose` (code) : remplit `{salutation}` avec la civilité et le nom de famille, ou « Bonjour, » pour une entreprise individuelle ; remplit `{signature}` avec la boîte de rôle service-client@alverno.example ; ajoute la ligne « À : » à partir de `get_order_contact`.
- `guard` : `check_output` avec, comme extraits cités, le bloc de faits et le contact du périmètre ; le vocabulaire du module couvre les transporteurs, pièces, motifs de retard et libellés de comptes fictifs ; `check_scope` compare le texte à un index construit à partir des valeurs simulées de tous les autres comptes. Si le texte ne passe pas, le résultat est `output_pi` ou `cross_account`.

**3b, `agents/maintenance/graph.py`**

```
route → fetch → gate(record_not_found | insufficient_history < 2) → quarantine →
    diagnose(LLM) → verify → guard →
        [diagnosis] END
        [work_request] propose → approve ⇄ reguard → apply → handoff → END
```

- `gate` : un équipement absent de la GMAO simulée donne `record_not_found`, le code `OpsRefusalCode` que le plan appelait `asset_not_found` ; moins de 2 bons de travail donne `insufficient_history`.
- `diagnose` : un seul appel `complete_json` avec le schéma `Diagnosis{codes: list[FailureCode] (3 au plus), cited_wo_ids (4 au plus), next_checks (4 au plus), confidence}`, sans union. L'invite contient au plus 8 lignes d'historique de cet équipement, filtrées par symptôme, au format `wo_id | date | kind | symptom | failure | action | remark`. Elle ne contient pas le classement BM25.
- `verify` : retire les identifiants cités qui n'ont pas été lus ; un code qu'aucun bon cité ne porte devient `INDETERMINE`.
- `guard` est strict : `check_output`, tout `NOM` ou `MATRICULE`, et la règle des petits rôles (un rôle avec une durée, une faute ou un résultat : réponse retenue). Une réponse parle de la machine, jamais du rendement du service.
- `rank_failure_codes(symptom, work_orders)` est une base de comparaison BM25 pure, utilisée en mode `tools` et publiée à côté du LLM.

En mode `tools`, aucun `ChatModel` n'est appelé : un `TemplateDrafter` écrit les brouillons et le classement BM25 donne le diagnostic.

### Injection : une sécurité tirée de la structure

La sécurité contre l'injection ne repose pas sur le rappel d'un détecteur. Elle tient à la structure :

- le code choisit les outils, le destinataire et les arguments ; un texte d'outil ne peut rien choisir ;
- la note interne de l'ERP n'atteint jamais une invite ;
- les écritures n'acceptent que des arguments en énumération fermée ou des identifiants validés ;
- aucun outil d'envoi n'existe.

La quarantaine (`privacy/quarantine.py`, `scrub_tool_text`) est une défense en profondeur : elle applique les motifs d'injection partagés, de nouveaux motifs d'action (`privacy/ops_injection.py` : impératifs « crée », « marque », « envoie », « ajoute », « supprime » suivis de « bon », « commande » ou « courriel » ; « note au système » ; « à cette adresse »), le motif des secrets, `detect_pi` et une règle sur les initiales et les identifiants de connexion. Les métriques comptent les phrases-marqueurs plantées dans les sorties servies et dans les arguments proposés, que la quarantaine se soit déclenchée ou non ([ADR 0015](0015-ops-evaluation-protocol.md)).

### Outils natifs d'Ollama et planificateur choisi par le modèle : reportés au module 4

Un planificateur choisi par le modèle (une étape `PlannerStep` par `complete_json`, ou les outils natifs d'Ollama derrière un nouveau protocole `ToolChatModel`) n'entre dans aucune porte du module 3. C'est une comparaison de banc d'essai facultative du module 4 (incrément I11). Elle passerait par le même exécuteur, les mêmes jetons et les mêmes comptes de sécurité.

### Câblage

- `ops_pipeline.py` : `load_ops_context(settings, module)` construit le `PrivacyContext` à partir de la liste d'exclusion, de `data/eval/ops/vocabulary.yaml`, des boîtes de rôle et de `OrgRoles`. `check_ops_local` reprend la mesure R14 de l'[EFVP RH](../efvp/hr-module.md) (refus de démarrer avec l'hôte distant ou un modèle infonuagique) : ni `allow_remote_ollama`, ni modèle infonuagique, et la vérification `MODEL_PATTERN`. `build_service_agent` et `build_maint_agent` réutilisent `pipeline.make_chat`.
- `config.py` : `for_module` accepte `"service"` et `"maint"` ; les branches `quality` et `hr` restent identiques à l'octet près (un test le vérifie). Nouveaux champs : `ops_erp_path`, `ops_gmao_path`, `ops_vocabulary_path`, `svc_eval_path`, `maint_eval_path`, `ops_max_edit_rounds` (2, au plus 2), `ops_max_work_orders` (8, au plus 12) et `ops_history_days` (548).
- Les connecteurs simulés et l'inférence locale ne communiquent rien hors du Québec ([art. 17](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:17), `lprp_communication_hors_quebec`). Un connecteur réel, ou infonuagique, exigerait une évaluation selon cet article, un nouvel ADR et une nouvelle version de l'EFVP.
- Aucune fonction ne sert à la prospection ni à la vente additionnelle : une telle demande est refusée pour `purpose_drift` ([art. 12](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:12), `lprp_utilisation_fins`).

### Précisions de l'étape I6 (2026-11-02)

Ces précisions décrivent le câblage livré à l'étape I6 ; elles ne changent aucune règle de confidentialité.

- **Lectures du contact par le code.** Outre `compose`, trois étapes lisent `get_order_contact` par l'identifiant de la commande déjà dans le périmètre, en mémoire seulement : `guard` et `reguard` (le contact rendu est un extrait cité de `check_output`, sans quoi le nom du destinataire serait pris pour un RP non appuyé), et l'en-tête de l'opérateur (libellé du compte et fonction du destinataire, [ADR 0013](0013-ops-human-approval-and-state.md)). Aucune ne stocke `ContactView` dans l'état, une trace, le journal ou un rapport ; aucune n'atteint une invite.
- **Règle d'écho de `check_output` en 3a.** `guard` et `reguard` passent une liste vide de valeurs brutes (`raw_values`) : la règle d'écho ne s'applique pas en 3a. Le rédacteur ne reçoit jamais la demande (bloc de faits seulement), et une demande nomme souvent le contact du périmètre, que le brouillon doit nommer : un écho sans exception pour les extraits cités retiendrait ces brouillons. Les autres règles restent : RP détecté hors extraits, gabarit, nom propre inconnu, puis `check_scope` avant tout. Un écho filtré par les extraits cités pourra être ajouté avec le mode `full` (étape I7) s'il est mesuré.
- **Texte approuvé.** `alverno service ask` montre le brouillon avec son en-tête avant la décision, puis, après l'approbation, affiche le texte approuvé une fois, dans un bloc à copier, avec la mention que rien n'a été envoyé.
- **Mots d'un libellé.** Dans l'index de `check_scope`, un trait d'union et une espace séparent les mêmes mots d'une valeur : un libellé d'un autre compte, ou l'un de ses mots à trait d'union, écrit avec une espace au lieu du trait d'union est reconnu.

### Aucune nouvelle dépendance

Versions installées, relevées pendant la conception : `langgraph` 1.2.19 et `langgraph-checkpoint` 4.3.1. Elles fournissent `interrupt`, `Command`, `InMemorySaver.delete_thread` et `JsonPlusSerializer`. `pydantic`, PyYAML, `langsmith` (`tracing_context`) et `rank-bm25` sont déjà des dépendances. Le plancher `langgraph>=1.0` de `pyproject.toml` ne change pas.

## Solutions écartées

- **Un agent ReAct (`create_react_agent`, `ToolNode`) ou un planificateur choisi par le modèle dans une porte** : la sécurité dépendrait du choix d'outil du plus petit modèle, et le chemin `--fake` ne couvrirait plus le choix d'outils du chemin Ollama.
- **Les outils natifs d'Ollama dans une porte** : même raison ; reportés à une comparaison de banc d'essai du module 4.
- **`langchain`, `langchain_ollama` ou le client `ollama`** : des dépendances de plus pour une fonction que `complete_json` remplit déjà.
- **Un routage par le LLM** : non reproductible et sensible à l'injection, comme un filtre LLM ([ADR 0006](0006-deterministic-privacy-layer.md)).
- **Donner la fiche complète au modèle et lui demander d'ignorer les prix ou le contact** : une consigne n'est pas une barrière ; la projection en est une.
- **Un outil d'envoi, même simulé** : il créerait une sorte « envoi » dans le registre, et rien ne distinguerait plus un envoi simulé d'un envoi réel au module 6.
- **Une écriture avec texte libre (note de suivi dans l'ERP, description de la demande de travail)** : elle conserverait du texte rédigé par le modèle ou l'opérateur, donc un dépôt de RP possible.
- **Accepter les identifiants sans préfixe** : un nombre nu (« 2026-0412 ») se confond avec une quantité, une date ou un autre numéro, et le routage devrait deviner de quoi il s'agit.
- **S'appuyer sur l'exclusion des coordonnées d'affaires (art. 1, al. 5)** : sa portée n'est pas tranchée (`lprp_coordonnees_affaires_portee`).
- **Une catégorie « erreur humaine » au catalogue des défaillances** : elle attribuerait une faute à un groupe de moins de 5 personnes.
- **Une recherche augmentée (RAG) sur les manuels d'équipement et des plongements** : hors du périmètre v1.
- **Des connecteurs réels ou infonuagiques** : ils exigeraient une évaluation selon l'art. 17 et un nouvel ADR.

## Conséquences

Positives :

- Les invariants « aucun envoi », « aucune écriture sans approbation » et « aucun contact dans une invite » se vérifient par des tests structurels et par le chemin `--fake`, sans réseau.
- Le RPRP peut lire chaque plan d'appels dans une table fixe.
- Les champs exclus ne quittent jamais le connecteur : aucune invite, aucun journal et aucun rapport ne peut les contenir.
- Les modules 1 et 2 ne changent pas, et `tests/test_quality_frozen.py` le prouve.

Négatives :

- Le routage déterministe refuse des paraphrases légitimes et les identifiants sans préfixe ; les faux blocages sont mesurés et publiés, pas corrigés par un plan choisi par le modèle.
- Sans durée, sans quart et sans technicien, le diagnostic perd de l'information. Cette perte de rappel est acceptée et mesurée.
- L'absence d'envoi laisse l'opérateur copier le texte approuvé à la main ; une erreur de destinataire à cette étape reste possible, et l'EFVP l'inscrit comme risque résiduel.
- La v1 est étroite : pas de courriel de client collé, pas d'anglais, pas de prix ni de réclamation. Une version 3a.2 qui accepterait des courriels exigerait une nouvelle version de l'EFVP.
- Le choix d'outils par le modèle n'est pas mesuré dans le module 3 ; ce sera le rôle du module 4.

Voir aussi l'[EFVP du module 3](../efvp/ops-module.md), le [registre juridique du module 3](../ops-legal-sources.md) et l'ADR 0016 (calibration de la porte, à venir, écrit après la première mesure `--fake`).
