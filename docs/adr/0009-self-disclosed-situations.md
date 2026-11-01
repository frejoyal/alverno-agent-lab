# ADR 0009 — Situations que la personne divulgue à son propre sujet

- **Statut** : accepté, sous réserve de la confirmation de la décision de gestion D6 par la présidente ([EFVP, section 7](../efvp/hr-module.md#7-avis-du-responsable-décision-et-approbation))
- **Date** : 2026-11-01

## Contexte

Une bonne part des questions RH décrivent la situation de la personne qui écrit :

- une grossesse : « Je suis enceinte, à quel congé ai-je droit ? » ;
- un harcèlement subi : « Mon superviseur [NOM] me harcèle, que faire ? » ;
- une blessure : « Je me suis blessé pendant mon quart de soir, que dois-je faire ? » ;
- une maladie : « Je suis malade depuis lundi, dois-je fournir un billet médical ? ».

Ces questions contiennent des renseignements que l'échelle du labo classe au niveau 5 (santé, accidents du travail) ou qui en sont très proches (un harcèlement subi). Or [docs/loi25.md](../loi25.md) place le niveau 5 hors du périmètre des agents tant qu'une EFVP et une décision de la direction ne l'autorisent pas. Le filtre du module 1, lui, refuse toute question qui contient « congé de maladie » ou « arrêt de travail ».

Refuser ces questions ne protège rien : la personne a déjà tapé le renseignement, et le refus la prive précisément des réponses les plus utiles (congés, absences, marche à suivre en cas de harcèlement ou d'accident). Le vrai risque est la **persistance** : que la situation se retrouve dans un journal, une trace, un rapport ou un artefact public.

## Décision

### Répondre à partir de la politique (décision D6)

Une situation sensible que la personne divulgue à son propre sujet reçoit une réponse tirée des politiques RH, qui sont de niveau 2 et ne contiennent aucun dossier. La couche de l'[ADR 0006](0006-deterministic-privacy-layer.md) classe la question comme `policy`, au sujet de la personne elle-même (`subject="self"`), niveau demandé 2 : la réponse ne révèle rien d'autre que la politique.

### Garder le mot qui décrit la situation

Le mot qui décrit la situation (« enceinte », « harcèle », « blessé », « malade ») **n'est pas caviardé**. La recherche en a besoin : c'est lui qui oriente vers la bonne politique (congés parentaux, prévention du harcèlement, accident du travail, absences pour maladie). Un marqueur générique comme [SANTÉ] effacerait le seul signal qui distingue ces politiques, et le grand modèle de langage (LLM) ne saurait plus laquelle appliquer. Les identifiants structurés de la même question (nom, numéro d'assurance sociale, téléphone, etc.) restent caviardés selon l'ADR 0006 : dans « Mon superviseur [NOM] me harcèle », le nom du superviseur n'atteint ni la recherche ni le LLM quand il est détecté.

### Traiter en mémoire seulement, ne jamais consigner

- Le texte de la question ne vit qu'en mémoire, le temps de la demande, dans le processus `alverno` et dans la mémoire du serveur Ollama local jusqu'au déchargement du modèle.
- Le journal des accès ([ADR 0008](0008-minimal-access-log.md)) ne garde ni la question, ni une empreinte, ni sa longueur. Une situation n'est pas un type de renseignement détecté : elle n'apparaît même pas dans `pi_types`.
- Les traces RH sont nettoyées (`scrub_trace`) et les rapports d'évaluation ne contiennent ni le texte des questions ni les réponses brutes du modèle.
- Le renseignement ne sert qu'à répondre à la question que la personne a posée, c'est-à-dire à la fin pour laquelle elle l'a fourni ([art. 4](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:4), [5](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:5) et [12](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:12), paraphrase), et il n'en reste rien à détruire après la demande ([art. 23](https://www.legisquebec.gouv.qc.ca/fr/document/lc/P-39.1#se:23)). C'est l'analyse retenue par l'EFVP, rédigée en tenant compte de la *Loi sur la protection des renseignements personnels dans le secteur privé*, état du droit au 31 octobre 2026 ; ce n'est pas un avis juridique.

### Une décision de la direction inscrite dans l'EFVP

Ce traitement en mémoire d'un contenu de niveau 5 exige une décision de la direction, comme le prévoit [docs/loi25.md](../loi25.md). Cette décision, D6, est inscrite à la section 7 de l'[EFVP du module RH](../efvp/hr-module.md#7-avis-du-responsable-décision-et-approbation) : **acceptée par défaut le 2026-11-01**, valeur retenue à la conception, et **à confirmer ou à renverser par la présidente** à la lecture de l'EFVP. Un renversement exigerait une nouvelle version de l'EFVP et du présent ADR. Rôles et consultation du RPRP : voir l'EFVP, section 2. Dans ce labo, le responsable du dépôt joue le rôle de la haute direction et doit confirmer cette décision.

### Ce qui reste refusé

- **Le dossier d'un tiers**, même décrit par sa situation : « La soudeuse enceinte du quart de jour est-elle en retrait préventif ? » et « Le cariste est-il en congé de maladie ? » sont refusées (`person_targeted`). Divulguer sa propre situation est permis ; questionner celle d'un collègue ne l'est pas.
- **La consultation de son propre dossier** : « Combien de jours de vacances me reste-t-il ? » ou « Pourquoi ma paie de jeudi dernier est-elle plus basse ? » sont refusées (`own_record`). L'assistant n'a accès à aucun dossier, y compris celui de la personne qui écrit ; le message de refus la dirige vers le service des ressources humaines (`rh@alverno.example`) et, pour consulter ou faire rectifier ses renseignements, vers le RPRP (`confidentialite@alverno.example`).
- **Les agrégats sur des personnes réelles** : « Combien d'employés sont en arrêt de travail ? » est refusée.

La personne impliquée qui demande comment agir reste servie, même si un tiers est mentionné : la question sur le harcèlement ci-dessus reçoit la marche à suivre de la politique de prévention du harcèlement, qui désigne la conseillère principale en ressources humaines pour recevoir une plainte (la directrice des ressources humaines si la plainte la concerne), ainsi que l'adresse `signalement@alverno.example`.

## Solutions écartées

- **Refuser toute situation sensible** : ne protège rien de ce que la personne a déjà tapé, et retire les réponses les plus utiles de l'assistant, notamment en cas de harcèlement.
- **Caviarder la situation en [SANTÉ]** : casse la recherche ; la question ne permet plus de choisir entre les politiques de congé, d'absence et d'accident.

## Conséquences

Positives :

- L'assistant répond aux questions de congé, d'absence, d'accident et de harcèlement, qui sont au cœur de sa raison d'être.
- Rien du texte de la question ne persiste dans les artefacts du module.

Négatives :

- Un contenu de niveau 5 transite en mémoire par le modèle local. Le serveur Ollama peut journaliser des invites (prompts) si son mode de débogage (`OLLAMA_DEBUG`) est actif ; l'EFVP inscrit ce journal, la mémoire du serveur Ollama, l'historique du terminal et le défilement du terminal à son inventaire. `hr ask` lit la question sur l'entrée standard et avertit quand elle est passée en argument.
- Un nom que la détection manque (écrit en minuscules, par exemple) atteint aussi le modèle local. La garde de sortie retient une réponse qui le reprend avec une majuscule au milieu d'une phrase (règle `proper_noun`) ; une reprise en minuscules ou en début de phrase n'est pas captée. C'est un manque publié (ADR 0006).
- Le jeu de référence RH contient des questions de ce type (`self_disclosure`) et vérifie qu'elles sont servies ; un changement de règle qui les refuserait fait échouer la porte ([ADR 0010](0010-hr-evaluation-protocol.md)).
- Toute liaison au système d'information RH, toute authentification (module 6) ou tout modèle hébergé déclencherait une réévaluation de cette décision dans l'EFVP.
