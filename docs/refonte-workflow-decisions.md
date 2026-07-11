# Refonte « Workflow » (toledo_v2) — décisions d'implémentation

Décisions prises par Jean-Pierre (11/7/2026) sur les écarts entre le design
retenu (`toledo_v2/handoff_iTraducteur/`) et les fonctionnalités existantes.
Le handoff décrit 3 modules : **Nouveau document** (import) / **Bibliothèque**
(lecture) / **Laboratoire** (configuration technique).

## 1. Clonage de voix (« Voix personnalisées » / « Créer une voix »)

Feature **future**, conservée visible dans l'onglet **Laboratoire** derrière un
feature flag **activé** (`feature_flags.py`).

Comportement au clic sur « Créer une voix » :
1. Message : « La fonctionnalité est en développement — voulez-vous nous
   partager votre intérêt pour cette fonctionnalité ? »
2. Si oui → prompt de saisie d'un **email**.
3. Trace écrite dans un **log d'intérêt** côté backend (fonctionnalité, email,
   horodatage) pour mesurer la demande.

## 2. Export PDF

Même mécanique que le clonage de voix : bouton visible (feature flag activé),
message « en développement », capture d'intérêt (email) + trace dans le même
log d'intérêt.

### Backend commun aux points 1 et 2
- Route de capture d'intérêt (ex. `POST /api/interet` :
  `{fonctionnalite, email}`) → append dans un fichier de log local.
- Deux flags dans `feature_flags.py` (ex. `teaser_voix_personnalisees`,
  `teaser_export_pdf`) à `True`.

## 3. Features réelles absentes du design → onglet Laboratoire

À reloger dans **Laboratoire** :
- Choix **moteur / voix TTS** standard (Piper / Kokoro + dropdown des voix).
- **Extrait de test de voix** (écouter un court texte).
- **Reprise d'un job interrompu** au redémarrage (check-resume).
- **Journal d'erreurs** (`.errors.log` / avertissements des jobs).
- **Source Markdown directe** (le design ne montre que le dépôt de PDF).

## Rappels du mapping (validé le 11/7/2026)

Adaptations backend pour la Bibliothèque — **livrées le 11/7/2026** :
- ✅ `GET /api/bibliotheque` : registre `bibliotheque.json` (local, gitignoré)
  alimenté par `translation_runner` au lancement de chaque traduction, statut
  et progression lus depuis les `.state.json` (`services/bibliotheque.py`).
- ✅ `POST /api/chapitres/contenu` : contenu Markdown d'un chapitre par index.
- ✅ `nb_chapitres` dans `ResultatAnalyse` (signets PDF sinon titres Markdown).
- ✅ `POST /api/interet` + `services/interet.py` (log local horodaté) et flags
  `teaser_voix_personnalisees` / `teaser_export_pdf` à `True`.

Note : les documents traduits **avant** la mise en place du registre
n'apparaissent pas dans la Bibliothèque (pas de rétro-remplissage en v1) ;
relancer une traduction les y inscrit.

Points de vigilance :
- La file de jobs reste **séquentielle** (un seul job Ollama) : l'UI du lot
  affiche des progressions individuelles mais le traitement est l'un après
  l'autre — ne pas promettre du parallèle.
- Les modèles IA affichés sont les modèles **Ollama locaux** (pas GPT-4o /
  Claude comme dans le prototype).
- Vendorer la police Manrope (pas de Google Fonts — app 100 % locale).
- Le design system vendoré (`tokens.css` / `Theme.swift`) prime sur les tokens
  OKLCH du prototype (confirmé par le README du handoff).
- Le panneau IA de la Bibliothèque (« 5 points clés » / « Générer un quiz »)
  est servi par le backend Étude existant (`POST /api/etude`).
