# ADR 0014 — Journal des accès du module 3 : un enregistrement par événement, la décision avant l'effet

- **Statut** : accepté le 2026-11-01, à l'approbation de l'[EFVP du module 3](../efvp/ops-module.md#7-avis-du-responsable-décision-et-approbation) par la présidente (dans ce labo, le responsable du dépôt dans ce rôle)
- **Date** : 2026-11-01

## Contexte

L'[ADR 0008](0008-minimal-access-log.md) a fixé le journal des accès du module RH : un enregistrement figé par demande, aucun texte et aucune empreinte de la question, une conservation de 90 jours, et aucune réponse quand le journal est indisponible. Le module 3 (sous-modules 3a, service à la clientèle, et 3b, entretien) garde ces règles. Trois choses changent toutefois.

- **Une demande produit plusieurs événements.** Les flux `customer_reply` (3a) et `work_request` (3b) passent par une approbation humaine ([ADR 0013](0013-ops-human-approval-and-state.md)) : le brouillon est présenté, l'opérateur décide, un brouillon modifié est présenté de nouveau, puis la demande se termine. Il faut relier ces enregistrements entre eux sans relier le journal au texte.
- **Une décision humaine précède un effet.** Chaque flux avec approbation se termine par une écriture simulée, `erp.record_reply_prepared` ou `gmao.create_work_request` ([ADR 0012](0012-ops-architecture-typed-tools.md)). Le journal doit montrer qu'une décision consignée précède chaque effet (affirmations G3 et G7 de l'[EFVP du module 3](../efvp/ops-module.md)).
- **Le journal est un renseignement personnel de niveau 4.** Dans l'ERP simulé, un numéro de commande est relié à un contact nommé. Un renseignement qui permet d'identifier une personne physique, même indirectement, est un renseignement personnel (RP) (art. 2 ; clé `lprp_definition_rp`). Le labo classe donc le journal au niveau 4 de son échelle ([docs/loi25.md](../loi25.md)), comme le journal RH.

Les exigences légales sont celles de l'ADR 0008, reprises par clé du [registre du module 3](../ops-legal-sources.md) :

- les fins sont fixées avant la collecte (art. 4 ; clé `lprp_collecte_interet_fins`), et la collecte se limite à ce qui est nécessaire à ces fins (art. 5 ; clé `lprp_collecte_necessaire`) ;
- les mesures de sécurité tiennent compte notamment de la sensibilité, de la finalité, de la quantité, de la répartition et du support des RP (art. 10 ; clé `lprp_mesures_securite`) ;
- dans l'entreprise, une personne n'a accès à un RP que s'il est nécessaire à ses fonctions (art. 20 ; clé `lprp_acces_besoin_fonctions`) ;
- un RP est détruit ou anonymisé une fois ses fins accomplies (art. 23 ; clé `lprp_destruction_anonymisation`) ;
- l'entreprise tient un registre des incidents de confidentialité (art. 3.8 ; clé `lprp_registre_incidents`) ; le règlement qui en précise le contenu n'a pas été consulté, et cet ADR n'en résume rien ;
- la personne concernée peut demander l'accès aux RP que l'entreprise détient sur elle, et la rectification d'un RP inexact, incomplet ou équivoque, ou dont la collecte, la communication ou la conservation n'est pas autorisée par la loi (art. 27 et 28 ; clés `lprp_acces_communication` et `lprp_rectification`).

Cet ADR est rédigé en tenant compte de la *Loi sur la protection des renseignements personnels dans le secteur privé* (RLRQ, c. P-39.1), état du droit au 1er novembre 2026 (texte consolidé à jour au 15 octobre 2026) ; **ceci n'est pas un avis juridique**. Les articles sont paraphrasés ; leur libellé exact se lit sur LégisQuébec : [art. 2](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:2), [3.8](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:3_8), [4](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:4), [5](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:5), [10](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:10), [20](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:20), [23](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:23), [27](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:27) et [28](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:28). Fabrications Alverno inc., ses comptes, ses commandes et ses équipements sont fictifs.

## Décision

### Finalités, fixées avant la collecte

Le journal du module 3 sert à trois fins, et à aucune autre (fin L de l'EFVP) :

1. **Compter les décisions et les refus** : pour chaque requête, le flux, l'événement, le motif de refus et les identifiants consultés.
2. **Soutenir l'évaluation d'un incident** : dater une requête, savoir quelle commande, quel compte ou quel équipement elle visait, quels outils ont été appelés et quels types de RP ont été retirés de la demande. Les incidents prévisibles du module 3 sont décrits dans l'EFVP : une réponse envoyée à un autre compte ou au mauvais destinataire, un artéfact qui contient un témoin, un accès non autorisé à `logs/`.
3. **Rendre des comptes sur l'approbation** : montrer que chaque écriture simulée suit une décision humaine consignée sur les mêmes arguments, et vérifier le bon fonctionnement de l'outil (modèle, durée, erreurs).

Comme le journal RH, il ne conserve ni la demande ni le texte servi : il ne peut pas démontrer qu'un brouillon était exempt de RP. Cette garantie repose sur la garde de sortie et sur les comptes de l'[ADR 0015](0015-ops-evaluation-protocol.md) (`answer_leak_count`, `cross_account_leaks`).

### Un schéma propre au module 3, le schéma RH inchangé

Le module 3 reçoit un nouveau modèle, `OpsAccessLogRecord` (pydantic, figé, `extra="forbid"`, `v: Literal[1]`). Le modèle RH `AccessLogRecord` de l'ADR 0008 ne change pas, octet pour octet. Les types des champs sont des motifs (expressions régulières), jamais les types `ToolName` ou les types d'identifiants de `connectors/` : le paquet `privacy/` n'importe rien de nouveau ([ADR 0012](0012-ops-architecture-typed-tools.md)).

| Champ | Type et contrainte | Contenu | Finalité |
|---|---|---|---|
| `v` | `Literal[1]` | version du schéma | structure du fichier |
| `module` | `Literal["service", "maint"]` | sous-module 3a ou 3b | structure du fichier |
| `event_id` | `^[0-9a-f]{32}$` | identifiant aléatoire (uuid4) de l'enregistrement, créé par `AccessLog`, comme aujourd'hui | 2 : désigner une ligne précise |
| `draft_id` | `^[0-9a-f]{32}$` ou vide | identifiant aléatoire (uuid4) du brouillon, sans lien avec son texte ; relie les enregistrements d'une même requête (voir plus bas) | 2 et 3 |
| `ts` | date et heure UTC, **arrondies à l'heure** | même règle que l'ADR 0008 | 1 et 2 |
| `channel` | `Literal["cli", "eval"]` | ligne de commande ou évaluation | 1 : ne pas compter les évaluations comme de l'usage |
| `flow` | `order_status`, `customer_reply`, `diagnosis`, `work_request`, ou vide | flux choisi par le routage ; vide quand le filtrage refuse la demande avant le routage | 1 |
| `event` | une des 10 valeurs du tableau suivant | événement consigné | 1, 2 et 3 |
| `reason` | `OpsRefusalCode` ou vide | code de refus ou de retenue | 1 |
| `tool_calls` | tuple de noms, chacun `^[a-z]+\.[a-z_]+$` | outils appelés depuis l'enregistrement précédent de la requête ; le nom seulement, jamais les arguments | 2 et 3 |
| `entity_refs` | tuple d'au plus 20 identifiants, chacun `^CMD-\d{4}-\d{4}$`, `^EQ-[A-Z]{3}-\d{2}$` ou `^OT-\d{4}-\d{4}$` | commandes, équipements et bons de travail lus ou cités | 2 |
| `account_ref` | `^CLI-\d{3}$` ou vide | compte de la commande en portée, lu dans l'ERP après `scope_check` ; jamais un compte tiré du texte de la demande ; toujours vide en 3b | 2 |
| `pi_types`, `pi_count` | types et nombre | types de RP retirés de la demande de l'opérateur et leur nombre, jamais leur valeur | 2 ; retirés au module 6, comme en RH |
| `quarantine_flags` | tuple de codes, chacun `^[a-z_]+$` | codes levés par la quarantaine du texte des outils, jamais le texte | 2 |
| `action_tool` | `^[a-z]+\.[a-z_]+$` ou vide | outil d'écriture de l'action proposée | 3 |
| `action_args_sha256` | `^[0-9a-f]{64}$` ou vide | empreinte SHA-256 du JSON canonique des arguments de l'action (voir plus bas) | 3 |
| `executed_external_send` | `Literal[False]` | toujours faux : aucun envoi n'existe | 3 |
| `edit_round` | entier de 0 à 2 | tour de modification ; la limite vient de `ops_max_edit_rounds` ([ADR 0013](0013-ops-human-approval-and-state.md)) | 3 |
| `model` | texte ou vide | nom du modèle de langage appelé depuis l'enregistrement précédent de la requête ; vide si aucun ne l'a été, par exemple en mode outils ou sur un enregistrement de décision | 3 |
| `latency_ms` | entier | durée depuis l'enregistrement précédent de la requête, ou depuis son début | 3 |
| `approver` | `Literal["operator"]` ou vide | `"operator"` sur les enregistrements de décision (`approved`, `edit_submitted`, `rejected`), vide ailleurs | 3 |
| `actor` | `None` | toujours vide dans ce module (voir plus bas) | aucune dans ce module |

`entity_refs` n'accepte pas les références de contact (`CT-xxx`) : le numéro de commande suffit aux fins 2 et 3, et la référence du contact n'ajouterait qu'un lien de plus vers une personne (art. 5 ; clé `lprp_collecte_necessaire`).

### Événements

| `event` | Écrit quand | Flux |
|---|---|---|
| `answered` | un texte composé a passé la garde ; avant son affichage | `order_status`, `diagnosis` |
| `refused` | refus du filtrage, du routage, du dossier (`record_not_found`, `insufficient_history`) ou de la portée, avant tout texte composé ; avant l'affichage du message de refus | tous |
| `withheld` | un texte composé est retenu par la vérification, la garde ou la revérification d'une modification (`reguard`) ; avant l'affichage du message de refus | tous |
| `drafted` | un brouillon a atteint l'interruption ; lu par `graph.get_state`, écrit avant son affichage ; `edit_round` vaut 0 | `customer_reply`, `work_request` |
| `approved` | l'opérateur approuve ; avant la création du jeton et avant la reprise du graphe | idem |
| `edit_submitted` | l'opérateur soumet une modification ; avant la reprise | idem |
| `re_presented` | la modification a passé `reguard` et le graphe présente une nouvelle interruption ; avant son affichage | idem |
| `rejected` | l'opérateur rejette, ou sa décision est invalide ; avant la reprise | idem |
| `abandoned` | fin de l'entrée (EOF) ou Ctrl-C pendant qu'un brouillon attend une décision | idem |
| `error` | une erreur échappe au graphe ou à un connecteur ; avant que l'erreur soit relancée | tous |

Précisions :

- `withheld` porte le code `ungrounded`, `output_pi`, `cross_account` ou `invalid_args` ; `rejected` porte `approval_rejected` (rejet) ou `invalid_decision` (décision invalide) ; `error` porte `connector_unavailable` quand un connecteur simulé échoue.
- L'enveloppe valide la décision de l'opérateur avec le modèle `ApprovalDecision` ([ADR 0013](0013-ops-human-approval-and-state.md)) **avant** d'écrire l'enregistrement de décision. Une valeur invalide est consignée comme `rejected` avec `invalid_decision` : aucun jeton n'est créé, et le graphe reçoit un rejet. Le nœud `approve` refait la même validation.
- `drafted` et `re_presented` portent `action_tool` et `action_args_sha256` de l'action présentée. `approved` reprend l'empreinte de l'action approuvée : c'est celle qui sera liée au jeton. `edit_submitted` laisse `action_args_sha256` vide : l'empreinte des arguments modifiés est recalculée par `reguard`, puis inscrite sur `re_presented`.
- Le journal consigne la **décision**, pas l'exécution : aucun enregistrement ne suit l'effet. En v1, l'effet est une écriture simulée en mémoire ; l'évaluation la vérifie par les reçus du simulateur et de l'exécuteur ([ADR 0015](0015-ops-evaluation-protocol.md)). Si l'écriture simulée échoue après `approved`, un enregistrement `error` (`connector_unavailable`) suit.

### Lien entre les enregistrements : `draft_id`

`AccessLog.record()` refuse un `event_id` fourni par l'appelant (ADR 0008) : l'identifiant d'un enregistrement reste aléatoire et propre à cette ligne. Le lien passe donc par un champ distinct, `draft_id`.

- `draft_id` est créé une seule fois, avec le brouillon (uuid4, 32 caractères hexadécimaux), et porté par l'état du graphe ([ADR 0013](0013-ops-human-approval-and-state.md)). Il ne dérive ni du texte, ni de la commande, ni de l'heure.
- Il figure sur chaque enregistrement de la requête qui suit la composition du brouillon : `drafted`, les décisions, `re_presented`, puis l'enregistrement final (`withheld`, `rejected`, `abandoned`, `error`, ou `approved` sans suite). Il reste le même d'un tour de modification à l'autre ; `edit_round` distingue les tours.
- Il est vide pour une requête qui n'a composé aucun brouillon : `order_status`, `diagnosis`, ou un refus avant la composition. Ces requêtes ont un seul enregistrement.
- Les identifiants internes de la requête (`request_id`) et du fil LangGraph (`thread_id`) ne sont jamais consignés.

### La décision avant la reprise, la reprise avant l'effet

Cette règle est l'affirmation G7 de l'EFVP. Les écritures du journal ont lieu dans l'enveloppe `OpsEnvelope` seulement, jamais dans un nœud du graphe ([ADR 0013](0013-ops-human-approval-and-state.md)).

1. **Enregistrement de décision d'abord.** `decide` écrit `approved`, `edit_submitted` ou `rejected` **avant** de créer le jeton d'approbation et **avant** `graph.invoke(Command(resume=...))`. Aucun nœud qui suit `approve` ne peut donc produire un effet avant que la décision soit consignée.
2. **Jeton ensuite.** Le jeton n'est créé qu'après l'écriture réussie de `approved`, et il est lié à `action_args_sha256` ([ADR 0013](0013-ops-human-approval-and-state.md)). Sans écriture, aucun jeton ; sans jeton, l'exécuteur refuse l'écriture simulée.
3. **Affichage en dernier.** Le texte final (texte approuvé à envoyer à la main, message de refus ou de retenue) n'est affiché qu'après l'écriture du dernier enregistrement de la requête. Un brouillon n'est affiché qu'après `drafted` ou `re_presented`.
4. **Fin du fil sur chaque chemin.** `checkpointer.delete_thread(thread_id)` s'exécute dans un bloc `finally`, qu'une écriture du journal ait réussi ou non.

Deux comptes de l'[ADR 0015](0015-ops-evaluation-protocol.md) mesurent cette règle, chacun avec son contrôle négatif : `log_after_effect_count` (une écriture exécutée avant son enregistrement `approved`) et `log_missing_count` (un `drafted` sans enregistrement final).

### En cas de panne, aucun effet : `audit_unavailable`

- Une erreur d'écriture (`OSError`) ou de validation devient une `AccessLogError`, comme en RH. L'enveloppe rend alors `audit_unavailable` : **rien n'est affiché, aucun jeton n'est créé, rien n'est exécuté**. Le message vient de `OpsRefusalPolicy` et nomme une boîte de rôle, jamais une personne. `alverno service ask` et `alverno maint ask` sortent avec le code 2 ; une évaluation s'interrompt avec le code 2.
- Aucun autre enregistrement n'est tenté pour cette requête. `audit_unavailable` est donc un message servi, pas un événement du journal.
- La purge des fichiers échus précède l'écriture, comme dans l'ADR 0008 : si elle échoue, rien n'est écrit et la requête reçoit `audit_unavailable`.
- **La journalisation ne peut pas être désactivée**, ni en ligne de commande ni en évaluation.

### `executed_external_send: Literal[False]`

Le module 3 n'a aucun moyen d'envoi : ni courriel, ni Teams, ni portail ([ADR 0012](0012-ops-architecture-typed-tools.md), affirmation G1). Le texte approuvé est affiché pour que l'opérateur l'envoie à la main depuis sa propre boîte. Le champ inscrit cette garantie dans chaque enregistrement : une tentative d'y écrire `True` échoue à la validation et produit `audit_unavailable`. Une capacité d'envoi (module 6) exigera une version 2 du schéma, un nouvel ADR et une nouvelle version de l'EFVP.

### `approver="operator"` et `actor=None` : une limite du labo

- Le labo n'a pas d'authentification. La personne qui lance `ask` approuve elle-même ses brouillons. `approver` vaut donc `"operator"` sur chaque décision : il dit qu'**une décision humaine a été prise au poste**, pas **qui** l'a prise.
- `actor` reste toujours vide. Les règles de l'ADR 0008 pour le module 6 s'appliquent telles quelles au module 3 : `actor` vaudra `HMAC-SHA256(clé, identifiant d'annuaire)`, avec une clé détenue par le RPRP hors de l'hôte du journal, et `pi_types` sera alors retiré du schéma.
- En production (modules 5 et 6), la règle prévue est l'approbation à quatre yeux pour les réponses qui annoncent une date révisée, par une personne authentifiée. Tant que ce n'est pas fait, l'approbation machinale et l'absence d'imputabilité sont des risques résiduels de l'EFVP (R13), à confirmer par le RPRP.

### Empreinte des arguments : un risque résiduel accepté

L'ADR 0008 exclut toute empreinte de la question, parce qu'une empreinte de texte court se confirme en devinant. Le module 3 consigne une seule empreinte, `action_args_sha256`, nécessaire à la fin 3 : sans elle, rien ne relie l'écriture exécutée à la décision consignée.

- Pour `gmao.create_work_request`, les arguments sont des codes fermés et des identifiants (équipement, symptôme, code de panne, bons de travail cités). L'empreinte ne protège donc pas ces valeurs, qu'on peut retrouver en essayant les combinaisons ; elles ne décrivent qu'une machine, et le journal est de toute façon de niveau 4.
- Pour `erp.record_reply_prepared`, les arguments contiennent `draft_sha256`, l'empreinte du texte approuvé. `action_args_sha256` est donc une empreinte **indirecte** de ce texte. Quelqu'un qui devine le texte exact d'un brouillon peut le confirmer. Le risque est accepté et rattaché au risque R16 de l'EFVP (réidentification par le journal ; proposé le 2026-11-01, à confirmer par le RPRP) : un brouillon du modèle compte jusqu'à 1 200 caractères et ne se devine pas mot pour mot ; la commande visée figure déjà dans `entity_refs` ; et le texte d'un brouillon ne contient, par construction, que les faits de la commande et les coordonnées d'affaires de son contact ([ADR 0012](0012-ops-architecture-typed-tools.md)).
- Aucune autre empreinte n'est consignée : ni celle de la demande, ni celle d'une modification, ni celle d'un texte retenu.

### Jamais consigné

- la demande de l'opérateur, brute ou caviardée ;
- le texte d'un brouillon, d'une modification ou d'un texte servi ;
- toute empreinte de texte autre que `action_args_sha256`, et toute longueur de texte ;
- les valeurs d'un contact : civilité, nom, fonction, courriel, et le libellé du compte (celui d'une entreprise individuelle est traité comme un nom) ;
- toute valeur sur un technicien : nom, matricule, initiales, identifiant de connexion, quart, durée ;
- les charges utiles des interruptions, les invites et les sorties du modèle ;
- les références de contact, `request_id` et `thread_id` ;
- l'utilisateur du système d'exploitation, le nom du poste et l'adresse IP.

### Fichiers, préfixes et purge

- **Préfixe par module.** `AccessLog(dir, prefix: Literal["hr", "service", "maint"] = "hr", record_type=...)`. Les valeurs par défaut reproduisent le comportement RH : les fichiers RH gardent leur nom, `logs/access/hr-AAAA-MM-JJ.jsonl`. Le module 3 écrit `logs/access/service-AAAA-MM-JJ.jsonl` et `logs/access/maint-AAAA-MM-JJ.jsonl`, un fichier par jour UTC.
- **Reconnaissance des fichiers.** `_DAY_FILE_RE` reconnaît les trois préfixes. Chaque instance d'`AccessLog` ne purge que les fichiers de son propre préfixe : une instance RH ne touche jamais un fichier `service-` ou `maint-`, et l'inverse. `alverno access purge` purge les trois préfixes.
- **Conservation : 90 jours au plus.** Le réglage `access_log_retention_days` accepte seulement une valeur de 1 à 90 jours, comme en RH : il peut raccourcir la conservation, jamais l'allonger. Les fichiers échus sont supprimés à chaque ajout et par `alverno access purge` (art. 23 ; clé `lprp_destruction_anonymisation`). Chaque rapport du module 3 inscrit la valeur effective.
- **Évaluations.** `alverno service eval` et `alverno maint eval` écrivent le journal de l'exécution dans `<out>/access/`, avec `channel="eval"`, **dans les deux modes** (`tools` et `full`). Contrairement au mode recherche du module RH ([ADR 0011](0011-hr-gate-calibration.md)), le mode outils sert des textes et exécute le flux d'approbation : ses enregistrements ont donc le même sens qu'en mode complet. `<out>/access/` est balayé par `alverno privacy scan` avec les autres artéfacts, puis exclu du téléversement de l'intégration continue (`reports/ci/**/access/`). Ces fichiers ne sont jamais versés dans git (`logs/` et `reports/**/access/` sont ignorés).

### Lecteurs et demandes des personnes

- **Lecteurs** : le RPRP (directeur des finances et de l'administration, rôle fictif) et le soutien informatique, qui agit sous son autorité, comme en RH (art. 20 ; clé `lprp_acces_besoin_fonctions`). L'outil s'exécute avec les droits de la personne qui l'utilise : elle peut lire les lignes de son poste, et la limite de l'EFVP RH s'applique (un poste partagé exigerait une nouvelle version de l'EFVP).
- **Incidents.** Le journal aide à détecter et à dater un incident ; il ne remplace pas le registre des incidents (art. 3.8 ; clé `lprp_registre_incidents`). L'analyse de l'ADR 0008 s'applique : une demande refusée par `scope_check` ou un brouillon retenu par la garde n'a rien communiqué ; selon cette analyse du labo, à confirmer par le RPRP pour chaque cas, ce n'est pas, en soi, un incident.
- **Accès et rectification.** Les enregistrements du journal sont les seuls éléments que le module 3 conserve après une requête : aucun brouillon, aucun état. Une demande d'accès ou de rectification qui les viserait (art. 27 et 28 ; clés `lprp_acces_communication` et `lprp_rectification`) est traitée par le RPRP. Le délai de réponse n'est pas indiqué ici : le [registre RH](../hr-legal-sources.md#énoncés-exclus) l'exclut encore.

### Exemple synthétique

Toutes les valeurs sont fictives et choisies pour l'exemple ; aucune n'est une mesure. Deux enregistrements d'une même requête `customer_reply` : le brouillon présenté, puis son approbation. La demande, le brouillon et le contact ne figurent nulle part.

```json
{"v": 1, "module": "service", "event_id": "0123456789abcdef0123456789abcdef", "draft_id": "fedcba9876543210fedcba9876543210", "ts": "2026-11-01T14:00Z", "channel": "cli", "flow": "customer_reply", "event": "drafted", "reason": null, "tool_calls": ["erp.get_order"], "entity_refs": ["CMD-2026-0412"], "account_ref": "CLI-003", "pi_types": [], "pi_count": 0, "quarantine_flags": [], "action_tool": "erp.record_reply_prepared", "action_args_sha256": "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff", "executed_external_send": false, "edit_round": 0, "model": "gemma4:e4b", "latency_ms": 1000, "approver": null, "actor": null}
{"v": 1, "module": "service", "event_id": "aaaaaaaaaaaaaaaabbbbbbbbbbbbbbbb", "draft_id": "fedcba9876543210fedcba9876543210", "ts": "2026-11-01T14:00Z", "channel": "cli", "flow": "customer_reply", "event": "approved", "reason": null, "tool_calls": [], "entity_refs": ["CMD-2026-0412"], "account_ref": "CLI-003", "pi_types": [], "pi_count": 0, "quarantine_flags": [], "action_tool": "erp.record_reply_prepared", "action_args_sha256": "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff", "executed_external_send": false, "edit_round": 0, "model": null, "latency_ms": 1000, "approver": "operator", "actor": null}
```

## Solutions écartées

- **Étendre le modèle RH `AccessLogRecord` avec des champs facultatifs** : le format publié du journal RH changerait, et les tests du module RH ne resteraient pas inchangés ; le module 3 ajoute son code à côté du code RH, sans le modifier ([ADR 0012](0012-ops-architecture-typed-tools.md)).
- **Relier les enregistrements par un `event_id` choisi par l'appelant** : `AccessLog.record()` le refuse, et l'identifiant d'une ligne doit rester aléatoire et propre à elle.
- **Consigner la décision après la reprise du graphe, ou après l'effet** : une panne entre les deux laisserait un effet sans trace de la décision qui l'autorise.
- **Un enregistrement `executed` après l'effet** : il ne protège rien, puisque l'effet a déjà eu lieu. En v1, l'effet est simulé en mémoire et vérifié par les reçus de l'évaluation ; un connecteur réel exigera une nouvelle version de l'EFVP et son propre reçu.
- **Une empreinte HMAC des arguments, avec une clé propre au processus** : l'enregistrement ne pourrait plus être rapproché du reçu de l'écriture une fois le processus terminé, notamment pour évaluer un incident.
- **Consigner la référence du contact ou le destinataire** : le numéro de commande suffit aux trois fins.
- **Un seul fichier pour tous les modules** : le nom des fichiers RH changerait, et le RPRP ne pourrait plus lire ou purger un module sans l'autre.
- **Une purge commune aux trois préfixes dans chaque instance** : une instance réglée à une conservation plus courte effacerait les fichiers d'un autre module.
- **Désactiver le journal en évaluation** : `log_missing_count` et `log_after_effect_count` ne mesureraient plus rien.

## Conséquences

Positives :

- Le journal montre, pour chaque écriture simulée, qu'une décision humaine sur les mêmes arguments a été consignée avant elle, sans contenir ni demande, ni brouillon, ni contact.
- Un journal indisponible bloque toute présentation, toute approbation et tout effet, au lieu de les laisser se produire sans trace.
- Le schéma, les fichiers et la purge du module RH restent inchangés.

Négatives :

- Le journal ne dit pas qui a approuvé : `actor` reste vide jusqu'au module 6, et l'auto-approbation est un risque résiduel de l'EFVP.
- `action_args_sha256` est une empreinte indirecte du texte approuvé ; le risque est accepté et soumis au RPRP.
- Un brouillon en attente est perdu si la ligne de commande se ferme ; seul l'enregistrement `abandoned` en garde la trace, et seulement pour EOF ou Ctrl-C.
- Le journal ne prouve pas qu'un brouillon servi était exempt de RP ; cette preuve vient de la garde de sortie et des évaluations de l'[ADR 0015](0015-ops-evaluation-protocol.md).

Voir aussi l'[EFVP du module 3](../efvp/ops-module.md), le [registre juridique du module 3](../ops-legal-sources.md) et les ADR [0012](0012-ops-architecture-typed-tools.md), [0013](0013-ops-human-approval-and-state.md) et [0015](0015-ops-evaluation-protocol.md).
