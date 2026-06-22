# Historique des ventes par tracker — Design

## Contexte

Sur la page d'un tracker (frontend, `app/index.html`), on veut un nouvel onglet
"Historique des ventes" listant en détail les annonces du tracker (prix, titre,
état, marque, favoris, date d'ajout, date de vente), avec scroll infini,
recherche, filtres, tri et export CSV — à la manière de Recop.

Recop propose 3 sous-onglets : Ventes / Retirés / Invendus. Notre modèle de
données actuel ne distingue pas une vente d'un retrait : `niche_items.sold_at`
est posé dès qu'un item disparaît du scan, qu'il ait été vendu ou simplement
supprimé par le vendeur. Pour égaler Recop, on ajoute une vraie détection.

## 1. Modèle de données

### Nouvelles colonnes sur `niche_items`

| Colonne | Type | Description |
|---|---|---|
| `nb_favoris` | INTEGER | Déjà présent dans le JSON Vinted (`favourite_count`) côté scraper, jamais stocké pour les niches. |
| `etat` | TEXT | Condition de l'article. Confirmé en direct via l'API Vinted : le champ `status` du JSON contient déjà la valeur exacte (ex. `"Neuf avec étiquette"`). |
| `sold_status` | TEXT NULL | `'vendu'` \| `'retire'`. Rempli uniquement quand `sold_at` est posé. |

### Détection vendu vs retiré

Vérifié en direct sur l'API Vinted pendant le brainstorming :
- Un item présent dans la recherche catalogue a `status` (= état/condition) et `favourite_count`.
- Quand un item disparaît du scan (logique existante de `mark_niche_items_sold`),
  avant de poser `sold_at`, on fait un `GET` sur son `url` stockée en base
  (déjà disponible, pas de construction d'URL nécessaire) :
  - **HTTP 404** ("La page n'existe pas") → `sold_status = 'retire'` (le
    vendeur a supprimé l'annonce — Vinted supprime totalement la page).
  - **HTTP 200** (la page reste accessible — Vinted conserve les pages des
    annonces vendues, avec un badge "Vendu" affiché à l'utilisateur) →
    `sold_status = 'vendu'`.

### Catégories de l'onglet "Historique des ventes"

- **Ventes** : `sold_at IS NOT NULL AND sold_status = 'vendu'`
- **Retirés** : `sold_at IS NOT NULL AND sold_status = 'retire'`
- **Invendus** : `sold_at IS NULL`

### Données historiques

Les items déjà marqués `sold_at` avant ce changement n'ont pas de
`sold_status` connu. Par défaut, ils sont classés `'vendu'` (= comportement
actuel implicite, qui ne distinguait pas). Seules les futures transitions
bénéficient de la vraie distinction vendu/retiré. Pas de backfill rétroactif
par re-vérification HTTP des anciens items (trop coûteux, valeur limitée).

### Limite de volume

La vérification HTTP par item ajoute de la charge sur Vinted. Plafond de 50
vérifications par niche par cycle de scan (20 min), avec une pause courte
entre appels (même esprit que le `sleep(3)` existant entre niches dans
`run_niche_scans`). Ce plafond couvre largement le volume réel de
ventes/retraits par tracker à chaque cycle.

## 2. API Backend

### `GET /niches/{id}/history`

Paramètres :
- `status` (obligatoire) — `vendu` | `retire` | `actif`
- `search` — texte libre, recherche sur titre + marque (`ILIKE`)
- `marque` — filtre exact sur une marque
- `etat` — filtre exact sur la condition
- `sort` — `recent` (défaut, par `first_seen` desc) | `prix_asc` | `prix_desc` | `vente_recent` (par `sold_at` desc, pertinent pour Ventes/Retirés uniquement)
- `limit` (défaut 30) / `offset` (défaut 0) — pagination scroll infini

Réponse : `{ "items": [...], "total": N }`. Chaque item :
`{ id, titre, prix, photo, url, marque, taille, etat, nb_favoris, first_seen, sold_at }`.

L'endpoint vérifie que la niche appartient à l'utilisateur authentifié (même
pattern que `/niches/{id}/items` et `/niches/{id}/stats` existants).

### `GET /niches/{id}/history/facets`

Paramètres : `status` (obligatoire, même valeurs que ci-dessus).
Réponse : `{ "marques": [...], "etats": [...] }` — valeurs distinctes
présentes dans cette catégorie, pour remplir les dropdowns de filtre sans
proposer de valeurs vides.

### `GET /niches/{id}/history/export`

Paramètres : `status`, `search`, `marque`, `etat` (mêmes sémantiques que
`/history`, sans pagination — toutes les lignes correspondantes).
Réponse : CSV (`Content-Type: text/csv`), colonnes : Titre, Prix, État,
Marque, Favoris, Date d'ajout, Date de vente. Généré en streaming pour éviter
de charger jusqu'à 5000 lignes en mémoire d'un coup (plan Expert).

## 3. Frontend (`vintedspy-frontend/app/index.html`)

### Nouvel onglet

À côté de "Statistiques" : bouton `nd-tab-history` ("Historique des ventes"),
panneau `nd-panel-history`, suit le pattern existant de `switchDetailTab`.

### Sous-onglets

Ventes / Retirés / Invendus — boutons simples au-dessus de la liste,
rechargent la liste avec le `status` correspondant. "Ventes" est le sous-onglet
par défaut à l'ouverture.

### Barre d'outils

- Champ recherche (titre/marque), debounce ~300ms.
- Dropdown "Toutes les marques" (rempli via `/facets`).
- Dropdown "Tous les états" (rempli via `/facets`).
- Dropdown tri (Plus récent / Prix ↑ / Prix ↓).
- Bouton "Exporter" → navigation directe vers l'URL `/history/export` (pas de
  `fetchWithTimeout`, pour ne pas couper un flux long).

Changer de sous-onglet, recherche, filtre ou tri réinitialise la pagination
(`offset = 0`) et recharge la liste rafraîchit aussi les facets.

### Liste de cartes

Réutilise le style `vs-card` existant, avec une mise en page détaillée par
carte :
- Photo (ou placeholder emoji existant si pas de photo)
- Titre + prix en gros + badge état (couleur verte pour les états "neuf")
- Ligne Marque / État
- Icône cœur + nombre de favoris
- "Ajouté le DD/MM/YYYY" + "Vendu le DD/MM/YYYY" (ce dernier masqué dans
  l'onglet Invendus, où `sold_at` est toujours nul)

### Scroll infini

Réutilise le pattern déjà en place sur le Feed (`IntersectionObserver` +
sentinel), avec son propre état (`historyOffset`, `historyLoading`,
`historyDone`), réinitialisé à chaque changement de sous-onglet/recherche/
filtre/tri.

### États vides

Message dédié par sous-onglet :
- Ventes : "Aucune vente enregistrée."
- Retirés : "Aucun retrait détecté."
- Invendus : "Aucune annonce active."

## 4. Gestion des erreurs & cas limites

- **Vérification HTTP de statut qui échoue** (timeout, 429, erreur réseau) :
  fallback `'vendu'` par défaut, pas de retry agressif. Si l'item reste avec
  `sold_status IS NULL` alors que `sold_at` est déjà posé, on retentera la
  vérification au scan suivant.
- **Rate limit Vinted (429)** pendant la vérification : on arrête les
  vérifications pour ce cycle (même comportement que les autres appels
  existants), reprise au cycle suivant.
- **Historique vide** dans une catégorie : message vide dédié, pas d'erreur.
- **Filtres qui ne correspondent à rien** après changement de sous-onglet :
  liste vide normale, pas de message d'erreur spécifique.
- **Export volumineux** : streaming côté backend, pas de timeout client
  court qui couperait le téléchargement.
- **Réconciliation d'offset pendant un scroll en cours** : non géré
  explicitement — l'historique de ventes change rarement en cours de
  session, contrairement au Feed temps réel. Pas de mécanisme dédié.

## 5. Tests

**Backend** :
- Classification vendu/retiré : mock des réponses HTTP Vinted (200 → vendu,
  404 → retiré, erreur/429 → fallback vendu sans crash).
- `/niches/{id}/history` : filtres, tri, pagination (offset/limit corrects,
  `total` cohérent).
- `/niches/{id}/history/export` : contenu CSV correct, toutes les lignes
  incluses (pas seulement une page), respect des filtres.

**Frontend** :
- Vérification manuelle en navigateur : changement de sous-onglet, recherche,
  filtres, tri, scroll infini, export déclenche un téléchargement, états
  vides corrects par sous-onglet.
- Pas de suite de tests automatisée frontend dans ce projet (HTML/JS simple,
  pas de framework de test en place) — cohérent avec l'existant.
