# Handoff : Refonte UI iTraducteur — direction "Workflow" (retenue)

## Aperçu
Refonte de l'app desktop (macOS, voir `interface_actuelle.png`) qui convertit un PDF en Markdown, traduit, génère un audio (TTS), et exporte en PDF/clone des voix. L'app actuelle est un formulaire unique vertical mélangeant toutes les options. Cette refonte la remplace par **3 modules** organisés par flux de travail (import → lecture/exploitation → configuration technique), avec divulgation progressive (mode simple par défaut, mode avancé optionnel).

## À propos du fichier de design
`iTraducteur - Workflow.dc.html` est une **référence de design** interactive (HTML/JS), **pas du code à copier tel quel**. La tâche : recréer ce design dans le vrai codebase de l'app (Swift/SwiftUI si l'app est native macOS, sinon le framework déjà en place), en respectant ses conventions.

## Fidélité
Haute fidélité sur structure, flux, et interactions. Le thème visuel (clair + sombre, tokens ci-dessous) est une proposition cohérente mais secondaire face au design system existant de l'app si celui-ci est mieux établi.

## Navigation générale
Barre supérieure fixe avec 3 onglets : **Nouveau document** / **Bibliothèque** / **Laboratoire**. À droite : bouton bascule **mode clair/sombre** (☾/☀) et un switch **Mode avancé** qui révèle/masque les réglages techniques dans le module Import.

## Module A — "Nouveau document" (import, multi-fichiers)
- **But** : importer un ou plusieurs PDF, les faire analyser automatiquement, lancer leur traduction en lot.
- Dropzone en haut (glisser-déposer plusieurs fichiers à la fois).
- Dès qu'un fichier est déposé : passe par un état "Analyse en cours…" puis "Prêt" avec 3 indicateurs (qualité source, temps estimé, nb de chapitres détectés).
- **Réglages du lot** (un seul bloc, appliqué à tous les fichiers) : langue cible ; si Mode avancé actif, sélection du moteur de conversion et du modèle IA.
- Bouton **"Lancer la traduction (N)"** traite tous les fichiers au statut "Prêt" en parallèle, avec pause/reprise globale.
- **Liste des fichiers du lot** : une ligne par fichier avec badge de statut (Analyse… / Prêt / En cours… / Terminé), barre de progression individuelle pendant le traitement, bouton de suppression (✕).
- Lien "Planifier plus tard" pour différer sans configurer.

## Module B — "Bibliothèque" (lecture / exploitation)
- **But** : consulter les documents déjà traduits, chapitre par chapitre, avec assistance IA et lecture audio.
- **Sidebar gauche** en 2 sections :
  1. **Document** — liste des documents disponibles (ceux terminés dans le lot + un doc par défaut), cliquable pour changer de document actif.
  2. **Chapitres** — table des matières du document actif, cliquable pour naviguer.
- **Zone de lecture** : bandeau en haut affichant le document actuellement ouvert (badge type + nom), puis titre + corps du chapitre sélectionné.
- **Barre audio** collée en bas de la zone de lecture : bouton lecture/pause, barre de progression, temps écoulé/total, bouton "Sauvegarder l'audio".
- **Panneau IA à droite** : bouton "Générer les 5 points clés" (affiche une liste à puces une fois généré) et bouton "Générer un quiz" (affiche des questions/réponses une fois généré).

## Module C — "Laboratoire" (configuration technique, isolée du flux principal)
- **Glossaire** : zone de texte (un terme par ligne, termes à ne jamais traduire) + bouton Enregistrer — s'applique automatiquement à toutes les traductions une fois sauvegardé.
- **Voix personnalisées** : liste des voix clonées disponibles (nom + durée d'échantillon) + lien "Créer une voix".

## Interactions clés
- Import : dépôt → analyse (auto, ~1s simulé) → prêt → lancement en lot → progression individuelle → statut "Terminé", document alors disponible dans la Bibliothèque.
- Mode avancé : switch global, révèle les selects techniques (moteur de conversion, modèle IA) uniquement dans le module Import.
- Mode sombre/clair : bouton dédié, bascule l'intégralité des tokens de couleur (voir ci-dessous), état global de l'app.
- Sélection de document et de chapitre dans la Bibliothèque : deux listes indépendantes dans la sidebar, la sélection active est surlignée.
- Génération de résumé/quiz : actions à la demande (pas automatique), affichent un résultat simulé une fois cliquées.

## Gestion d'état à prévoir côté app
- File d'attente d'import : liste de fichiers avec `id`, `nom`, `statut` (analyse/prêt/traitement/terminé), `progression`, métadonnées d'analyse (qualité, ETA, nb chapitres).
- Réglages de lot partagés : langue cible, moteur de conversion, modèle IA (visibles seulement si mode avancé).
- Bibliothèque : liste des documents traduits disponibles + document actif + chapitre actif par document.
- Lecteur audio : position de lecture, état play/pause (persister la position comme tout lecteur média).
- Panneau IA : état généré ou non pour résumé et quiz, par document/chapitre.
- Glossaire : texte, persistant, appliqué par défaut aux traductions.
- Voix clonées : liste (nom, durée d'échantillon), état de l'assistant de création (si repris du prototype précédent).
- Préférence de thème (clair/sombre) et mode avancé : à persister (ex: préférences utilisateur locales).

## Design tokens
**Clair (par défaut)** : fond `oklch(97% 0.006 95)` ; surfaces `100%/94%/90%` (même teinte) ; bordures `88%`. Texte `oklch(22% 0.01 95)` (principal), `45%/60%` (atténués). Accent `oklch(56% 0.15 250)`. Succès `oklch(52% 0.13 150)`. Attention `oklch(60% 0.15 70)`. Violet (voix/IA) `oklch(52% 0.14 300)`.

**Sombre** : fond `oklch(16% 0.015 255)` ; surfaces `21%/25.5%/29%` ; bordures `33%`. Texte `oklch(96% 0.005 255)` (principal), `72%/58%` (atténués). Accent `oklch(66% 0.16 255)`. Succès `oklch(72% 0.14 150)`. Attention `oklch(80% 0.14 85)`. Violet `oklch(70% 0.14 300)`.

Rayons : cartes 12-18px, contrôles 8-10px, pastilles 5-7px. Police : Manrope (titres, 700-800), système (corps). Espacements : gap 10-22px entre blocs.

## Fichiers
- `iTraducteur - Workflow.dc.html` — prototype interactif complet (3 modules, mode avancé, mode sombre).
- `support.js` — runtime du prototype (ne pas porter dans l'app cible).
- `interface_actuelle.png` — capture de l'interface existante à remplacer, pour comparaison avant/après.
