# ADR 0008 — Journal des accès minimal

- **Statut** : accepté
- **Date** : 2026-11-01

## Contexte

L'échelle de sensibilité du labo ([docs/loi25.md](../loi25.md)) associe le niveau 4 à une journalisation des accès. L'assistant RH ne sert que des documents de niveau 2, mais ses questions peuvent contenir, par accident, des renseignements jusqu'au niveau 5. Il faut donc pouvoir dire après coup quelles demandes ont été servies ou refusées et quels documents ont été cités, sans que le journal devienne lui-même un dépôt de renseignements personnels.

Trois exigences tirent en sens contraire.

- **Consigner et compter** : pour chaque demande, la décision, le motif de refus et les documents cités, avec leur niveau.
- **Soutenir l'évaluation d'un incident** : pour juger du risque de préjudice, l'entreprise tient compte notamment du caractère sensible du renseignement, des conséquences possibles de son utilisation et de la probabilité qu'il serve à nuire, et elle consulte son responsable de la protection des renseignements personnels (RPRP) (art. 3.7, paraphrase).
- **Minimiser** : la collecte se limite à ce qui est nécessaire aux fins fixées d'avance (art. 5), les mesures de sécurité tiennent compte notamment de la sensibilité, de la finalité, de la quantité, de la répartition et du support des renseignements (art. 10), et un renseignement est détruit ou anonymisé une fois ses fins accomplies (art. 23).

Le module 1 offre déjà des traces facultatives (`alverno ask --trace`) qui ne gardent pas le texte de la question, mais en gardent une empreinte courte et la longueur. Pour une question courte, une empreinte se confirme en devinant : il suffit de calculer l'empreinte d'une question candidate et de la comparer.

Cet ADR est rédigé en tenant compte de la *Loi sur la protection des renseignements personnels dans le secteur privé* (RLRQ, c. P-39.1), état du droit au 31 octobre 2026 ; ce n'est pas un avis juridique. Les articles sont paraphrasés ; leur libellé exact se lit sur LégisQuébec : [art. 3.1](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:3_1), [3.5](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:3_5), [3.6](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:3_6), [3.7](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:3_7), [3.8](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:3_8), [5](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:5), [8.1](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:8_1), [10](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:10) et [23](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:23).

## Décision

### Finalités, fixées avant la collecte

Le journal sert à trois fins, et à aucune autre :

1. **Compter les refus** : consigner, pour chaque demande, la décision, le motif de refus et les documents cités (avec leur niveau), afin de compter les refus et leurs motifs.
2. **Soutenir l'évaluation d'un incident** (art. 3.7) : dater une demande et savoir quels documents ont été servis et quels types de renseignements ont été retirés de la question.
3. **Vérifier le bon fonctionnement de l'assistant** : modèle utilisé, durée et erreurs.

Le journal ne conserve pas la réponse : il ne peut pas démontrer l'absence de renseignement personnel dans une réponse servie. Cette garantie repose sur la garde de sortie et sur le compte de fuites `answer_leak_count` (affirmation G3 de l'[EFVP du module RH](../efvp/hr-module.md)). Le niveau des documents cités, lui, vaut 2 par construction, puisqu'un document RH d'un autre niveau bloque le chargement du corpus.

### Un enregistrement figé par demande

Chaque demande RH, servie ou refusée, ajoute un seul enregistrement `AccessLogRecord` (pydantic, figé, `extra="forbid"`). Tout champ supplémentaire ou tout texte libre est rejeté à la validation. Chaque champ est rattaché à l'une des trois finalités ci-dessus.

| Champ | Contenu | Finalité |
|---|---|---|
| `v` | version du schéma (1) | structure du fichier |
| `event_id` | identifiant aléatoire (uuid4, 32 caractères hexadécimaux), sans lien avec le texte de la question | 2 : désigner une ligne précise dans l'évaluation d'un incident |
| `ts` | date et heure UTC, **arrondies à l'heure** | 2 : rapprocher une demande de l'heure d'un incident signalé (écran vu par un tiers, entrée du journal d'Ollama) ; 1 : fichiers quotidiens et purge. Précision proposée le 2026-11-01, à confirmer par le RPRP : l'heure suffit à ces deux fins et réduit le risque de réidentification |
| `module` | `hr` | structure du fichier |
| `channel` | `cli` ou `eval` | 1 : ne pas compter les exécutions d'évaluation comme de l'usage |
| `decision` | `answered`, `refused` ou `error` | 1 et 3 |
| `reason` | code de refus, ou vide | 1 |
| `kind`, `subject`, `requested_level` | classement de la question par la couche de l'[ADR 0006](0006-deterministic-privacy-layer.md) | 1 : compter les refus par catégorie (personne, petit groupe, dossier) et repérer une hausse des sondages, sans les rattacher à une personne |
| `pi_types`, `pi_count` | types de renseignements retirés de la question et leur nombre, jamais leur valeur | 2 : savoir quels types de renseignements ont pu transiter si un incident touche le poste (journal d'Ollama, défilement du terminal) ; 1 : compter les notes de caviardage. Retirés au module 6 |
| `doc_ids`, `max_doc_level` | documents cités et niveau le plus élevé servi | 1 et 2 |
| `model` | nom du modèle de langage | 3 |
| `latency_ms` | durée de la demande | 3 |
| `actor` | toujours vide dans ce module (voir le module 6 plus bas) | aucune dans ce module |

Exemple d'enregistrement **synthétique** : toutes les valeurs sont fictives et choisies pour l'exemple, aucune n'est une mesure. Il correspond à une question du type « Mon NAS doit-il figurer sur le formulaire de dépôt direct ? », où la personne avait écrit son numéro d'assurance sociale (NAS) ; ni la question ni le numéro ne sont conservés.

```json
{"v": 1, "event_id": "0123456789abcdef0123456789abcdef", "ts": "2026-11-01T14:00Z", "module": "hr", "channel": "cli", "decision": "answered", "reason": null, "kind": "policy", "subject": "self", "requested_level": 2, "pi_types": ["nas"], "pi_count": 1, "doc_ids": ["RH-011"], "max_doc_level": 2, "model": "gemma4:e4b", "latency_ms": 1000, "actor": null}
```

### Aucune empreinte de question

Aucune empreinte, aucun préfixe et aucune longueur de la question n'existent dans le module 2 : ni dans le journal, ni dans les traces RH (nettoyées par `scrub_trace`), ni dans les rapports d'évaluation. Une demande est identifiée seulement par son identifiant aléatoire.

### Règles d'exploitation

- **Emplacement** : `logs/access/hr-AAAA-MM-JJ.jsonl`, un fichier par jour UTC. Le dossier `logs/` sera exclu de git : `.gitignore` recevra `logs/` et `reports/**/access/` avec le code du journal.
- **Moment de l'écriture** : l'enregistrement est ajouté après la composition du texte servi et **avant** son affichage. Si une erreur Ollama échappe au graphe, un enregistrement `decision="error"` est écrit, puis l'erreur est relancée.
- **En cas de panne, aucune réponse** : une erreur d'écriture (`OSError`) ou de validation devient une `AccessLogError`. `hr ask` sert alors le message `audit_unavailable` (« Le journal des accès est indisponible ; par prudence, aucune réponse n'est donnée. ») et sort avec le code 2 ; une évaluation s'interrompt avec le code 2. **La journalisation ne peut pas être désactivée.**
- **Conservation : 90 jours.** Les fichiers de plus de 90 jours sont supprimés à chaque ajout et par `alverno access purge`. Cette durée, retenue à la conception et à confirmer par le RPRP, laisse le temps d'enquêter sur un incident signalé dans le trimestre, sans constituer un historique de l'usage de l'outil. Le réglage `access_log_retention_days` existe pour les tests ; changer sa valeur en exploitation est une décision de gouvernance du RPRP, à inscrire dans l'EFVP. Mesure retenue le 2026-11-01 (EFVP, R15) : le réglage accepte seulement une valeur de 1 à 90 jours, donc il peut raccourcir la conservation mais jamais l'allonger, et chaque rapport RH inscrit la valeur effective.
- **Jamais conservé** : la question (brute ou caviardée), toute empreinte, tout préfixe ou toute longueur de la question, les valeurs et les positions des renseignements détectés, la réponse, les extraits, l'utilisateur du système d'exploitation, le nom du poste et l'adresse IP.
- **Lecteurs** : le RPRP (voir l'[EFVP, section 2](../efvp/hr-module.md#2-rôles-et-responsabilités)) et le personnel informatique qui agit sous son autorité. L'outil s'exécute avec les droits de la personne qui l'utilise, qui peut donc lire les lignes de son poste ; sur un poste personnel, ces lignes ne portent que sur ses propres demandes, et ce risque résiduel est accepté (proposé le 2026-11-01, à confirmer par le RPRP) ; un poste partagé exigerait une nouvelle version de l'EFVP. Le journal est traité comme une donnée de niveau 4.

### Analyse du labo : une tentative refusée n'est pas, en soi, un incident

*Interprétation à confirmer par le RPRP pour chaque cas ; elle ne figure pas dans les sources juridiques vérifiées.*

La loi définit l'incident de confidentialité comme un accès, une utilisation ou une communication d'un renseignement personnel que la loi n'autorise pas, ou comme sa perte ou toute autre atteinte à sa protection (art. 3.6, paraphrase). Une question refusée par la couche de l'ADR 0006 s'arrête avant toute recherche : aucun renseignement n'a été consulté ni communiqué, et le corpus RH n'en contient de toute façon aucun. Une réponse retenue par la garde de sortie (`output_pi`) n'a pas été servie. Selon cette analyse, ni l'une ni l'autre n'est inscrite au registre des incidents.

Les incidents prévisibles de ce module sont d'une autre nature :

- un artéfact d'intégration continue ou un rapport versionné qui contient un vrai renseignement personnel ;
- le journal de débogage d'Ollama ou l'historique du shell, qui contient les renseignements d'un tiers, lu par une autre personne ;
- un accès non autorisé aux fichiers de `logs/` ;
- un document contenant des renseignements personnels (niveau 4 ou 5) servi par l'assistant.

Dès qu'il y a des motifs de croire à l'un d'eux, l'entreprise prend des mesures raisonnables pour réduire le risque (art. 3.5). Chacun, que le risque de préjudice soit sérieux ou non, est inscrit au registre (art. 3.8) ; si le risque est sérieux, la CAI et les personnes concernées sont avisées (art. 3.5). Le journal aide à dater la demande en cause et à évaluer le risque (art. 3.7).

### Journaux des évaluations

En mode complet, `alverno hr eval` écrit le journal de l'exécution dans `<out>/access/`. En mode recherche, il n'écrit aucun enregistrement : rien n'y est servi, et `answered` garde un seul sens, une réponse servie (décision du 2026-11-01, voir l'[ADR 0011](0011-hr-gate-calibration.md)). Ces fichiers ne seront **jamais versés dans git** : `.gitignore` recevra `logs/` et `reports/**/access/`. Le présent ADR montre le format par l'exemple synthétique ci-dessus. Dans l'intégration continue (CI), les rapports ne sont téléversés qu'après une recherche des témoins (`alverno privacy scan reports/ci`) qui ne trouve rien.

### Règles du module 6 (authentification), hors périmètre ici

Quand l'authentification arrivera, le champ `actor` cessera d'être vide. Ces règles sont fixées dès maintenant :

- `actor` vaudra `HMAC-SHA256(clé, identifiant d'annuaire)`. Le RPRP détient la clé hors de l'hôte du journal et hors de l'annuaire ; elle est renouvelée et détruite à chaque période de conservation.
- `pi_types` sera alors retiré du schéma : lié à une personne, savoir qu'elle a tapé un NAS ou un nom devient un renseignement sur elle.
- Les citations des documents sensibles par leur sujet, RH-007 (maladie et obligations familiales), RH-008 (congés parentaux) et RH-010 (harcèlement), ne seront consignées que par `max_doc_level`, sans leur identifiant : lié à une personne, le fait de consulter la politique sur le harcèlement en dit long.
- Le labo traite toute analyse par personne (fréquence d'usage, sujets consultés) comme un **profilage** au sens de l'art. 8.1, qui inclut l'évaluation de caractéristiques comme le rendement au travail : information préalable des personnes, fonction désactivée par défaut et nouvelle version de l'EFVP.

### Risque résiduel accepté : l'empreinte des traces du module 1

Les traces facultatives du module 1 (`alverno ask --trace FILE`) gardent un préfixe de 48 bits de l'empreinte SHA-256 de la question (12 caractères hexadécimaux) et sa longueur. Pour une question courte, quelqu'un qui devine la question peut confirmer sa supposition. Ce risque est **accepté et inscrit au registre des risques de l'EFVP** :

- les traces sont facultatives et locales ;
- le module Qualité refuse les questions visant une personne, et son corpus est de niveau 2 ;
- changer ce format modifierait un comportement publié du module 1, que l'[ADR 0007](0007-hr-envelope-and-per-module-data.md) garde inchangé.

Les traces RH (`alverno hr ask --trace`) ne reprennent pas ce mécanisme.

## Solutions écartées

- **Une empreinte HMAC de la question, avec sa clé dans le même dossier** : la clé à côté du journal ne protège rien, et l'empreinte reste confirmable.
- **365 jours avec un pseudonyme de l'acteur** : un an d'historique par personne, sans finalité qui l'exige.
- **Une chaîne d'empreintes entre les enregistrements** : complexité sans finalité définie ici ; l'intégrité du poste est hors du modèle de menace.
- **Le journal dans `.cache/`** : ce dossier sert aux artefacts reconstruisibles, qu'on efface sans y penser.
- **Des fichiers mensuels** : la purge à 90 jours se ferait par blocs d'un mois et garderait jusqu'à un mois de trop.

## Conséquences

Positives :

- Le journal consigne la décision, le motif et les documents cités de chaque demande, et soutient l'évaluation d'un incident, sans contenir ni question ni réponse.
- Un journal indisponible bloque l'assistant au lieu de le laisser servir sans trace.
- Les règles du module 6 sont écrites avant que l'authentification n'existe.

Négatives :

- Le journal ne prouve pas qu'une réponse servie était exempte de renseignements personnels ; cette preuve vient de la garde de sortie et des évaluations.
- Même arrondi à l'heure, un horodatage croisé avec un horaire de quart peut encore réduire le nombre de personnes possibles : c'est pourquoi le journal est traité comme une donnée de niveau 4 et lu seulement par le RPRP et le personnel informatique sous son autorité.
- La purge s'exécute à chaque ajout et par `alverno access purge` : sans aucune demande pendant une longue période, un fichier échu reste en place jusqu'au prochain ajout ou à la prochaine purge manuelle. La purge précède l'écriture de l'enregistrement : si elle échoue (fichier échu verrouillé, par exemple), rien n'est écrit et la demande reçoit `audit_unavailable`, sans enregistrement qui contredirait ce qui a été servi.
- Le journal ne permet pas de savoir qui a posé une question ; c'est voulu tant que le module 6 n'existe pas.
