---
doc_id: FORM-001
title: "Gabarit — Rapport de non-conformité"
doc_type: form
department: qualite
version: "2.0"
effective_date: 2025-10-06
sensitivity: 2
---

## Utilisation

Ce gabarit structure le rapport de non-conformité ouvert dans l'ERP selon QP-003. Le technicien qualité le remplit dans un délai de 24 heures après la découverte de l'écart. Les champs marqués d'un astérisque (*) sont obligatoires pour l'enregistrement.

**Rappel** : aucun renseignement personnel. Les personnes sont désignées par leur titre de poste et les postes de travail par leur numéro (voir POL-002).

## Section 1 — Identification

| Champ | Contenu attendu |
|---|---|
| Numéro de NC* | Format NC-AAAA-NNN, attribué par l'ERP |
| Date d'ouverture* | AAAA-MM-JJ |
| Client | Nom de l'entreprise cliente, ou « interne » |
| Numéro de pièce et révision* | Selon le dessin |
| Numéro de lot* | Lot de fabrication ou lot de réception (R-AAMMJJ-NN) |
| Quantité touchée / quantité du lot* | Par exemple 4 / 12 |
| Étape de détection* | Réception, en cours, inspection finale, chez le client, audit |
| Détecté par* | Titre de poste seulement |

## Section 2 — Description

| Champ | Contenu attendu |
|---|---|
| Exigence* | Document et valeur exigée (par exemple SPEC-STR-01, 70 à 110 µm) |
| Constat* | Valeur mesurée ou description objective |
| Instrument utilisé | Numéro EQ-NNNN, étalonnage valide |
| Photos | Au moins une photo du défaut, sans personne visible |

## Section 3 — Classement

- [ ] NC mineure
- [ ] NC majeure (fonction, sécurité, caractéristique critique ou découverte chez le client)
- [ ] Plainte client

## Section 4 — Confinement

Décrire le tri effectué sur le stock, les en-cours et les pièces expédiées, avec les quantités trouvées conformes et non conformes.

## Section 5 — Disposition (décision du comité MRB)

- [ ] Retouche — méthode :
- [ ] Réparation — approbation écrite du client jointe
- [ ] Utiliser tel quel — approbation écrite du client jointe si la caractéristique figure au dessin du client
- [ ] Rebut
- [ ] Retour au fournisseur

Date de la décision du MRB et titre de poste du responsable qualité qui l'approuve.

## Section 6 — Réinspection

Résultat de la réinspection après la disposition, avec la référence de l'instrument et la date.

## Section 7 — Action corrective

- CAPA requise ? Oui / Non
- Motif : NC majeure, plainte client, récurrence (3 NC ou plus de même cause en 90 jours), autre
- Numéro de CAPA : format CAPA-AAAA-NN

## Section 8 — Fermeture

Date de fermeture (cible : 30 jours après l'ouverture) et confirmation que les enregistrements sont complets.

## Historique

La version 2.0 remplace la version 1.1. Modification : ajout de la section sur la réinspection et du rappel sur les renseignements personnels.
