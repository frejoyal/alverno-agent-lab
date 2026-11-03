# ADR 0013 — Module 3 : approbation humaine, jeton à usage unique et état éphémère

- **Statut** : accepté le 2026-11-01, à l'approbation de l'[EFVP du module 3](../efvp/ops-module.md#7-avis-du-responsable-décision-et-approbation) par la présidente (dans ce labo, le responsable du dépôt dans ce rôle)
- **Date** : 2026-11-01

## Contexte

Dans le module 3, deux fins préparent une action : `customer_reply` (3a) prépare un brouillon de réponse et l'écriture simulée `erp.record_reply_prepared` ; `work_request` (3b) prépare l'écriture simulée `gmao.create_work_request` ([ADR 0012](0012-ops-architecture-typed-tools.md)). L'approbation humaine y est obligatoire : la personne approuve, modifie ou rejette. Pour que cette approbation protège un effet réel, chaque sous-module a une écriture structurée (décision 3 du responsable du dépôt).

LangGraph offre `interrupt` et `Command(resume=...)` pour suspendre un graphe et le reprendre. Sept constats, relevés pendant la revue du plan, orientent la conception. Ceux marqués « (revue) » viennent d'essais faits pendant la revue et ne sont pas ré-exécutés ici.

1. **Un nœud interrompu reprend depuis sa première ligne** (revue). Un effet placé dans le nœud d'approbation s'exécuterait deux fois.
2. **Un appel de nœud ne prouve pas une décision humaine.** Sans preuve liée aux arguments, un nœud `apply` appelé par erreur ou une modification d'arguments après approbation passerait.
3. **La charge utile d'une interruption est enregistrée par le point de contrôle** (checkpointer) et peut paraître dans une trace. Un texte de brouillon ou un contact y serait exposé.
4. **Un point de contrôle durable créerait un dépôt de niveau 4** : brouillons, état et décisions sur disque.
5. **Le traçage hébergé doit rester coupé à chaque reprise**, pas seulement au premier appel : chaque `invoke` est un nouvel appel.
6. **Une erreur peut afficher une valeur** : la validation pydantic d'une valeur de reprise, ou une `KeyError` sur un fil inconnu (revue), produit une trace d'appel qui peut citer l'entrée.
7. **L'opérateur s'approuve lui-même dans le labo.** Aucune authentification n'existe avant le module 6 (décision 4 du responsable du dépôt : `approver="operator"`, `actor=None`).

Cet ADR ne contient aucune mesure : aucune ligne de `src/` du module 3 n'existe au moment de sa rédaction. Il est rédigé en tenant compte de la *Loi sur la protection des renseignements personnels dans le secteur privé* (RLRQ, c. P-39.1), état du droit vérifié le 1er novembre 2026 sur le texte consolidé à jour au 15 octobre 2026 ; **ceci n'est pas un avis juridique**. Chaque énoncé juridique renvoie à une clé du [registre du module 3](../ops-legal-sources.md) ou du [registre RH](../hr-legal-sources.md).

## Décision

### Un nœud `approve` pur, avec une charge utile limitée aux identifiants

Le nœud `approve` (`agents/ops/approval.py`) appelle :

```
raw = interrupt({"draft_id", "flow", "action_id", "tool", "edit_round"})
```

- La charge utile ne porte que des identifiants et des codes : aucun texte de brouillon, aucun contact, aucun argument.
- L'enveloppe lit le brouillon par `graph.get_state` pour l'afficher ; il ne transite jamais par la charge utile.
- Le nœud valide `raw` avec le modèle pydantic du projet `ApprovalDecision` : `decision` parmi `approve`, `edit` et `reject` ; `edited_text` (1500 caractères au plus, facultatif) ; `edited_args` (facultatif) ; `token` (facultatif).
- Une valeur invalide devient un rejet, avec le motif `invalid_decision`. Elle n'est jamais affichée.
- `interrupt(response_schema=...)` n'est pas utilisé : le plancher `langgraph>=1.0` reste tel quel.
- Le nœud ne fait aucune entrée-sortie. Il reprend depuis sa première ligne à chaque reprise ; la métrique `duplicate_executions` et son contrôle négatif le surveillent ([ADR 0015](0015-ops-evaluation-protocol.md)).

### Une action par interruption

`ProposedAction(action_id, tool, args, args_sha256, summary_fr)` décrit une seule écriture. Chaque interruption présente une seule action : l'approbation vaut donc pour une action à la fois, par construction. Aucun lot d'actions n'est approuvé d'un seul geste.

### Un jeton HMAC à usage unique, lié à l'empreinte des arguments

- `ApprovalAuthority` est détenue par l'enveloppe, hors du graphe. Elle tient une clé HMAC aléatoire propre au processus, qui ne quitte jamais la mémoire.
- Elle émet un `ApprovalToken(action_id, tool, args_sha256, nonce, mac)` **seulement après** l'écriture de l'enregistrement de décision `approved` au journal ([ADR 0014](0014-ops-access-log.md)).
- Le jeton passe au graphe dans la valeur de reprise (`ApprovalDecision.token`). Il ne contient aucun contenu : des identifiants, une empreinte, un nonce et un code d'authentification.
- `ToolExecutor` exige, pour une écriture, `authority.verify(token, name, sha256(canonical_json(args)))`. Des arguments modifiés après l'approbation changent l'empreinte, et le jeton est refusé.
- L'exécuteur consomme le nonce. Une seconde utilisation est refusée et comptée dans `duplicate_execution_attempts`.

L'exécuteur prouve ainsi qu'**une décision humaine consignée a approuvé ces arguments exacts**, et non seulement que le nœud `apply` l'a appelé. Le nœud `apply` fait un seul appel `executor.call(write_tool, args, token=decision.token)` ; le nœud `handoff` marque ensuite la remise (`handed_off = True`).

### Modification et revérification (`reguard`), en 2 tours au plus

Une modification repasse par les mêmes barrières qu'un brouillon :

- un corps modifié passe par la quarantaine, `check_output` et `check_scope` ;
- des arguments modifiés sont validés par le modèle d'arguments et les énumérations fermées, et leur empreinte est recalculée.

Si la modification passe, le graphe revient à `approve` avec une nouvelle interruption, et `edit_round` augmente de 1. Si elle échoue, le résultat est `withheld`, avec le motif `output_pi`, `cross_account` ou `invalid_args`. Le nombre de tours est borné par `ops_max_edit_rounds = 2` (réglage qui n'accepte pas plus de 2). Une modification soumise après le deuxième tour n'est pas acceptée : elle devient un rejet, avec le motif `invalid_decision`.

En ligne de commande, la modification est lue sur l'entrée standard, jamais par un fichier temporaire ouvert dans `$EDITOR`, qui laisserait un brouillon sur disque.

### État en primitives JSON seulement

`OpsState` (`agents/ops/state.py`) est un `TypedDict` de primitives JSON. Ses champs : `request_redacted`, `flow`, `ids`, `tool_events`, `records`, `quarantine_flags`, `draft_body`, `draft_citations`, `draft_id`, `draft_sha256`, `proposed_action`, `decision`, `edit_round`, `refusal` et `trace` (`Annotated[list[dict], operator.add]`).

- `ContactView` n'est jamais stocké dans l'état : `compose` le lit, et seul le texte composé reste ([ADR 0012](0012-ops-architecture-typed-tools.md)).
- Aucun objet Python arbitraire n'entre dans l'état, donc la sérialisation n'a jamais besoin de `pickle`.
- `agents/ops/scrub.py` nettoie la trace de chaque nœud : il ne garde que la fin, des comptes, des codes, des identifiants et des signalements. Un nœud inconnu ne garde aucun détail. Les charges utiles d'interruption et les textes ne sont jamais gardés.

### Point de contrôle en mémoire vive, détruit à la fin de chaque requête

- Le point de contrôle est `InMemorySaver(serde=JsonPlusSerializer(pickle_fallback=False))`. `serde` est passé par mot-clé, car le constructeur l'exige ainsi (revue). Il y en a un par enveloppe.
- La limite de récursion du graphe est 25.
- `checkpointer.delete_thread(thread_id)` s'exécute dans un bloc `finally`, sur chaque chemin : réponse, refus, rejet, retenue, abandon et erreur. La métrique `residual_threads` compte les fils restés dans le point de contrôle après un item.
- `langgraph/checkpoint/serde/_msgpack.py` lit le réglage du mode strict de msgpack (`STRICT_MSGPACK_ENABLED`) à l'importation. Les tests le fixent donc dans `os.environ` en tête de `tests/conftest.py`, avant toute importation (ou corrigent `_msgpack.STRICT_MSGPACK_ENABLED`), et le vérifient avec `caplog` (incrément I5).

Un brouillon en attente est perdu si la commande se termine avant la décision. C'est accepté : seul un brouillon est perdu, et aucun RP n'est écrit sur disque. L'avis affiché au lancement et l'EFVP le disent.

Ces choix appliquent les mesures de sécurité raisonnables ([art. 10](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:10), `lprp_mesures_securite`) et la destruction une fois les fins accomplies ([art. 23](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:23), `lprp_destruction_anonymisation`) : l'état d'une requête n'existe qu'en mémoire vive, le temps de la requête.

### Traçage hébergé coupé à chaque appel

Chaque `graph.invoke`, le premier comme chaque `Command(resume=...)`, s'exécute :

1. après `check_tracing_environment()`, qui lève `LegacyTracingError` devant une variable de traçage LangChain v1 (comme aux modules 1 et 2) ;
2. sous `langsmith.tracing_context(enabled=False)`.

Un test vérifie le traçage coupé sur chaque reprise, pas seulement sur le premier appel.

### Erreurs relancées sans contexte

Une `KeyError` venue d'un fil inconnu et une `ValidationError` sont interceptées et relancées en `OpsRuntimeError(...) from None`. Le message donne un code et une étape, jamais une valeur. Aucune valeur d'entrée ne peut donc paraître dans une trace d'appel.

### Ordre de l'enveloppe, qui échoue fermé

`OpsEnvelope` (`agents/ops/runtime.py`) a trois méthodes, `start`, `decide` et `abandon`. Leur ordre est fixe.

1. **`start`**
   1. Appeler `check_tracing_environment()`.
   2. Créer `request_id` et `thread_id` avec uuid4.
   3. Appliquer le filtre de la demande. Un refus n'entraîne aucun appel de connecteur ni de LLM ; `CountingConnector` et `CountingChat` le vérifient.
   4. Appeler le graphe sous `tracing_context(enabled=False)`.
   5. Sur `__interrupt__`, lire le brouillon par `graph.get_state`, écrire l'enregistrement `drafted` (avec `draft_id`), et **seulement ensuite** afficher le brouillon avec l'en-tête de l'opérateur.
2. **`decide`**
   1. Écrire d'abord l'enregistrement de décision : `approved`, `edit_submitted` ou `rejected`.
   2. Émettre ensuite le jeton, si la décision est une approbation.
   3. Reprendre le graphe par `graph.invoke(Command(resume=...))`, après `check_tracing_environment()` et sous `tracing_context(enabled=False)`.
   4. Si le graphe présente une nouvelle interruption, écrire `re_presented`, puis l'afficher. S'il se termine en `withheld`, écrire `withheld`.
   5. N'afficher le texte final qu'après l'écriture du dernier enregistrement.
3. **`abandon`** (fin de l'entrée standard ou Ctrl-C) : écrire l'enregistrement `abandoned`.
4. **`finally`** : `checkpointer.delete_thread(thread_id)` sur chaque chemin, puis la conversion des erreurs décrite plus haut.
5. **Journal indisponible** : si une écriture au journal échoue, le résultat est `audit_unavailable`. Rien n'est affiché, aucun jeton n'est émis, rien n'est exécuté.

Les écritures au journal ne vivent que dans l'enveloppe, jamais dans un nœud. Règle de cet ADR : **chaque nœud après `approve` reste pur tant que l'enregistrement de décision n'est pas écrit, et cet enregistrement précède toujours `Command(resume)`**. La métrique `log_after_effect_count` compte une écriture exécutée avant son enregistrement `approved` ; `log_missing_count` compte un `drafted` sans enregistrement terminal. Le schéma des enregistrements est dans l'[ADR 0014](0014-ops-access-log.md).

### Barrières et contrôles négatifs

Chaque barrière de cet ADR a son contrôle négatif, qui doit faire échouer sa métrique (`tests/test_ops_negative_controls.py`, détails dans l'[ADR 0015](0015-ops-evaluation-protocol.md)) :

| Barrière retirée | Métrique qui doit échouer |
|---|---|
| nœud `approve` contourné, exécuteur intact | `unapproved_write_attempts` supérieur à 0 |
| jeton forgé, ou arguments modifiés avec une empreinte périmée | `edit_mismatch` supérieur à 0, ou tentatives refusées supérieures à 0 |
| effet placé dans le nœud `approve` | `duplicate_executions` supérieur à 0 |
| nonce non consommé | `duplicate_execution_attempts` supérieur à 0 |
| `reguard` contourné | `edit_not_reguarded` supérieur à 0 |
| `delete_thread` omis | `residual_threads` supérieur à 0 |
| journal écrit après la reprise | `log_after_effect_count` supérieur à 0 |

### Responsabilité de l'approbation dans le labo

- L'opérateur s'approuve lui-même : `approver="operator"`, `actor=None` jusqu'au module 6 (décision 4 du responsable du dépôt). C'est une limite du labo, inscrite comme risque résiduel dans l'EFVP.
- L'en-tête d'approbation affiche le libellé du compte, la fonction du destinataire, les identifiants cités et la ligne « Brouillon préparé par un outil automatisé — à relire ». Le libellé du compte s'affiche à l'opérateur ; il n'entre dans aucune invite ni aucun journal.
- La mention « outil automatisé » figure dans l'en-tête de l'opérateur seulement, pas dans le texte au client (décision 10 du responsable du dépôt). C'est un choix d'Alverno. Il repose sur une **lecture du labo**, marquée « incertain » au registre : l'art. 12.1 vise une décision fondée exclusivement sur un traitement automatisé ([art. 12.1](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:12_1), `lprp_decision_automatisee`), et ici chaque brouillon et chaque diagnostic n'est qu'une aide, suivie d'une décision humaine. Une approbation machinale pourrait être vue autrement ; ce risque est inscrit dans l'EFVP.
- Règle prévue pour l'exploitation (modules 5 et 6), hors périmètre ici : une approbation à quatre yeux pour une réponse qui annonce une date révisée, une personne approbatrice authentifiée et un `actor` pseudonymisé par HMAC, selon les règles du module 6 de l'[ADR 0008](0008-minimal-access-log.md).

## Solutions écartées

- **Un point de contrôle SQLite (`langgraph-checkpoint-sqlite`)** : il créerait un dépôt durable de niveau 4 (brouillons, état, décisions) pour un besoin que la mémoire vive couvre.
- **`interrupt_before` sur le nœud `apply`** : la décision entrerait par `update_state`, hors de la validation pydantic du nœud ; un point d'arrêt statique ne dit pas quelle action est approuvée.
- **`interrupt(response_schema=...)`** : il exigerait de relever le plancher `langgraph>=1.0` ; la validation par le modèle `ApprovalDecision` du projet donne le même résultat et transforme une valeur invalide en rejet.
- **Un dossier d'envoi (« outbox ») où déposer les brouillons approuvés** : du texte libre sur disque, donc un dépôt de RP, et l'ébauche d'un chemin d'envoi.
- **Émettre le jeton dans un nœud du graphe** : le nœud reprend depuis sa première ligne, et la clé HMAC entrerait dans l'état.
- **Un jeton qui ne lie pas l'empreinte des arguments** : une modification après approbation passerait.
- **Approuver un lot d'actions d'un seul geste** : l'approbation ne vaudrait plus pour chaque action.
- **Le texte du brouillon dans la charge utile d'interruption** : il serait enregistré par le point de contrôle et visible dans une trace.
- **Une modification par fichier temporaire dans `$EDITOR`** : un brouillon resterait sur disque.
- **Un rôle d'approbateur choisi à l'invite dès maintenant** (autre option de la décision 4) : il resterait non authentifié et donnerait une fausse impression de responsabilité.
- **Une approbation à quatre yeux dès maintenant** : sans authentification, deux approbations sur le même poste ne prouvent rien.

## Conséquences

Positives :

- Aucune écriture simulée ne s'exécute sans un enregistrement `approved` qui porte la même empreinte d'arguments, et un contrôle négatif prouve que chaque barrière est nécessaire.
- Une décision consignée précède toujours son effet ; un journal indisponible bloque tout.
- Aucun brouillon, aucun état et aucun contact ne reste sur disque après la requête.
- Le traçage hébergé reste coupé à chaque reprise.

Négatives :

- Un brouillon en attente est perdu si la commande se termine ; l'opérateur doit relancer la demande.
- L'opérateur s'approuve lui-même et n'est pas authentifié : l'approbation machinale reste un risque résiduel, réduit seulement par l'en-tête (compte, fonction du destinataire, identifiants cités).
- Le texte approuvé et le brouillon restent dans le défilement du terminal ; l'EFVP l'inscrit comme risque résiduel.
- Deux tours de modification au plus : une troisième correction exige une nouvelle demande.
- La clé HMAC est propre au processus : aucune approbation ne survit à un redémarrage, et aucune file d'approbation entre processus n'existe avant les modules 5 et 6.

Voir aussi l'[EFVP du module 3](../efvp/ops-module.md), le [registre juridique du module 3](../ops-legal-sources.md), l'[ADR 0014](0014-ops-access-log.md) (journal) et l'[ADR 0015](0015-ops-evaluation-protocol.md) (protocole d'évaluation).
