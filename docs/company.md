# Fabrications Alverno inc. — profil de l'entreprise

> **Entreprise entièrement fictive.** Fabrications Alverno inc., ses employés, ses clients, ses documents et ses chiffres sont inventés pour les besoins du laboratoire. Toute ressemblance avec une entreprise réelle serait fortuite. Drummondville est une ville réelle ; l'entreprise n'y existe pas. Aucune personne n'est nommée : on parle de rôles.

## Fiche d'identité

| Élément | Valeur |
|---|---|
| Raison sociale | Fabrications Alverno inc. (fictive) |
| Siège et usine | Parc industriel de Drummondville, Centre-du-Québec |
| Fondation | 1994 |
| Propriété | Entreprise familiale, 2e génération ; la présidente, fille du fondateur, dirige depuis 2015 |
| Effectif | 118 employés |
| Activité | Fabrication métallique sur mesure en sous-traitance pour OEM |
| Certifications | ISO 9001:2015 ; CWB selon CSA W47.1 (division 2) ; soudage selon CSA W59 |
| Domaine Internet fictif | `alverno.example` |
| Langue de travail | Français |

## Ce que fait Alverno

Alverno fabrique des pièces et des sous-assemblages métalliques sur plans clients, en petites et moyennes séries (de 1 à environ 500 unités par commande). L'entreprise ne conçoit pas de produits propres : elle transforme les dessins de ses clients en pièces conformes, livrées à temps.

Procédés :

- **Découpe laser** de tôle (acier doux, inox, aluminium), avec logiciel d'imbrication (*nesting*).
- **Pliage** sur presses plieuses CNC.
- **Usinage CNC** : centres d'usinage 3 et 5 axes, tours CNC.
- **Soudage** GMAW, FCAW et GTAW (TIG) sur acier, inox et aluminium, par soudeurs qualifiés CWB.
- **Finition** : peinture en poudre (ligne de prétraitement et four de cuisson), passivation de l'inox, ébavurage.
- **Assemblage mécanique** et emballage pour expédition.

## Marchés et clients

Les clients sont des fabricants d'équipement d'origine du Québec et du nord-est de l'Amérique du Nord. Dans le corpus, ils portent des noms clairement inventés et marqués comme fictifs ; dans cette documentation, on les désigne par segment :

| Segment | Exemples de pièces | Exigences particulières |
|---|---|---|
| Machinerie agricole et forestière | châssis, supports de vérins, protecteurs | soudures structurales, peinture extérieure résistante aux UV et au sel |
| Équipement agroalimentaire en inox | trémies, convoyeurs, cadres sanitaires | inox 304/316L, soudures meulées et passivées, rugosité de surface contrôlée |
| Armoires et boîtiers électriques | boîtiers, panneaux, plaques de montage | tolérances de pliage, fini peint uniforme, mise à la terre |
| Remorques et équipements de transport | timons, traverses, supports | soudures critiques, traçabilité matière complète |

## Organisation

Environ 118 personnes, sur deux quarts de travail (jour et soir) :

| Fonction | Effectif approx. | Rôles types |
|---|---|---|
| Direction et administration | 12 | présidence, finances, approvisionnement, ressources humaines |
| Ventes et estimation | 6 | estimateurs, chargés de projet |
| Ingénierie et programmation | 8 | dessinateurs CAO, programmeurs CNC et laser |
| Production | 78 | soudeurs, machinistes, opérateurs laser et plieuses, peintres, assembleurs, chefs d'équipe |
| Qualité | 7 | directeur qualité, techniciens et inspecteurs, inspecteur en soudage certifié |
| Maintenance | 4 | mécaniciens et électricien d'entretien |
| Expédition et réception | 3 | commis à l'expédition, cariste |

Le service Qualité relève de la présidence. Il gère le système qualité ISO 9001, les qualifications CWB, l'étalonnage des instruments, l'inspection (réception, en cours, finale, premier article) et le cycle non-conformité / action corrective (NC/CAPA).

## Systèmes d'information

| Système | Usage | Remarque pour le labo |
|---|---|---|
| ERP manufacturier infonuagique | commandes, gammes, ordres de fabrication, achats, inventaire | futures intégrations (service client) |
| GMAO | entretien préventif et correctif des machines | module maintenance prévu |
| Microsoft 365 | courriel, Teams ; **SharePoint** héberge la documentation qualité | source réelle du corpus dans un vrai déploiement |
| CAO SolidWorks | dessins et modèles 3D | hors du périmètre actuel |
| Logiciel d'imbrication laser | programmation de la découpe | hors du périmètre actuel |
| SIRH | dossiers des employés, paie, formations | **renseignements personnels** : sensibilité élevée, module RH séparé |

Aucun nom de produit commercial n'est requis pour reproduire le labo : l'ERP, la GMAO et le SIRH sont décrits génériquement.

## Documentation qualité (corpus du jalon 1)

Le corpus `data/corpus/` reproduit, à petite échelle, la structure documentaire d'un système ISO 9001 :

| Préfixe | Type | Exemples de sujets |
|---|---|---|
| `POL` | politique | politique qualité, classification de l'information et protection des renseignements personnels, évaluation des fournisseurs |
| `QP` | procédure | maîtrise des documents, inspection du premier article, étalonnage, gestion des NC et CAPA |
| `IT` | instruction de travail | critères visuels de soudure CSA W59, mesure d'épaisseur de peinture en poudre, passivation de l'inox |
| `SPEC` | spécification client | exigences de fini et de tolérance propres à un segment client |
| `NC` | rapport de non-conformité | cas fictifs avec cause, disposition et action corrective |
| `FORM` | gabarit | rapport de non-conformité, rapport d'inspection premier article |

Chaque document déclare sa version et sa date d'entrée en vigueur. Quelques documents existent volontairement en deux versions contradictoires ; la plus récente fait foi, et le document récent l'indique explicitement.

## Problèmes d'affaires visés par le labo

1. **Accès à l'information qualité** au poste de travail, sur les deux quarts, sans dépendre de la disponibilité du service Qualité (jalon 1).
2. **Réduction des erreurs de version** : appliquer la bonne révision d'une spécification client.
3. **Intégration des nouveaux employés** dans un contexte de pénurie de main-d'œuvre spécialisée.
4. **Préparation aux audits** ISO 9001 et CWB : retrouver rapidement la preuve documentaire.
5. Plus tard : demandes RH courantes (avec protection des renseignements personnels), suivi client, maintenance, tableau de bord pour la direction.

## Contraintes de la direction

- Budget de PME : pas d'équipe de science des données, peu d'appétit pour des abonnements par utilisateur coûteux.
- Données sensibles pour les clients (dessins, spécifications) : préférence nette pour un traitement local.
- Obligations de la Loi 25 sur les renseignements personnels (voir [loi25.md](loi25.md)).
- Écosystème Microsoft 365 déjà en place : le déploiement final doit s'y intégrer (voir [roadmap.md](roadmap.md)).
