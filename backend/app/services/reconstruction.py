"""
Reconstruction d'un document SCANNÉ traduit (Laboratoire › Scan multi-colonnes → traduction).

Deux sorties, écrites à côté de la traduction (comme toutes les sorties de l'app) :
  1. `<traduction>_mise_en_page.pdf` : même format de page que le scan, illustrations découpées
     dans le scan et replacées à LEUR position, texte traduit coulé dans les colonnes d'origine ;
  2. `<traduction>_interactif.html` : page autonome (aucune ressource externe) — lecture continue
     avec les illustrations, ou page par page avec le scan d'origine en regard, sommaire, thème, taille.

Chaîne : PDF scanné → (par page) illustrations + cadres de texte, mis en cache dans
`<base>_scan_colonnes_illustrations/` → paragraphes traduits rattachés à LEUR page source
(les titres de chapitre servent d'ancres) → PDF / HTML.

Limites assumées (affichées à l'utilisateur, jamais masquées) :
  - le texte traduit est plus long que l'original : la police est réduite page par page jusqu'à
    TAILLE_MIN_PT ; au-delà, le reste part sur une page « suite » plutôt que d'être tronqué ;
  - les petits pictogrammes inline du livre (⟦icône⟧ de l'OCR) ne sont pas recadrés : ils sont
    remplacés par un losange ;
  - seule la géométrie du scan est utilisée : aucun appel à Ollama ici.
"""

from __future__ import annotations

import base64
import difflib
import html as html_mod
import io
import os
import re
import time

from app.services import scan_colonnes
from app.services.persistance import ecrire_binaire_atomique, ecrire_json_atomique, ecrire_texte_atomique, lire_json_tolerant

SUFFIXE_SCAN = "_scan_colonnes"
VERSION_CACHE = 5           # à incrémenter quand la détection change (invalide le cache)
DPI_APERCU = 90              # scan d'origine embarqué dans le HTML (mode « Pages »)
QUALITE_ILLUS = 85
QUALITE_APERCU = 65
# Le scan type est enregistré à demi-taille (Cycle I : une double page = 587 pt de large, soit
# ~10 cm par page) : y couler le texte demanderait une police de 4 pt. La sortie est donc agrandie
# ×ECHELLE_SORTIE (même format, proportions identiques) pour retrouver la taille d'un vrai livre.
ECHELLE_SORTIE = 2.0
TAILLE_MAX_PT = 10.0
TAILLE_MIN_PT = 7.0
PAS_TAILLE_PT = 0.5
AIRE_MIN_ILLUS = 0.02        # fraction de la page en dessous de laquelle une zone n'est pas une illustration
COTE_MIN_ILLUS = 0.08        # idem pour chaque côté (écarte l'ombre de reliure, haute mais étroite)
# Texte pris pour une illustration (voir _ressemble_a_du_texte) : fond clair dominant + encre en petits morceaux.
SEUIL_FOND_TEXTE = 150                  # ton dominant (0–255) au-dessus duquel la zone a un fond de papier
ENCRE_TEXTE_MIN, ENCRE_TEXTE_MAX = 0.03, 0.40
HAUTEUR_MAX_CARACTERE = 0.045           # fraction de la hauteur de page (gros numéros de section compris)
LARGEUR_MAX_MOT = 0.09                  # fraction de la largeur de page
PART_ENCRE_EN_PETITS_MORCEAUX = 0.6
MARGE_HAUT, MARGE_BAS = 0.03, 0.93   # le texte n'est jamais coulé dans le titre courant / folio
NB_MAX_SUITES = 200          # par page source ; au-delà, échec explicite plutôt qu'un texte coupé
SEUIL_TITRE = 0.6            # ressemblance minimale pour rattacher un chapitre à un titre du scan
LANGUES_HTML = {"français": "fr", "anglais": "en", "espagnol": "es"}


class ReconstructionImpossible(Exception):
    """Le document ne peut pas être reconstruit (pas issu du scan, fichiers manquants…)."""


# ── Où sont les fichiers ─────────────────────────────────────────────────────

def localiser(chemin_traduit: str) -> dict:
    """
    Traduction → PDF scanné d'origine + dossier des pages du scan. Le chemin source vient de
    l'état de la traduction (`.state.json`) ; à défaut, il est dérivé du nom (`…_traduit_<modèle>.md`).
    """
    etat = lire_json_tolerant(os.path.splitext(chemin_traduit)[0] + ".state.json")
    source = etat.get("chemin_pdf") if isinstance(etat, dict) else None
    if not source:
        m = re.match(r"^(.*)_traduit(?:_[^/]*)?\.md$", chemin_traduit)
        source = f"{m.group(1)}.md" if m else None
    if not source or not source.endswith(f"{SUFFIXE_SCAN}.md"):
        raise ReconstructionImpossible(
            "Ce document ne vient pas du Scan multi-colonnes : la mise en page d'origine "
            "n'est reconstruite que pour un PDF scanné nettoyé dans le Laboratoire.")
    pdf = source[: -len(f"{SUFFIXE_SCAN}.md")] + ".pdf"
    sorties = scan_colonnes.chemins_sortie(pdf)
    if not os.path.isfile(pdf):
        raise ReconstructionImpossible(f"PDF d'origine introuvable : {pdf}")
    if not os.path.isdir(sorties["pages"]):
        raise ReconstructionImpossible("Les pages du scan ont été effacées : relancez le Scan multi-colonnes.")
    pages = sorted(int(m.group(1)) for f in os.listdir(sorties["pages"])
                   if (m := re.match(r"page-(\d{4})\.json$", f)))
    # Le dossier garde les pages des passages précédents (reprise) : seule la plage du scan qui a ÉCRIT le Markdown
    # compte. Scan 1-10 puis 5-7 terminé : les pages 1-4 et 8-10 n'ont rien à recevoir. Scan 5-7 arrêté : le Markdown
    # couvre toujours 1-10, et les 10 pages restent.
    reglages = scan_colonnes.reglages_du_markdown(scan_colonnes.lire_etat(pdf)) or {}
    if isinstance(reglages.get("page_debut"), int) and isinstance(reglages.get("page_fin"), int):
        pages = [n for n in pages if reglages["page_debut"] <= n <= reglages["page_fin"]]
    if not pages:
        raise ReconstructionImpossible("Aucune page scannée trouvée : relancez le Scan multi-colonnes.")
    base = os.path.splitext(chemin_traduit)[0]
    return {
        "pdf": pdf,
        "source_md": source,
        "double_page": reglages.get("double_page"),
        "langue_html": LANGUES_HTML.get(etat.get("langue_cible") if isinstance(etat, dict) else None, "und"),
        "dossier_pages": sorties["pages"],
        "dossier_illus": f"{os.path.splitext(pdf)[0]}{SUFFIXE_SCAN}_illustrations",
        "pages": pages,
        "sortie_pdf": f"{base}_mise_en_page.pdf",
        "sortie_html": f"{base}_interactif.html",
    }


# ── Géométrie d'une page : illustrations + cadres de texte ───────────────────

def detecter_illustrations(image) -> list[tuple[int, int, int, int]]:
    """
    Rectangles (x0, y0, x1, y1, en pixels) des illustrations d'une page scannée.

    Le texte, vu de loin, reste « clair » : moyenné sur une tuile, l'encre d'un paragraphe ne
    descend le gris que d'une vingtaine de niveaux sous le papier. Une illustration est soit
    nettement plus sombre, soit colorée. D'où : réduction ×8 (moyenne), médiane 9×9 (efface les
    lignes de texte isolées), seuil sur le gris OU la saturation, puis composantes connexes.
    Mesuré sur le Cycle I : le navire (p. 1) et la salle (p. 3) sont isolés, la p. 2 (texte seul)
    n'en a aucune ; sans le filtre de côté, l'ombre de reliure était prise pour une illustration.
    La saturation se compare à celle du PAPIER : un papier jauni (235, 215, 170) sature déjà à 70 et une
    limite absolue prenait la page entière pour une illustration, texte imprimé par-dessus.
    """
    import cv2
    import numpy as np

    rgb = np.array(image.convert("RGB"))
    h, w, _ = rgb.shape
    gris = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    sat = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)[..., 1]
    fond = float(np.percentile(gris, 85))
    sat_papier = float(np.median(sat[gris >= fond]))
    k = 8
    g = cv2.medianBlur(cv2.resize(gris, (w // k, h // k), interpolation=cv2.INTER_AREA), 9)
    s = cv2.medianBlur(cv2.resize(sat, (w // k, h // k), interpolation=cv2.INTER_AREA), 9)
    masque = ((g < fond - 35) | (s > sat_papier + 60)).astype(np.uint8)
    masque = cv2.morphologyEx(masque, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    masque = cv2.morphologyEx(masque, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    nb, _, stats, _ = cv2.connectedComponentsWithStats(masque)
    rects = []
    for i in range(1, nb):
        x, y, ww, hh, _ = stats[i]
        # Rognage : un bord dont la ligne (ou colonne) n'est remplie qu'à moitié est un bandeau
        # collé à l'illustration (titre de chapitre en couleur, mesuré p. 1), pas l'illustration.
        zone = masque[y:y + hh, x:x + ww]
        lignes = (zone.mean(axis=1) >= 0.5).nonzero()[0]
        cols = (zone.mean(axis=0) >= 0.5).nonzero()[0]
        if not len(lignes) or not len(cols):
            continue
        y, hh = y + int(lignes[0]), int(lignes[-1] - lignes[0] + 1)
        x, ww = x + int(cols[0]), int(cols[-1] - cols[0] + 1)
        x0, y0, x1, y1 = x * k, y * k, min(w, (x + ww) * k), min(h, (y + hh) * k)
        if (x1 - x0) * (y1 - y0) < AIRE_MIN_ILLUS * w * h:
            continue
        if x1 - x0 < COTE_MIN_ILLUS * w or y1 - y0 < COTE_MIN_ILLUS * h:
            continue
        if _ressemble_a_du_texte(gris[y0:y1, x0:x1], w, h):
            continue
        rects.append((int(x0), int(y0), int(x1), int(y1)))
    return sorted(rects, key=lambda r: (r[0] >= w // 2, r[1], r[0]))


def _ressemble_a_du_texte(gris, largeur_page: int, hauteur_page: int) -> bool:
    """
    Une zone assez sombre pour passer le seuil d'illustration peut n'être que du texte serré, ou du texte
    sur un fond grisé (encadrés, pages de règles). Mesuré sur le Cycle I (08/11/2026) : 18 zones sur 22
    étaient du texte, recollé en anglais dans le PDF recréé. Une vraie illustration a un ton dominant
    sombre ou moyen et presque pas d'encre franche ; un texte a un fond clair dominant, 10 à 16 % d'encre
    nettement plus noire que ce fond, et cette encre est faite de petits morceaux (lettres, mots). Ce
    dernier critère garde un dessin au trait sur papier blanc : ses traits longs sont connectés.
    """
    import cv2
    import numpy as np

    if gris.size == 0:
        return False
    hist = np.convolve(np.bincount(gris.ravel(), minlength=256), np.ones(9), "same")
    fond = int(np.argmax(hist))
    if fond < SEUIL_FOND_TEXTE:
        return False
    encre = (gris < fond - 80).astype(np.uint8)
    part = float(encre.mean())
    if not ENCRE_TEXTE_MIN <= part <= ENCRE_TEXTE_MAX:
        return False
    nb, _, stats, _ = cv2.connectedComponentsWithStats(encre, connectivity=8)
    if nb <= 1:
        return False
    petits = ((stats[1:, cv2.CC_STAT_HEIGHT] <= HAUTEUR_MAX_CARACTERE * hauteur_page)
              & (stats[1:, cv2.CC_STAT_WIDTH] <= LARGEUR_MAX_MOT * largeur_page))
    aires = stats[1:, cv2.CC_STAT_AREA]
    return float(aires[petits].sum()) / float(aires.sum()) >= PART_ENCRE_EN_PETITS_MORCEAUX


def _meme_zone(r, q) -> bool:
    """Deux rectangles désignent la même zone s'ils se recouvrent à plus de moitié (intersection / union)."""
    ix = max(0, min(r[2], q[2]) - max(r[0], q[0]))
    iy = max(0, min(r[3], q[3]) - max(r[1], q[1]))
    inter = ix * iy
    union = (r[2] - r[0]) * (r[3] - r[1]) + (q[2] - q[0]) * (q[3] - q[1]) - inter
    return union > 0 and inter / union >= 0.5


def _cadre_encre(gris, x0: int, x1: int) -> tuple[int, int, int, int]:
    """Rectangle de l'encre d'une demi-page (le papier vide autour n'est pas une illustration)."""
    zone = gris[:, x0:x1] < 200
    lignes, cols = zone.any(axis=1).nonzero()[0], zone.any(axis=0).nonzero()[0]
    if not len(lignes):
        return (x0, 0, x1, gris.shape[0])
    return (int(x0 + cols[0]), int(lignes[0]), int(x0 + cols[-1] + 1), int(lignes[-1] + 1))


def illustrations_page(image, mise: dict, reglage: dict | None = None) -> list[tuple[int, int, int, int]]:
    """
    Illustrations retenues pour une page : la détection, moins les zones que l'utilisateur a retirées
    dans l'aperçu, moins celles des demi-pages en mode 'image' ou 'ignorer' ; une demi-page 'image'
    devient UNE illustration (son encre entière), recopiée telle quelle dans le PDF recréé.
    """
    rects = detecter_illustrations(image)
    retirees = (reglage or {}).get("illustrations_retirees") or []
    rects = [r for r in rects if not any(_meme_zone(r, q) for q in retirees)]
    for moitie in mise["moities"]:
        if moitie["mode"] in ("image", "ignorer"):
            rects = [r for r in rects if not moitie["x0"] <= (r[0] + r[2]) // 2 < moitie["x1"]]
        if moitie["mode"] == "image":
            rects.append(_cadre_encre(mise["gris"], moitie["x0"], moitie["x1"]))
    return sorted(rects, key=lambda r: (r[0] >= mise["w"] // 2, r[1], r[0]))


def cadres_texte(mise: dict, illustrations: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    """
    Cadres où couler le texte traduit, DANS L'ORDRE DE LECTURE (demi-page gauche puis droite,
    colonnes de gauche à droite, haut en bas) : chaque colonne détectée par le scan, bornée à
    l'encre réelle, privée des bandes occupées par une illustration qui la recouvre.
    """
    gris, h = mise["gris"], mise["h"]
    cadres = []
    for moitie in mise["moities"]:
        for a, b in moitie["colonnes"]:
            lignes = ((gris[:, a:b] < 120).mean(axis=1) > 0.004).nonzero()[0]
            haut = max(int(lignes[0]) if len(lignes) else 0, int(h * MARGE_HAUT))
            bas = min(int(lignes[-1]) if len(lignes) else h, int(h * MARGE_BAS))
            intervalles = [(haut, bas)]
            for x0, y0, x1, y1 in illustrations:
                if min(b, x1) - max(a, x0) <= 0.3 * (b - a):
                    continue
                coupe0, coupe1 = y0 - 12, y1 + 12
                nouveaux = []
                for p, q in intervalles:
                    if coupe1 <= p or coupe0 >= q:
                        nouveaux.append((p, q))
                        continue
                    if coupe0 > p:
                        nouveaux.append((p, coupe0))
                    if coupe1 < q:
                        nouveaux.append((coupe1, q))
                intervalles = nouveaux
            cadres += [(a, p, b, q) for p, q in intervalles if q - p > 0.05 * h]
    return cadres


def _couleur_papier(image) -> list[int]:
    import numpy as np
    rgb = np.array(image.convert("RGB")).reshape(-1, 3)
    clair = rgb[rgb.sum(axis=1) >= np.percentile(rgb.sum(axis=1), 60)]   # >= : un papier uniforme laisserait sinon un ensemble vide
    return [int(v) for v in np.median(clair, axis=0)]


def preparer_geometrie(chemin_pdf: str, dossier: str, pages: list[int], double_page: bool | None = None) -> dict:
    """
    Géométrie + images de chaque page, en cache (une page déjà faite n'est jamais refaite tant que le PDF,
    VERSION_CACHE et le réglage `double_page` du scan n'ont pas changé). Retourne le manifeste {page: {...}}.
    """
    manifeste_chemin = os.path.join(dossier, "manifeste.json")
    signature = {"version": VERSION_CACHE, "mtime": os.path.getmtime(chemin_pdf), "dpi": scan_colonnes.DPI_RENDU,
                 "double_page": double_page}
    manifeste = lire_json_tolerant(manifeste_chemin)
    if not isinstance(manifeste, dict) or manifeste.get("signature") != signature:
        manifeste = {"signature": signature, "pages": {}}
    # Une page dont les régions ont été retouchées à la main depuis est refaite (le reste du cache tient).
    manuels = {n: scan_colonnes.reglage_page(chemin_pdf, n) for n in pages}
    a_faire = [n for n in pages if str(n) not in manifeste["pages"]
               or manifeste["pages"][str(n)].get("regions") != manuels[n]]
    if a_faire:
        os.makedirs(dossier, exist_ok=True)
        doc = scan_colonnes._ouvrir_pdf(chemin_pdf)
        try:
            for n in a_faire:
                image = scan_colonnes.rendre_page(doc, n)
                mise = scan_colonnes.analyser_mise_en_page(image, double_page, manuels[n])
                illus = illustrations_page(image, mise, manuels[n])
                fichiers = []
                for i, (x0, y0, x1, y1) in enumerate(illus):
                    nom = f"page-{n:04d}-illus-{i + 1}.jpg"
                    image.crop((x0, y0, x1, y1)).save(os.path.join(dossier, nom), "JPEG", quality=QUALITE_ILLUS)
                    fichiers.append({"rect": [x0, y0, x1, y1], "fichier": nom})
                apercu = f"page-{n:04d}-apercu.jpg"
                r = DPI_APERCU / scan_colonnes.DPI_RENDU
                image.resize((int(image.width * r), int(image.height * r))).save(
                    os.path.join(dossier, apercu), "JPEG", quality=QUALITE_APERCU)
                manifeste["pages"][str(n)] = {
                    "largeur": image.width, "hauteur": image.height,
                    "papier": _couleur_papier(image),
                    "illustrations": fichiers,
                    "cadres": [list(c) for c in cadres_texte(mise, illus)],
                    "apercu": apercu,
                    "regions": manuels[n],
                }
        finally:
            doc.close()
        ecrire_json_atomique(manifeste_chemin, manifeste)
    return manifeste


# ── Texte traduit rattaché à sa page ─────────────────────────────────────────

_RE_MARQUEUR = re.compile(r"<!-- === chapitre (\d+) : (.*?) === -->")


def _blocs_traduits(corps: str) -> list[tuple[str, str]]:
    """Corps Markdown d'un chapitre traduit → [(type, texte)] ('titre' | 'encadre' | 'para')."""
    blocs = []
    for morceau in re.split(r"\n\s*\n", corps):
        morceau = re.sub(r"<!--.*?-->", "", morceau, flags=re.S).strip()
        if not morceau:
            continue
        if morceau.startswith("#"):
            premiere, _, reste = morceau.partition("\n")
            blocs.append(("titre", premiere.lstrip("#").strip()))
            if reste.strip():
                blocs.append(("para", reste.strip()))
        elif morceau.startswith(">"):
            blocs.append(("encadre", " ".join(ligne.lstrip(">").strip() for ligne in morceau.splitlines())))
        else:
            blocs.append(("para", " ".join(morceau.splitlines())))
    return blocs


def chapitres_traduits(md: str) -> list[dict]:
    """[{index, titre_source, blocs}] — un seul chapitre implicite si le document n'a pas de marqueur."""
    parts = _RE_MARQUEUR.split(md)
    if len(parts) == 1:
        return [{"index": 0, "titre_source": None, "blocs": _blocs_traduits(md)}]
    return [{"index": int(parts[i]), "titre_source": parts[i + 1], "blocs": _blocs_traduits(parts[i + 2])}
            for i in range(1, len(parts), 3)]


def _norm(t: str) -> str:
    return re.sub(r"\W+", " ", t.lower()).strip()


def _aligner_titres(cibles: list[str], titres_scan: list[str]) -> list[int | None]:
    """
    Pour chaque titre de `cibles`, la position du titre du scan qui lui correspond (None si aucun), en respectant
    l'ordre des deux listes. L'alignement maximise la ressemblance TOTALE : prendre à chaque chapitre son meilleur
    titre, sans regarder la suite, laissait un faux titre lointain faire sauter tous les chapitres suivants.
    À total égal, le titre le plus proche gagne.
    """
    n, m = len(cibles), len(titres_scan)
    normes = [_norm(t) for t in titres_scan]
    scores = [[difflib.SequenceMatcher(None, _norm(c), t).ratio() for t in normes] for c in cibles]
    gain = [[0.0] * (m + 1) for _ in range(n + 1)]     # gain[i][j] : meilleur total pour cibles[i:] et titres[j:]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            pris = scores[i][j] + gain[i + 1][j + 1] if scores[i][j] >= SEUIL_TITRE else -1.0
            gain[i][j] = max(pris, gain[i + 1][j], gain[i][j + 1])
    resultat: list[int | None] = [None] * n
    i = j = 0
    while i < n and j < m:
        if scores[i][j] >= SEUIL_TITRE and scores[i][j] + gain[i + 1][j + 1] >= gain[i][j] - 1e-9:
            resultat[i] = j
            i, j = i + 1, j + 1
        elif gain[i][j + 1] >= gain[i][j] - 1e-9:
            j += 1
        else:
            i += 1
    return resultat


def titres_source(source_md: str) -> list[dict] | None:
    """Tous les chapitres du Markdown nettoyé, traduits ou non : [{titre, niveau}], dans l'ordre. None si illisible."""
    from app.services.pdf_extractor import identifier_chapitres
    try:
        return [{"titre": c["titre"], "niveau": c.get("niveau", 0)} for c in identifier_chapitres(source_md)]
    except (OSError, ValueError):
        return None


def repartir_par_page(dossier_pages: str, pages: list[int], md_traduit: str,
                      source: list[dict] | None = None) -> dict[int, list[tuple[str, str]]]:
    """
    Paragraphes traduits → page source. Les titres de chapitre (marqueurs de la traduction) sont
    retrouvés parmi les titres du scan, dans l'ordre (rapprochement approximatif : le scan stocke
    les titres AVANT réparation, ex. « 1 Garden » pour « 11 Garden »).
    `source` (titres_source) donne aussi les chapitres NON traduits : sans eux, un chapitre traduit seul
    n'a pas de borne et son texte s'étale jusqu'à la fin du livre. Dans un chapitre :
      - même nombre de paragraphes qu'à la source (cas mesuré le plus fréquent, 15 chapitres sur 20
        du Cycle I) → correspondance 1 pour 1, la page est exacte ;
      - sinon (paragraphes fusionnés par l'OCR ou le modèle) → position proportionnelle aux caractères.
    """
    plat = []   # (page, type, texte) dans l'ordre du document
    for n in pages:
        d = lire_json_tolerant(os.path.join(dossier_pages, f"page-{n:04d}.json"))
        if isinstance(d, dict):
            plat += [(n, k, t) for k, t in d.get("blocs", [])]
    if not plat:
        raise ReconstructionImpossible("Les pages du scan sont vides.")
    titres = [i for i, (_, k, _) in enumerate(plat) if k == "titre"]
    chapitres = chapitres_traduits(md_traduit)

    # 1. ancre de chaque chapitre dans `plat`. La liste de la source ne sert que si elle correspond bien aux
    #    marqueurs (Markdown nettoyé régénéré depuis ?) ; sinon on se limite aux chapitres traduits.
    if source and all(c["titre_source"] is not None and c["index"] < len(source)
                      and _norm(source[c["index"]]["titre"]) == _norm(c["titre_source"]) for c in chapitres):
        liste, rangs = source, [c["index"] for c in chapitres]
    else:
        liste = [{"titre": c["titre_source"] or "", "niveau": 0} for c in chapitres]
        rangs = list(range(len(chapitres)))
    if len(chapitres) == 1 and chapitres[0]["titre_source"] is None:   # chapitre implicite : tout le document
        ancres: list[int | None] = [0]
    else:
        ancres = [None if p is None else titres[p]
                  for p in _aligner_titres([e["titre"] for e in liste], [plat[i][2] for i in titres])]

    par_page: dict[int, list[tuple[str, str]]] = {n: [] for n in pages}
    for c, chap in enumerate(chapitres):
        rang = rangs[c]
        debut = ancres[rang]
        if debut is None:   # titre introuvable : on le rattache à la fin du chapitre précédent
            debut = max([a for a in ancres[:rang] if a is not None], default=0)
        # Fin : prochain chapitre de même niveau ou plus haut (un chapitre traduit contient ses sous-chapitres).
        suivants = [a for e, a in zip(liste[rang + 1:], ancres[rang + 1:])
                    if a is not None and a > debut and e["niveau"] <= liste[rang]["niveau"]]
        fin = suivants[0] if suivants else len(plat)
        source = [b for b in plat[debut:fin] if b[1] != "titre"] or [plat[debut]]
        traduits = chap["blocs"]
        corps = [b for b in traduits if b[0] != "titre"]
        if len(corps) == len(source):
            pages_corps = [s[0] for s in source]
        else:
            cumul, total = [], 0
            for s in source:
                total += len(s[2])
                cumul.append(total)
            t_total = sum(len(b[1]) for b in corps) or 1
            pages_corps, vu = [], 0
            for b in corps:
                milieu = (vu + len(b[1]) / 2) / t_total * total
                vu += len(b[1])
                j = next((j for j, v in enumerate(cumul) if v >= milieu), len(source) - 1)
                pages_corps.append(source[j][0])
        page_titre = plat[debut][0] if ancres[rang] is not None or not pages_corps else pages_corps[0]
        j = 0
        for k, t in traduits:
            if k == "titre":
                par_page[page_titre].append((k, t))
            else:
                par_page[pages_corps[j]].append((k, t))
                j += 1
    return par_page


# ── Rendu commun ─────────────────────────────────────────────────────────────

def inline_html(texte: str) -> str:
    """Markdown inline minimal → HTML sûr (échappé d'abord)."""
    t = html_mod.escape(texte, quote=False)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<i>\1</i>", t)
    t = t.replace(scan_colonnes.MARQUEUR_ICONE, '<span class="ico">◆</span>')
    return t


def _blocs_html(blocs: list[tuple[str, str]], ancre_chap=None) -> str:
    sortie = []
    for k, t in blocs:
        if k == "titre":
            ident = f' id="{ancre_chap(t)}"' if ancre_chap else ""
            sortie.append(f"<h3{ident}>{inline_html(t)}</h3>")
        elif k == "encadre":
            sortie.append(f'<div class="encadre"><p>{inline_html(t)}</p></div>')
        else:
            sortie.append(f"<p>{inline_html(t)}</p>")
    return "\n".join(sortie)



# ── 1. PDF mis en page ───────────────────────────────────────────────────────

_CSS_PDF = """
* {{ font-family: serif; }}
body {{ font-size: {taille}pt; line-height: 1.18; color: #1f1a14; text-align: justify; margin: 0; }}
p {{ margin: 0 0 {espace}pt 0; }}
h3 {{ font-size: {titre}pt; color: #7a2418; font-weight: bold; margin: {espace}pt 0 {espace}pt 0; text-align: left; }}
.encadre {{ background-color: #ffffff; border: 0.6pt solid #8a6d3b; padding: 2pt 3pt; margin: 0 0 {espace}pt 0; }}
.ico {{ color: #7a2418; }}
"""


def _css_pdf(taille: float) -> str:
    return _CSS_PDF.format(taille=taille, titre=round(taille * 1.25, 1), espace=round(taille * 0.3, 1))


def _tient(html: str, taille: float, cadres) -> bool:
    import pymupdf
    story = pymupdf.Story(html=html, user_css=_css_pdf(taille))
    for cadre in cadres:
        plus, _ = story.place(cadre)
        # Sans draw, le Story n'avance pas : chaque place remesure le début du texte, seul le premier
        # cadre compte et toute page qui dépasse une colonne tombe à la taille minimale. None = sans sortie.
        story.draw(None)
        if not plus:
            return True
    return False


def _cadres_suite(largeur: float, hauteur: float):
    import pymupdf
    m, g = largeur * 0.06, largeur * 0.04
    col = (largeur - 2 * m - 3 * g) / 4
    return [pymupdf.Rect(m + i * (col + g), hauteur * 0.07, m + i * (col + g) + col, hauteur * 0.92) for i in range(4)]


def _exiger_traduction_a_jour(loc: dict, chemin_traduit: str) -> None:
    """Un Markdown réécrit après la traduction ne décrit plus son texte : les pages seraient fausses, sans erreur."""
    source = loc["source_md"]
    if os.path.isfile(source) and os.path.getmtime(source) > os.path.getmtime(chemin_traduit):
        raise ReconstructionImpossible(
            "Le fichier nettoyé a été régénéré après cette traduction (nouveau scan) : "
            "relancez sa traduction pour reconstruire la mise en page.")


def generer_pdf(chemin_traduit: str) -> dict:
    """
    Écrit `<traduction>_mise_en_page.pdf`. Retourne {chemin, pages, illustrations, pages_suite,
    tailles} — `pages_suite` liste les pages dont le texte n'a pas tenu même à TAILLE_MIN_PT.
    """
    import pymupdf

    loc = localiser(chemin_traduit)
    _exiger_traduction_a_jour(loc, chemin_traduit)
    manifeste = preparer_geometrie(loc["pdf"], loc["dossier_illus"], loc["pages"], loc["double_page"])
    with open(chemin_traduit, encoding="utf-8") as f:
        par_page = repartir_par_page(loc["dossier_pages"], loc["pages"], f.read(), titres_source(loc["source_md"]))

    ech = 72 / scan_colonnes.DPI_RENDU * ECHELLE_SORTIE
    sortie = pymupdf.open()
    nb_illus, pages_suite, tailles = 0, [], {}
    for n in loc["pages"]:
        info = manifeste["pages"][str(n)]
        W, H = info["largeur"] * ech, info["hauteur"] * ech
        papier = tuple(v / 255 for v in info["papier"])
        page = sortie.new_page(width=W, height=H)
        page.draw_rect(page.rect, color=None, fill=papier)
        for ill in info["illustrations"]:
            x0, y0, x1, y1 = ill["rect"]
            page.insert_image(pymupdf.Rect(x0 * ech, y0 * ech, x1 * ech, y1 * ech),
                              filename=os.path.join(loc["dossier_illus"], ill["fichier"]))
            nb_illus += 1
        blocs = par_page.get(n) or []
        if not blocs:
            continue
        cadres = [pymupdf.Rect(a * ech, b * ech, c * ech, d * ech) for a, b, c, d in info["cadres"]]
        if not cadres:
            cadres = _cadres_suite(W, H)
        contenu = _blocs_html(blocs)
        taille = TAILLE_MAX_PT
        while taille > TAILLE_MIN_PT and not _tient(contenu, taille, cadres):
            taille = round(taille - PAS_TAILLE_PT, 2)
        tailles[n] = taille

        # Calque de texte : dessiné dans un document temporaire de même format, puis superposé.
        tampon = io.BytesIO()
        ecrivain = pymupdf.DocumentWriter(tampon)
        story = pymupdf.Story(html=contenu, user_css=_css_pdf(taille))
        dev = ecrivain.begin_page(pymupdf.Rect(0, 0, W, H))
        plus = 1
        for cadre in cadres:
            plus, _ = story.place(cadre)
            story.draw(dev)
            if not plus:
                break
        ecrivain.end_page()
        nb_suites = 0
        while plus and nb_suites < NB_MAX_SUITES:   # le reste continue sur une page « suite »
            dev = ecrivain.begin_page(pymupdf.Rect(0, 0, W, H))
            for cadre in _cadres_suite(W, H):
                plus, _ = story.place(cadre)
                story.draw(dev)
                if not plus:
                    break
            ecrivain.end_page()
            nb_suites += 1
        if plus:   # jamais de texte coupé sans le dire
            raise ReconstructionImpossible(f"Le texte de la page {n} ne tient pas en {NB_MAX_SUITES} pages de suite : "
                                           "le PDF n'a pas été généré.")
        ecrivain.close()
        calque = pymupdf.open("pdf", tampon.getvalue())
        page.show_pdf_page(page.rect, calque, 0)
        for s in range(1, calque.page_count):
            suite = sortie.new_page(width=W, height=H)
            suite.draw_rect(suite.rect, color=None, fill=papier)
            suite.show_pdf_page(suite.rect, calque, s)
            suite.insert_text((W * 0.06, H * 0.045), f"Suite de la page {n}", fontsize=9, color=(0.45, 0.4, 0.35))
        if calque.page_count > 1:
            pages_suite.append(n)
        calque.close()

    sortie.set_metadata({"title": os.path.basename(os.path.splitext(chemin_traduit)[0]),
                         "creator": "Traducteur PDF (toledo) — mise en page reconstruite"})
    ecrire_binaire_atomique(loc["sortie_pdf"], sortie.tobytes(garbage=3, deflate=True))
    sortie.close()
    return {"chemin": loc["sortie_pdf"], "pages": len(loc["pages"]), "illustrations": nb_illus,
            "pages_suite": pages_suite, "tailles": tailles}


# ── 2. HTML interactif ───────────────────────────────────────────────────────

def _data_uri(chemin: str) -> str:
    with open(chemin, "rb") as f:
        return "data:image/jpeg;base64," + base64.b64encode(f.read()).decode("ascii")


def _position_illustration(info: dict, ill: dict) -> float:
    """Part (0–1) du texte de la page qui PRÉCÈDE l'illustration dans l'ordre de lecture,
    estimée par la surface des cadres de texte situés avant elle."""
    x0, y0, x1, y1 = ill["rect"]
    milieu = info["largeur"] / 2
    moitie_ill = (x0 + x1) / 2 >= milieu
    avant = total = 0.0
    for a, b, c, d in info["cadres"]:
        aire = (c - a) * (d - b)
        total += aire
        moitie_cadre = (a + c) / 2 >= milieu
        # avant l'illustration : toute la demi-page précédente, et dans la même demi-page
        # les cadres qui se terminent au-dessus d'elle
        if moitie_cadre < moitie_ill or (moitie_cadre == moitie_ill and d <= y0 + 12):
            avant += aire
    return avant / total if total else 0.0


def generer_html(chemin_traduit: str, titre: str | None = None) -> dict:
    """Écrit `<traduction>_interactif.html` (autonome : images en data-URI). Retourne {chemin, pages, illustrations, octets}."""
    loc = localiser(chemin_traduit)
    _exiger_traduction_a_jour(loc, chemin_traduit)
    manifeste = preparer_geometrie(loc["pdf"], loc["dossier_illus"], loc["pages"], loc["double_page"])
    with open(chemin_traduit, encoding="utf-8") as f:
        par_page = repartir_par_page(loc["dossier_pages"], loc["pages"], f.read(), titres_source(loc["source_md"]))

    pages_html, sommaire, nb_illus = [], [], 0
    n = 0

    def ancre(t: str) -> str:
        """Une ancre par OCCURRENCE : deux « Introduction » sont deux entrées du sommaire."""
        sommaire.append((f"chap-{len(sommaire) + 1}", t, n))
        return sommaire[-1][0]

    for n in loc["pages"]:
        info = manifeste["pages"][str(n)]
        blocs = par_page.get(n) or []
        # illustrations insérées dans le flux à leur position estimée
        inserts = sorted(((_position_illustration(info, ill), ill) for ill in info["illustrations"]),
                         key=lambda x: x[0])
        total = sum(len(t) for _, t in blocs) or 1
        morceaux, cumul, i_ins = [], 0, 0
        for k, t in blocs:
            while i_ins < len(inserts) and inserts[i_ins][0] * total <= cumul:
                morceaux.append(_figure(loc, inserts[i_ins][1], n))
                i_ins += 1
            morceaux.append(_blocs_html([(k, t)], ancre))
            cumul += len(t)
        morceaux += [_figure(loc, ill, n) for _, ill in inserts[i_ins:]]
        nb_illus += len(inserts)
        pages_html.append(
            f'<section class="page" id="page-{n}" data-page="{n}">'
            f'<div class="page-tete"><span class="folio">Page {n}</span>'
            f'<button type="button" class="bascule-original" aria-pressed="false">Voir le scan d\'origine</button></div>'
            f'<div class="page-corps"><figure class="original" hidden>'
            f'<img loading="lazy" src="{_data_uri(os.path.join(loc["dossier_illus"], info["apercu"]))}" '
            f'alt="Scan d\'origine, page {n}"><figcaption>Scan d\'origine — page {n}</figcaption></figure>'
            f'<div class="texte">{"".join(morceaux) or "<p class=vide>(page sans texte)</p>"}</div></div></section>')

    titre = titre or os.path.basename(os.path.splitext(chemin_traduit)[0])
    toc = "".join(f'<li><a href="#{a}">{inline_html(t)}</a> <span class="p">p. {p}</span></li>' for a, t, p in sommaire)
    page = _GABARIT_HTML.replace("{{TITRE}}", html_mod.escape(titre)) \
        .replace("{{LANG}}", loc["langue_html"]) \
        .replace("{{SOMMAIRE}}", toc) \
        .replace("{{PAGES}}", "\n".join(pages_html)) \
        .replace("{{META}}", html_mod.escape(f"{len(loc['pages'])} page(s) · {nb_illus} illustration(s) · généré le "
                                             f"{time.strftime('%d/%m/%Y %H:%M')}"))
    ecrire_texte_atomique(loc["sortie_html"], page)
    return {"chemin": loc["sortie_html"], "pages": len(loc["pages"]), "illustrations": nb_illus,
            "octets": len(page.encode("utf-8"))}


def _figure(loc: dict, ill: dict, n: int) -> str:
    return (f'<figure class="illus"><img loading="lazy" src="{_data_uri(os.path.join(loc["dossier_illus"], ill["fichier"]))}" '
            f'alt="Illustration de la page {n}"><figcaption>Illustration — page {n}</figcaption></figure>')


def info(chemin_traduit: str) -> dict:
    """Disponibilité (rapide, sans rendu) + sorties déjà présentes."""
    try:
        loc = localiser(chemin_traduit)
    except ReconstructionImpossible as e:
        return {"disponible": False, "raison": str(e)}
    return {
        "disponible": True,
        "pdf_source": loc["pdf"],
        "pages": len(loc["pages"]),
        "pdf_genere": loc["sortie_pdf"] if os.path.isfile(loc["sortie_pdf"]) else None,
        "html_genere": loc["sortie_html"] if os.path.isfile(loc["sortie_html"]) else None,
    }


_GABARIT_HTML = r"""<!DOCTYPE html>
<html lang="{{LANG}}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{TITRE}}</title>
<style>
:root { --papier:#f4efe6; --surface:#fffdf8; --texte:#1f1a14; --doux:#6b6257; --accent:#7a2418; --bord:#d9cfbf; --taille:17px; }
:root[data-theme="dark"] { --papier:#16130f; --surface:#211d18; --texte:#ece4d6; --doux:#a89c8a; --accent:#e08a6f; --bord:#3a332a; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --papier:#16130f; --surface:#211d18; --texte:#ece4d6; --doux:#a89c8a; --accent:#e08a6f; --bord:#3a332a; } }
* { box-sizing:border-box; }
html { scroll-behavior:smooth; }
body { margin:0; background:var(--papier); color:var(--texte); font:var(--taille)/1.6 Georgia, "Iowan Old Style", "Palatino Linotype", serif; }
header { position:sticky; top:0; z-index:5; display:flex; flex-wrap:wrap; gap:8px; align-items:center; padding:10px 16px; background:var(--surface); border-bottom:1px solid var(--bord); font-family:-apple-system, system-ui, sans-serif; font-size:14px; }
header h1 { font-size:16px; margin:0 auto 0 0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:40vw; }
header button, header select { font:inherit; padding:5px 10px; border-radius:6px; border:1px solid var(--bord); background:var(--papier); color:var(--texte); cursor:pointer; }
header button[aria-pressed="true"] { background:var(--accent); color:#fff; border-color:var(--accent); }
.mise { display:grid; grid-template-columns:260px minmax(0,1fr); max-width:1280px; margin:0 auto; }
nav { position:sticky; top:52px; align-self:start; max-height:calc(100vh - 52px); overflow:auto; padding:16px; font-family:-apple-system, system-ui, sans-serif; font-size:13px; }
nav ul { list-style:none; padding-left:0; margin:6px 0; } nav li { margin:4px 0; } nav a { color:var(--texte); text-decoration:none; } nav a:hover { color:var(--accent); }
nav .p { color:var(--doux); font-size:11px; } nav .meta { color:var(--doux); font-size:11px; }
main { padding:16px 24px 80px; min-width:0; }
.page { background:var(--surface); border:1px solid var(--bord); border-radius:10px; padding:20px 28px; margin:0 0 20px; }
.page-tete { display:flex; justify-content:space-between; align-items:center; font-family:-apple-system, system-ui, sans-serif; font-size:12px; color:var(--doux); margin-bottom:8px; }
.bascule-original { font:inherit; border:1px solid var(--bord); background:transparent; color:var(--doux); border-radius:6px; padding:3px 8px; cursor:pointer; }
.page-corps { display:block; }
.texte { max-width:44em; margin:0 auto; text-align:justify; hyphens:auto; }
h3 { color:var(--accent); font-size:1.25em; margin:1.4em 0 .5em; text-align:left; scroll-margin-top:70px; }
.encadre { border:1px solid var(--bord); border-left:4px solid var(--accent); background:var(--papier); padding:.6em 1em; border-radius:6px; margin:1em 0; }
.encadre p { margin:0; }
.ico { color:var(--accent); font-size:.8em; }
figure { margin:1.2em 0; text-align:center; }
figure img { max-width:100%; height:auto; border-radius:6px; cursor:zoom-in; box-shadow:0 2px 10px rgba(0,0,0,.15); }
figcaption { font:12px -apple-system, system-ui, sans-serif; color:var(--doux); margin-top:4px; }
/* Mode « Pages » : scan d'origine en regard du texte traduit */
body.mode-pages .page-corps { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:24px; align-items:start; }
body.mode-pages .original { display:block !important; position:sticky; top:64px; margin:0; }
body.mode-pages .bascule-original { display:none; }
body.mode-lecture .page { border:0; background:transparent; padding:0 0 8px; border-radius:0; }
body.mode-lecture .page-tete .folio { opacity:.7; }
.vide { color:var(--doux); font-style:italic; }
mark { background:#f5d76e; color:#000; }
#loupe { position:fixed; inset:0; background:rgba(0,0,0,.85); display:none; align-items:center; justify-content:center; z-index:20; cursor:zoom-out; }
#loupe.ouvert { display:flex; } #loupe img { max-width:96vw; max-height:94vh; }
@media (max-width: 900px) { .mise { grid-template-columns:1fr; } nav { position:static; max-height:none; border-bottom:1px solid var(--bord); } body.mode-pages .page-corps { grid-template-columns:1fr; } body.mode-pages .original { position:static; } main { padding:16px; } .page { padding:16px; } header h1 { max-width:100%; } }
@media print { header, nav, .bascule-original, #loupe { display:none !important; } .mise { display:block; } .page { break-inside:avoid-page; border:0; } }
</style>
</head>
<body class="mode-lecture">
<header>
  <h1>{{TITRE}}</h1>
  <button type="button" data-mode="lecture" aria-pressed="true">Lecture continue</button>
  <button type="button" data-mode="pages" aria-pressed="false">Page par page + original</button>
  <input type="search" id="chercher" placeholder="Rechercher…" aria-label="Rechercher dans le texte" style="font:inherit;padding:5px 8px;border-radius:6px;border:1px solid var(--bord);background:var(--papier);color:var(--texte);width:150px">
  <button type="button" id="moins" aria-label="Réduire le texte">A−</button>
  <button type="button" id="plus" aria-label="Agrandir le texte">A+</button>
  <button type="button" id="theme" aria-label="Changer de thème">◐</button>
</header>
<div class="mise">
  <nav><strong>Sommaire</strong><ul>{{SOMMAIRE}}</ul><p class="meta">{{META}}</p></nav>
  <main>{{PAGES}}</main>
</div>
<div id="loupe" role="dialog" aria-label="Image agrandie"><img alt=""></div>
<script>
(function () {
  var racine = document.documentElement, corps = document.body;
  function lire(k, d) { try { return localStorage.getItem(k) || d; } catch (e) { return d; } }
  function ecrire(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
  function mode(m) {
    corps.className = "mode-" + m;
    document.querySelectorAll("[data-mode]").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.mode === m)); });
    ecrire("toledo-mode", m);
  }
  document.querySelectorAll("[data-mode]").forEach(function (b) { b.addEventListener("click", function () { mode(b.dataset.mode); }); });
  mode(lire("toledo-mode", "lecture"));
  var taille = parseFloat(lire("toledo-taille", "17"));
  function appliquerTaille() { racine.style.setProperty("--taille", taille + "px"); ecrire("toledo-taille", String(taille)); }
  document.getElementById("moins").onclick = function () { taille = Math.max(12, taille - 1); appliquerTaille(); };
  document.getElementById("plus").onclick = function () { taille = Math.min(26, taille + 1); appliquerTaille(); };
  appliquerTaille();
  var theme = lire("toledo-theme", "");
  if (theme) racine.dataset.theme = theme;
  document.getElementById("theme").onclick = function () {
    var sombre = racine.dataset.theme ? racine.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    racine.dataset.theme = sombre ? "light" : "dark"; ecrire("toledo-theme", racine.dataset.theme);
  };
  document.querySelectorAll(".bascule-original").forEach(function (b) {
    b.addEventListener("click", function () {
      var fig = b.closest(".page").querySelector(".original"), ouvert = fig.hidden;
      fig.hidden = !ouvert; b.setAttribute("aria-pressed", String(ouvert));
      b.textContent = ouvert ? "Masquer le scan d'origine" : "Voir le scan d'origine";
    });
  });
  var loupe = document.getElementById("loupe");
  document.addEventListener("click", function (e) {
    if (e.target.matches("figure img")) { loupe.querySelector("img").src = e.target.src; loupe.classList.add("ouvert"); }
    else if (e.target.closest("#loupe")) loupe.classList.remove("ouvert");
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") loupe.classList.remove("ouvert"); });
  // Recherche : surligne les occurrences et défile jusqu'à la première.
  var originaux = new Map();
  document.querySelectorAll(".texte p, .texte h3").forEach(function (el) { originaux.set(el, el.innerHTML); });
  var minuterie;
  document.getElementById("chercher").addEventListener("input", function (e) {
    clearTimeout(minuterie);
    var q = e.target.value.trim();
    minuterie = setTimeout(function () {
      var premier = null, re = q.length >= 2 ? new RegExp("(" + q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + ")(?![^<]*>)", "gi") : null;
      originaux.forEach(function (h, el) {
        el.innerHTML = re ? h.replace(re, "<mark>$1</mark>") : h;
        if (re && !premier && el.querySelector("mark")) premier = el;
      });
      if (premier) premier.scrollIntoView({ block: "center" });
    }, 250);
  });
})();
</script>
</body>
</html>
"""
