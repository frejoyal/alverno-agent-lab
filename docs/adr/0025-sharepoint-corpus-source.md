# ADR 0025 — Bibliothèque SharePoint simulée comme source du corpus Qualité (module 6a)

- **Statut** : proposé le 2026-11-11, en attente de l'approbation de l'[EFVP du module 6](../efvp/m365-module.md)
- **Date** : 2026-11-11

## Contexte

La documentation Qualité d'Alverno vit, dans une PME réelle, dans une bibliothèque SharePoint : versions, étiquettes de confidentialité, permissions par groupe. Le labo lit aujourd'hui `data/corpus/`, un dossier de fichiers Markdown. Le module 6 doit montrer comment l'agent Qualité lirait SharePoint sans changer ses réponses ni élargir ce qu'il voit.

Le responsable du dépôt a choisi, le 2026-11-11, le volet dépôt seulement : aucun locataire Microsoft 365 réel, aucune connexion à Microsoft Graph. La bibliothèque est donc **simulée** par un export versionné dans le dépôt, à la forme des métadonnées de Microsoft Graph.

Cet ADR ne cite aucun énoncé juridique ; l'analyse est dans l'[EFVP du module 6](../efvp/m365-module.md) ; **ceci n'est pas un avis juridique**.

## Décision

### Export simulé

`data/m365/sharepoint/library.json` décrit la bibliothèque « Documentation qualité » du site « Qualité » : pour chaque élément, l'identifiant, le nom, le chemin, la version, la date de modification, l'étiquette `eTag`, l'étiquette de confidentialité et les groupes qui ont le droit de lecture. Le contenu de chaque élément est le fichier correspondant de `data/corpus/`, désigné par son empreinte SHA-256 : l'export ne recopie aucun texte.

Les étiquettes sont celles de [loi25.md](../loi25.md), traduites en noms d'étiquettes Microsoft Purview fictifs : « Général » (niveau 2) et « Restreint — client » (spécifications `SPEC-*`). Aucun élément n'a d'étiquette de niveau 3 ou plus.

### Synchronisation

`alverno m365 sync` lit l'export et écrit le corpus synchronisé dans un dossier choisi (par défaut `.cache/m365/corpus/`, hors du dépôt) :

1. un élément est retenu seulement si le groupe de service de l'agent (« Agent Qualité — lecture ») figure parmi ses lecteurs ;
2. un élément est retenu seulement si son étiquette est autorisée pour la source (« Général » et « Restreint — client » pour l'agent local) ;
3. le contenu doit correspondre à l'empreinte de l'export, sinon la synchronisation s'arrête ;
4. une deuxième synchronisation ne réécrit que les éléments dont l'`eTag` a changé, et retire ceux qui ont disparu de l'export ; un manifeste (`sync-manifest.json`) garde l'`eTag` et l'empreinte de chaque élément.

**Non-régression** : avec l'export versionné, le corpus synchronisé est identique, octet pour octet, à `data/corpus/`. L'agent Qualité qui lit le corpus synchronisé donne donc les mêmes réponses.

### Ce qui n'est pas simulé

Microsoft Graph, l'authentification de l'application auprès de Microsoft Entra ID et la pagination des changements (`delta`) ne sont pas exécutés. Le connecteur réel est décrit dans le [guide de déploiement](../m365/deployment-guide.md) : application à permission de site (`Sites.Selected`) limitée au seul site Qualité, en lecture.

## Solutions écartées

- **Locataire de développement Microsoft 365** : écarté par le responsable du dépôt pour ce module ; le guide de déploiement décrit les étapes.
- **Lire SharePoint à chaque question** : ajoute une dépendance en ligne à chaque réponse et une trace de chaque question chez le fournisseur ; la synchronisation garde l'inférence locale.
- **Permission `Sites.Read.All`** : donne accès à tous les sites du locataire ; `Sites.Selected` limite l'application au site Qualité.

## Conséquences

- Positives : le chemin SharePoint est démontré et testé sans compte réel ; les réponses de l'agent ne changent pas.
- Négatives : la forme de l'export suit Microsoft Graph à la date de rédaction ; un changement de l'interface réelle exigerait d'adapter le connecteur, ce que la simulation ne peut pas détecter.
