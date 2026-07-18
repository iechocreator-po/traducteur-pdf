# Notes de version — toledo (Traducteur PDF)

## 2026-07-18 — Moteur de traduction unifié + workflow chapitre par chapitre

Refonte de fiabilité de la traduction et des outils de gestion des documents,
branche `feat/moteur-traduction-unifie`.

### Corrections importantes
- **Moteur de traduction unifié** : fusion des deux moteurs divergents (document
  entier vs par chapitres) en un seul. Règle le bug récurrent de la traduction par
  chapitre (« tourne sans fin, pas de statut, impossible de relancer ») : la
  progression avance désormais **au grain du sous-morceau** (la barre ne fige plus).
- **Reprise fiable inter-session** : un job en pause, interrompu ou coupé par un
  arrêt du serveur redevient reprenable depuis « Nouveau document ».
- **Appariement TOC PDF ↔ contenu corrigé** : sur les PDF à signets, la majorité
  des chapitres recevaient par erreur le contenu du « Half-Title » (traduction
  faussée/tronquée). Chaque chapitre reçoit maintenant son vrai contenu.
- **ETA réaliste** : l'estimation avant lancement n'est plus ~6× trop basse
  (découpage aligné sur le moteur, temps par morceau plus honnête) ; l'ETA en cours
  se recale sur le débit réel.

### Nouveautés
- Section **« Vos traductions »** (dans « Nouveau document ») : liste tous les
  documents (en cours, en pause, interrompus, terminés). Par document : **Pause**,
  **Reprendre**, **➕ Chapitres** et **Supprimer** (retire du registre, garde les
  fichiers).
- **Traduction chapitre par chapitre** : « ➕ Chapitres » ouvre un sélecteur
  **inline sous le document** ; les chapitres déjà traduits sont verrouillés
  (« ✓ déjà traduit »), on ne coche que les nouveaux (flux additif).
- **Traductions planifiées** : bouton **Retirer** sur chaque ligne (tout statut) ;
  un job déclenché avec succès est auto-purgé (plus de « Déclenché » fantôme).
- **Feedback des actions** : « Lancer la traduction » grise pendant le lancement ;
  la barre affiche « N/M chapitres » à gauche de « X/Y morceaux ».
- **Anti-doublon** : lancer une traduction retire le document du lot (il vit dans
  « Vos traductions »).

### Connu / à suivre
- Le bouton **Supprimer** peut rester visible/actif pendant une traduction en cours
  (item K de la roadmap).
- Parité **macOS** de « Vos traductions » différée (item J). v1 web uniquement.
