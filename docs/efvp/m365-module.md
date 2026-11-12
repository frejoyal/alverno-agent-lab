# EFVP — Agent Qualité dans Microsoft 365 et Copilot Studio (module 6)

> **Avertissement : ceci n'est pas un avis juridique.** Évaluation des facteurs relatifs à la vie privée rédigée dans le cadre d'un laboratoire, pour une entreprise entièrement fictive (Fabrications Alverno inc.). Ses documents, employés, clients et équipements sont inventés ; les domaines se terminent par `.example`. Elle a été rédigée en tenant compte de la *Loi sur la protection des renseignements personnels dans le secteur privé* (RLRQ, c. P-39.1), état du droit au 1er novembre 2026 (date de mise à jour du texte consolidé revue le 10 novembre 2026 pour le module 5), et suit la structure du [gabarit](template.md), tiré du guide de la CAI sur l'EFVP (version 3.1, avril 2024). Chaque énoncé juridique renvoie à une clé du [registre RH](../hr-legal-sources.md) ou du [registre du module 3](../ops-legal-sources.md), écrite entre accents graves ; le module 6 n'ajoute aucune clé. Les renseignements sur les services de Microsoft viennent de pages publiques lues le 2026-11-11 ; ils peuvent changer. Aucune personne n'est nommée : on parle de rôles.

| Élément | Valeur |
|---|---|
| Version | 1.0 |
| Date | 2026-11-11 |
| Statut | version 1.0 en attente de l'avis du RPRP et de l'approbation |
| Moment | rédigée avant l'implémentation, quand aucun code `src/` du module 6 n'existe |
| Gabarit | [template.md](template.md) |
| Volets | 6a, bibliothèque SharePoint simulée comme source du corpus ; 6b, API locale de l'agent Qualité avec jeton de l'organisation ; 6c, agent Copilot Studio publié dans Teams, décrit par des fichiers |
| Lecteurs visés | le responsable de la protection des renseignements personnels (RPRP), la présidente, le soutien informatique |

**Les nombres de cette EFVP.** Chaque nombre est une borne préenregistrée ([ADR 0028](../adr/0028-m365-evaluation-protocol.md)), un compte prévu à la conception ou un prix publié, daté et sourcé. Aucun résultat n'est mesuré à cette date ; les résultats entreront dans la version 1.1.

## 0. Résumé

**Le projet.** Rejoindre les employés d'Alverno là où ils travaillent, dans Teams, sans sortir l'inférence ni les documents du poste. Le responsable du dépôt a choisi, le 2026-11-11, le **volet dépôt seulement** : aucun locataire Microsoft 365 réel n'est utilisé ; tout est simulé, décrit et vérifié dans le dépôt.

- **6a, source SharePoint** : la documentation Qualité est lue depuis un export simulé d'une bibliothèque SharePoint, avec ses étiquettes et ses permissions ; le corpus synchronisé est identique au corpus actuel ([ADR 0025](../adr/0025-sharepoint-corpus-source.md)).
- **6b, API locale** : l'agent Qualité, et lui seul, devient appelable par HTTPS depuis Microsoft 365, sur `127.0.0.1`, par la passerelle de données locale (connexion sortante), avec un jeton Microsoft Entra ID et un rôle d'application ([ADR 0026](../adr/0026-local-api-and-auth.md)).
- **6c, agent Copilot Studio** : un agent publié dans Teams, à sujets classiques, sans IA générative hébergée, qui appelle l'API par un connecteur personnalisé ([ADR 0027](../adr/0027-copilot-studio-agent.md)).

**Situation légale.**

| Article | Analyse | Conclusion |
|---|---|---|
| [Art. 3.3](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:3_3) (`lprp_efvp_systeme`) | Développement d'un système d'information : les employés écrivent des questions dans Teams ; ces questions peuvent contenir des RP par accident. | **EFVP requise**, produite avant le code. |
| [Art. 17](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:17) (`lprp_communication_hors_quebec`) | La question et la réponse transitent par Microsoft 365 et y sont conservées. Microsoft les stocke au Canada (régions Toronto et Québec) ; des services d'identité sont mondiaux, et Microsoft indique que le canal Teams peut transférer des données hors de la géographie choisie. Le fichier de l'employé (identité Entra ID) est déjà chez Microsoft pour Teams. | **Analyse produite** ([analyse de l'art. 17](../m365/article-17.md)) ; **déclenchée en cas de mise en service** pour l'identifiant de l'employé et pour tout RP écrit dans une question. Dans le labo, aucune donnée réelle n'est communiquée. |
| [Art. 18.3](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:18_3) (`lprp_mandataire_prestataire`) | Microsoft agit comme prestataire de services pour l'hébergement de Teams et de Copilot Studio. | Contrat écrit avec les mesures exigées, **à vérifier avant la mise en service** (contrat de services de Microsoft et addenda sur la protection des données). |
| [Art. 8](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:8) (`lprp_information_collecte`) | L'agent recueille la question de l'employé. | Avis au premier message (fins, conservation dans Teams, aucun RP à écrire, RPRP). |

**Ce que le projet affirme.** Six affirmations vérifiables (M1 à M6, [annexe B](#b-affirmations-et-preuves)). Les deux principales : l'API donne exactement les mêmes réponses que l'agent appelé directement (M2) ; elle refuse tout jeton invalide ou sans le rôle (M3).

**Risques résiduels principaux.** R1 (une question contient un RP, conservé dans Teams), R2 (lieu de traitement du canal Teams non établi par le dépôt) et R6 (une mise en service réelle activerait par erreur les fonctions génératives, traitées aux États-Unis) reposent sur l'avis, le refus de l'API, les réglages de l'environnement et la vérification avant mise en service. Ils sont soumis à la présidente (section 7).

**Avis et approbation.** Les dix décisions de conception (section 1), l'avis du RPRP et l'approbation de la haute direction sont **en attente** (section 7).

**Limites de l'évaluation.** Aucun locataire réel : Teams, Copilot Studio, la passerelle et la rétention de Microsoft 365 ne sont ni exécutés ni mesurés. Les renseignements sur les services de Microsoft viennent de pages publiques à une date donnée. Les prix sont indicatifs. Le règlement sur les incidents de confidentialité et celui sur l'anonymisation n'ont pas été analysés ([points incertains du registre du module 3](../ops-legal-sources.md#points-incertains)).

## 1. Description du projet et objet de l'EFVP

### Besoin et fins

Les employés de la production et de la qualité consultent les procédures dans le bureau ou sur un poste partagé ; ils utilisent Teams chaque jour. Les fins sont fixées avant toute collecte (art. 4 ; clé `lprp_collecte_interet_fins`) :

| Fin | Volet | Description | Niveau des données traitées |
|---|---|---|---|
| Q-S | 6a | Tenir le corpus Qualité à jour depuis la bibliothèque de l'entreprise | 2 (documents internes), et spécifications de clients pour l'agent local seulement |
| Q-A | 6b, 6c | Répondre à une question Qualité posée dans Teams, avec citations | 2 ; la question peut contenir un RP par accident |
| Q-J | 6b | Tenir le journal des accès de l'API (qui, quand, décision) | identifiant haché de l'employé |

Aucune fin ne vise à évaluer un employé : le journal ne sert qu'à la sécurité et à la réponse aux demandes d'accès.

### Fonctionnement

- **6a.** `alverno m365 sync` lit l'export simulé `data/m365/sharepoint/library.json`, garde les éléments que le groupe de service de l'agent peut lire et dont l'étiquette est permise, vérifie l'empreinte de chaque contenu et écrit le corpus synchronisé hors du dépôt.
- **6b.** `alverno m365 serve` expose `POST /v1/quality/ask` sur `127.0.0.1`. Un jeton Entra ID valide, émis pour l'API, du bon locataire et avec le rôle `Quality.Ask`, est exigé. La réponse vient de l'agent Qualité inchangé (recherche hybride, barrière, modèle local, vérification des citations).
- **6c.** Un agent Copilot Studio à sujets classiques appelle l'action `AskQuality` du connecteur personnalisé, qui passe par la passerelle de données locale. Les fonctions génératives et le transfert entre régions sont désactivés.

**Ce que le module ne fera jamais dans cette version.** Exposer les assistants RH, service à la clientèle, entretien ou direction ; ouvrir un port entrant ; activer les réponses génératives ou l'orchestration générative de Copilot Studio ; utiliser un locataire, un jeton ou une clé réels ; journaliser le texte d'une question ou d'une réponse.

### Objet de l'évaluation

- **Inclus** : la synchronisation, l'API et son contrôle d'accès, le journal de l'API, les fichiers de l'agent Copilot Studio, les flux de données vers Microsoft 365 décrits par ces fichiers, la gouvernance et le plan d'adoption.
- **Exclus** : l'agent Qualité lui-même (évalué par [loi25.md](../loi25.md) et le module 1) ; les autres agents ; Microsoft 365 Copilot ; le fonctionnement interne des services de Microsoft.
- **Hypothèses** : un locataire Microsoft 365 avec Teams existerait déjà dans l'entreprise ; l'identité des employés y est déjà gérée.

### Décisions de conception

Proposées par l'agent du dépôt le 2026-11-11 et **soumises au responsable du dépôt** :

1. Volet dépôt seulement : aucun locataire, jeton ou clé réels ; Teams, Copilot Studio et la passerelle sont décrits par des fichiers vérifiés par des tests.
2. Source : export simulé d'une bibliothèque SharePoint, sans texte recopié (empreintes des fichiers de `data/corpus/`) ; synchronisation qui respecte permissions et étiquettes ; corpus synchronisé identique au corpus actuel.
3. Connecteur réel décrit avec la permission `Sites.Selected` limitée au site Qualité, en lecture.
4. API limitée à l'agent Qualité (niveau 2) ; aucun autre agent exposé ; aucune route d'écriture.
5. Écoute sur `127.0.0.1` seulement ; passage par la passerelle de données locale, sans port entrant.
6. Jeton Microsoft Entra ID (RS256) vérifié : signature, émetteur, audience, locataire, dates, rôle `Quality.Ask` ; clés de test dans le labo.
7. Journal des accès de l'API selon l'[ADR 0008](../adr/0008-minimal-access-log.md), sans question ni réponse, identifiant de l'employé haché avec un sel du poste.
8. Agent Copilot Studio à sujets classiques, réponses et orchestration génératives désactivées, transfert entre régions désactivé, connecteur isolé par une stratégie de protection des données ; avis au premier message.
9. Porte `m365-fake` préenregistrée ([ADR 0028](../adr/0028-m365-evaluation-protocol.md)) ; portes existantes et empreintes inchangées ; nouvelles dépendances déclarées (`starlette`, `uvicorn`, `pyjwt[crypto]`) aux versions déjà verrouillées.
10. Branche `module-6-m365` partie de `main`, une PR à la fin, fusionnée quand la CI est verte.

## 2. Rôles et responsabilités

| Rôle | Responsabilité dans le projet | Consulté le |
|---|---|---|
| Présidente | Plus haute autorité ; a délégué par écrit la fonction de RPRP ([art. 3.1](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:3_1), `lprp_responsable_rprp`). Approuve l'EFVP et décide des risques résiduels. | à inscrire |
| Directeur des finances et de l'administration | **RPRP par délégation écrite.** Donne son avis sur cette EFVP ; vérifie le contrat avec Microsoft avant toute mise en service. | à inscrire |
| Responsable de la qualité | Propriétaire de la bibliothèque SharePoint et du contenu ; propriétaire de l'agent dans Teams. | non consulté à cette date |
| Soutien informatique | Administrateur de Microsoft 365 et de Power Platform ; installe la passerelle ; gère les groupes et le rôle `Quality.Ask`. | non consulté à cette date |
| Responsable du dépôt | Réalise le module et la documentation. | — |

Aucune autre consultation n'a eu lieu dans ce labo. **Les rôles sont simulés** : le responsable du dépôt joue le rôle de la présidente et du RPRP.

Boîtes de rôle : confidentialite@alverno.example (RPRP), qualite@alverno.example, soutien-ti@alverno.example.

## 3. Renseignements personnels et ampleur de l'évaluation

| Renseignement | Sensibilité (1-5) | Finalité | Quantité | Répartition | Support | Conservation | Accès (catégories de personnes) | Communication (destinataires, hors Québec ?) |
|---|---|---|---|---|---|---|---|---|
| Identité de l'employé dans le jeton (identifiant `oid`, nom) | 2 | Q-A, Q-J | un jeton par appel | mémoire de l'API | mémoire | fin de l'appel | le code | déjà chez Microsoft (Entra ID, stockage possiblement mondial) |
| Identifiant haché de l'employé | 2 | Q-J | une ligne par appel | journal de l'API | fichier | 90 jours (durée de l'[ADR 0008](../adr/0008-minimal-access-log.md)) | RPRP, soutien informatique | aucune |
| Texte de la question et de la réponse | 2 ; RP possible par accident | Q-A | par conversation | Teams, journal d'activité de l'agent ; mémoire de l'API | Microsoft 365 ; mémoire | selon la rétention de Teams et du journal d'activité ; fin de l'appel côté API | l'employé, les administrateurs de Microsoft 365 | **Microsoft** (prestataire) ; stockage au Canada, exceptions publiées |
| Documents Qualité | 2 (spécifications de clients : 3 contractuel) | Q-S | 31 documents | poste | fichiers | version courante | le code | aucune vers Microsoft dans ce module (la bibliothèque y est déjà) |

**Champs jamais envoyés à Microsoft par ce module.** Les documents, l'index, les extraits complets récupérés, les données RH, clients ou d'entretien ; le journal des accès de l'API.

**Ampleur.** Volume faible ; aucune donnée réelle dans le labo. Le facteur qui élève le risque est la conservation, chez Microsoft, de questions écrites librement par les employés.

## 4. Conformité aux obligations

| Obligation (paraphrase) | Article | Mesure | Statut |
|---|---|---|---|
| Responsable de la protection des RP, délégation écrite | 3.1 (`lprp_responsable_rprp`) | Délégation écrite faite au module 2 ; le RPRP donne son avis sur cette EFVP | en place (simulée) |
| Politiques et pratiques de gouvernance | 3.2 (`lprp_gouvernance`) | [Gouvernance](../m365/governance.md) : propriétaires, groupes, rétention de Teams, révision trimestrielle des accès | prévu (I5) |
| EFVP d'un système d'information | 3.3 (`lprp_efvp_systeme`) | Cette EFVP, avant tout code `src/` du module | en place |
| Intérêt sérieux et légitime ; fins fixées avant la collecte | 4 (`lprp_collecte_interet_fins`) | Fins Q-S, Q-A et Q-J fixées à la section 1 | en place |
| Collecte limitée aux RP nécessaires | 5 (`lprp_collecte_necessaire`) | Aucun champ de RP demandé ; questions avec RP refusées par l'API ; journal sans contenu | prévu (I2) |
| Information de la personne lors de la collecte | 8 (`lprp_information_collecte`) | Avis au premier message de la conversation | prévu (I3) |
| Mesures de sécurité raisonnables | 10 (`lprp_mesures_securite`) | Jeton Entra ID et rôle ; écoute locale ; passerelle sortante ; stratégie de protection des données | prévu (I2, I3) |
| Utilisation limitée aux fins de la collecte | 12 (`lprp_utilisation_fins`) | Le journal ne sert qu'à la sécurité et aux demandes d'accès ; aucune analyse par employé | prévu |
| Communication ou traitement hors Québec | 17 (`lprp_communication_hors_quebec`) | [Analyse de l'art. 17](../m365/article-17.md) ; fonctions génératives et transfert entre régions désactivés | analyse rédigée ; à confirmer avant mise en service |
| Mandataire ou prestataire de services | 18.3 (`lprp_mandataire_prestataire`) | Contrat écrit avec Microsoft, mesures de protection et de confidentialité ; vérifié par le RPRP | à vérifier avant mise en service |
| Destruction ou anonymisation une fois les fins accomplies | 23 (`lprp_destruction_anonymisation`) | Journal de l'API purgé après 90 jours ; rétention de Teams fixée par la gouvernance | prévu (I2, I5) |

## 5. Risques et stratégies d'atténuation

| # | Risque | Mesures | Preuve | Risque résiduel |
|---|---|---|---|---|
| R1 | Un employé écrit un RP dans une question ; la question est conservée dans Teams | Avis au premier message ; refus de l'API pour toute question avec un RP ; rétention de Teams limitée | Test du refus par l'API ; `api_parity` = 1,0 sur les questions du jeu | **moyen** : le texte reste dans Teams ; soumis à la présidente |
| R2 | Le canal Teams traite ou conserve des données hors de la géographie Canada | Environnement créé au Canada ; vérification du contrat et des engagements de résidence avant mise en service | Analyse de l'art. 17 ; liste de vérification du guide | **moyen** : non établi par le dépôt ; soumis à la présidente |
| R3 | L'API répond à quelqu'un qui n'est pas autorisé | Jeton Entra ID vérifié (signature, émetteur, audience, locataire, dates) et rôle `Quality.Ask` | `auth_bypass` = 0 sur 8 cas ; contrôle négatif | faible |
| R4 | L'API expose un autre agent ou des données de niveau 3 ou plus | Une seule route métier ; test de la liste des routes | `spec_api_coverage` = 1,0 ; test des routes | faible |
| R5 | Le journal de l'API garde le texte des questions | Schéma de l'ADR 0008 ; aucun champ de texte | `log_content_hits` = 0 ; contrôle négatif | faible |
| R6 | Une mise en service active les réponses génératives : le contenu serait traité aux États-Unis | `agent.yaml` sans fonction générative ; transfert entre régions désactivé ; guide de déploiement | `generative_flags` = 0 ; liste de vérification | **moyen** : dépend de l'administrateur ; soumis à la présidente |
| R7 | La synchronisation lit un document que l'agent ne devrait pas voir | Groupe de service et étiquettes vérifiés ; `Sites.Selected` limité au site Qualité | `sync_excluded_wrong_group` = 0 ; contrôle négatif | faible |
| R8 | La couche Microsoft 365 change les réponses de l'agent | Agent inchangé ; corpus synchronisé identique | `sync_identical` = 1 ; `api_parity` = 1,0 | faible |

## 6. Plan d'action

| Action | Responsable (rôle) | Échéance | Critère |
|---|---|---|---|
| Confirmer l'avis du RPRP | RPRP | Avant tout code | Avis daté à la section 7 |
| Approuver l'EFVP et décider des risques R1, R2 et R6 | Présidente | Avant tout code | Approbation datée ; ADR 0025 à 0028 passent à « accepté » |
| Réaliser la synchronisation, l'API, l'agent et la porte | Responsable du dépôt | Incréments I1 à I5 | Critère de sortie de l'[ADR 0028](../adr/0028-m365-evaluation-protocol.md) |
| Vérifier le contrat avec Microsoft et les engagements de résidence des données | RPRP | Avant toute mise en service | Vérification datée dans l'analyse de l'art. 17 |
| Fixer la rétention de Teams et du journal d'activité de l'agent | Soutien informatique et RPRP | Avant toute mise en service | Politique datée dans la gouvernance |
| Publier la version 1.1 | Responsable du dépôt et RPRP | Après l'incrément I5 | Résultats recopiés ; avis confirmé ; approbation |

| Réévaluation | Date ou déclencheur |
|---|---|
| Version 1.1 | Après les mesures |
| Mise en service dans un locataire réel | Avant la mise en service, avec la liste de vérification du guide |
| Activation d'une fonction générative, ou exposition d'un autre agent | Avant le changement |
| Revue périodique | Au plus tard le 2027-11-11 |

## 7. Avis du responsable, décision et approbation

### Avis du RPRP

**Avis proposé : favorable, sous conditions** (rédigé le 2026-11-11 ; **en attente de confirmation** par le responsable du dépôt dans le rôle du RPRP) :

1. aucune ligne de code `src/` du module 6 avant la confirmation de cet avis et l'approbation de cette version ;
2. aucun locataire, jeton ou clé réels dans le dépôt ni dans la CI ;
3. aucune mise en service avant la vérification du contrat avec Microsoft, de la rétention et des réglages de l'environnement (guide de déploiement) ;
4. l'API n'expose que l'agent Qualité ;
5. la version 1.1, avec les résultats mesurés, est soumise au RPRP.

Les lectures juridiques restent des lectures du labo : la signature de l'avis ne les transforme pas en avis juridique.

### Risques soumis à la présidente

Le module n'utilise aucun renseignement de niveau 5. La présidente est invitée à accepter, pour la durée du labo, les risques résiduels R1 (RP écrit dans une question et conservé dans Teams), R2 (lieu de traitement du canal Teams non établi par le dépôt) et R6 (activation par erreur d'une fonction générative lors d'une mise en service) : **en attente**.

### Approbation de la haute direction

| Élément | Contenu |
|---|---|
| Avis du RPRP | Favorable, sous conditions (proposé) ; en attente de confirmation |
| Décisions de conception 1 à 10 | En attente |
| Risques R1, R2 et R6 | En attente |
| Approbation de la version 1.0 | En attente |
| Approuvé par | La présidente ; dans ce labo, le responsable du dépôt dans ce rôle |
| Date | — |

| Version | Date | Changements |
|---|---|---|
| 1.0 | 2026-11-11 | Première version, rédigée avant l'implémentation |

## Annexes

### A. Modèle de menace

| Menace | Acteur | Vecteur | Barrière |
|---|---|---|---|
| Accès non autorisé | employé sans le rôle, personne externe | appel direct de l'API ou de l'agent | jeton et rôle (R3) ; aucune écoute externe |
| Fuite de contenu par le journal | administrateur, attaquant sur le poste | lecture du journal de l'API | journal sans texte (R5) |
| Traitement hors du Canada | configuration de l'environnement | fonction générative activée, transfert entre régions | réglages et guide (R2, R6) |
| Sur-exposition | erreur de développement | route vers un autre agent | une seule route, test (R4) |

Hors périmètre : un poste compromis, un locataire compromis, un fournisseur qui ne respecte pas son contrat.

### B. Affirmations et preuves

| # | Affirmation | Preuve |
|---|---|---|
| M1 | La synchronisation ne lit que ce que le groupe de l'agent peut lire, et reproduit le corpus actuel | `sync_identical` = 1 ; `sync_excluded_wrong_group` = 0 et son contrôle négatif |
| M2 | L'API donne les mêmes réponses que l'agent appelé directement | `api_parity` = 1,0 sur 50 questions |
| M3 | L'API refuse tout jeton invalide ou sans le rôle | `auth_bypass` = 0 sur au moins 8 cas ; `auth_false_reject` = 0 ; contrôle négatif |
| M4 | Le journal de l'API ne garde ni question ni réponse | `log_content_hits` = 0 et son contrôle négatif |
| M5 | Les fichiers de l'agent décrivent exactement l'API et n'activent aucune fonction générative | `spec_api_coverage` = 1,0 ; `topic_action_valid` = 1 ; `generative_flags` = 0 |
| M6 | Aucun témoin ne se retrouve dans les rapports | `canary_hits` = 0 |

### C. Bornes de la porte

Profil `m365-fake`, préenregistré dans l'[ADR 0028](../adr/0028-m365-evaluation-protocol.md) : chaque compte vaut 0, `api_parity` et `spec_api_coverage` valent 1,0, `sync_identical` et `topic_action_valid` valent 1, avec au moins 50 questions et 8 cas d'accès.

### D. Documents joints

- Décisions : [ADR 0025](../adr/0025-sharepoint-corpus-source.md) (source SharePoint), [ADR 0026](../adr/0026-local-api-and-auth.md) (API et jeton), [ADR 0027](../adr/0027-copilot-studio-agent.md) (agent Copilot Studio), [ADR 0028](../adr/0028-m365-evaluation-protocol.md) (protocole).
- Microsoft 365 : [analyse de l'art. 17](../m365/article-17.md), [gouvernance](../m365/governance.md), [plan d'adoption](../m365/adoption-plan.md), [comparaison](../m365/comparison.md), [guide de déploiement](../m365/deployment-guide.md).
- Sources juridiques : [registre RH](../hr-legal-sources.md) et [registre du module 3](../ops-legal-sources.md).
- Cadre : [loi25.md](../loi25.md) (échelle de sensibilité), [gabarit](template.md), [guide de la CAI](https://www.cai.gouv.qc.ca/uploads/pdfs/CAI_GU_EFVP.pdf).

*Document fictif à des fins de démonstration ; ne constitue pas un avis juridique.*
