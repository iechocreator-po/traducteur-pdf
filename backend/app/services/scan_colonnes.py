"""
Nettoyage d'un PDF SCANNÉ à plusieurs colonnes (Laboratoire, 11/10/2026).

Problème : un livre scanné porte souvent une couche texte OCR faite sur la page
entière — colonnes lues en travers, double page lue d'un bloc, pied de page
planté au milieu d'un paragraphe. Les extracteurs « texte » (pymupdf4llm,
marker) la reprennent telle quelle. Ici on l'IGNORE et on refait l'OCR
(Tesseract) région par région :

    page → (double page ? gouttière) → colonnes → segments texte/encadré
         → titres numérotés (bandes d'encre) → OCR par segment → Markdown

Principes (mesurés sur « Cycle I (Inward Odyssey) » : 3 pages d'échantillon, 15
titres sur 20 trouvés d'emblée puis 20/20 ; puis le livre complet, 61 pages, qui a
montré que les titres ne sont pas partout des sections consécutives) :

- les colonnes sont mesurées sur les bandes qui RESSEMBLENT à du texte (≥ 2
  gouttières verticales nettes) — une illustration n'en a pas, et la mesurer
  sur la moitié basse de la page échouait dès qu'une illustration y était ;
- un candidat-titre n'est retenu que si son OCR ressemble à un titre numéroté ;
- un numéro de section isolé et mal lu n'est corrigé que si ses DEUX voisins le
  confirment (« 10, 1, 12 » → 11), et chaque réparation est journalisée ;
- un symbole de jeu (icône) lu comme du bruit devient `⟦icône⟧` plutôt qu'un
  caractère inventé ; un « I » lu `|` redevient « I ».

Volontairement SANS LLM : la correction de l'OCR par un modèle local n'a gagné
qu'environ un point de mots reconnus pour 4 minutes de calcul, avec des
altérations de noms propres (voir CLAUDE.md, section « Scan multi-colonnes »).

Limites connues : seuils réglés sur un livre à 3 colonnes ; une page sans
gouttières détectées retombe en pleine largeur ; le texte posé sur une
illustration (bandeau de titre) n'est pas lu.
"""

import concurrent.futures
import datetime
import io
import os
import re
import shutil
import statistics as st
import subprocess
import tempfile
import time
import uuid

from app.services.job_manager import (
    enregistrer_job,
    est_annule,
    soumettre_travail,
    supprimer_job_registre,
)
from app.services.persistance import ecrire_json_atomique, lire_json_tolerant

DPI_RENDU = 200
ECHELLE_OCR = 3            # agrandissement avant Tesseract (mesuré : ×3 suffit, ×4/×5 sans gain)
CONFIANCE_MIN_LIGNE = 55   # sous ce seuil, la ligne est du bruit (illustration, ombre de reliure)
CONFIANCE_MIN_TITRE = 80   # idem pour une bande de titre (voir ocr_titre)
VERSION_ALGO = 2           # à incrémenter quand un résultat de page n'est plus valable (invalide le cache)
RATIO_DOUBLE_PAGE = 1.25   # largeur/hauteur au-delà duquel la page est une double page
TRAVAILLEURS_PAR_DEFAUT = 3
MARQUEUR_ICONE = "⟦icône⟧"
PONCTUATION_OK = set(".,;:!?—–-“”\"'’‘()…")
CONNECTEURS = {"the", "of", "a", "an", "and", "to", "in", "for", "on", "at", "by", "with"}

_RE_PIED = re.compile(r"^(\d{1,3}\s+\D{3,40}|\D{3,40}\s+\d{1,3})$")
_RE_TITRE = re.compile(r"^\d{1,2} [A-Z]\S*(\s\S+)*$")
# numéro d'entrée en gros caractères que Tesseract espace : « 0 2 8 1 As the… » → « 0281 As the… »
_RE_NUMERO_ECLATE = re.compile(r"^((?:\d ){2,5}\d)(?= [A-Z“\"])")


class ScanColonnesEchec(Exception):
    """Échec attendu (Tesseract absent, page illisible) — message affichable tel quel."""


# ── Primitives d'image ────────────────────────────────────────────────────────

def _numpy():
    import numpy as np
    return np


def _lisser(profil, taille: int):
    from scipy.ndimage import uniform_filter1d
    return uniform_filter1d(profil, taille)


def plages(masque, longueur_min: int) -> list[tuple[int, int]]:
    """Plages [debut, fin) consécutives où `masque` est vrai, d'au moins `longueur_min`."""
    sortie, debut = [], None
    for i, v in enumerate(list(masque) + [False]):
        if v and debut is None:
            debut = i
        elif not v and debut is not None:
            if i - debut >= longueur_min:
                sortie.append((debut, i))
            debut = None
    return sortie


def detecter_gouttiere(gris) -> int:
    """Abscisse de la gouttière d'une double page : colonne la plus vide autour du centre."""
    np = _numpy()
    h, w = gris.shape
    sombre = (gris < 120).astype(float)[int(h * 0.5):]
    profil = _lisser(sombre.mean(0), 9)
    bas, haut = int(w * 0.44), int(w * 0.56)
    zone = profil[bas:haut]
    # la zone la plus vide peut être large (marges + reliure) : on vise le MILIEU de la plus
    # longue plage vide plutôt que son premier pixel, pour ne pas couper contre une colonne
    plage = max(plages(zone <= zone.min() + 0.002, 1), key=lambda r: r[1] - r[0])
    return bas + (plage[0] + plage[1]) // 2


def detecter_colonnes(gris, x0: int, x1: int, bande: int = 50) -> list[tuple[int, int]]:
    """
    Bornes x des colonnes d'une demi-page. Ne vote que sur les bandes
    horizontales qui ressemblent à du texte (≥ 2 gouttières verticales nettes) :
    une illustration n'en a pas. Le 70ᵉ centile entre bandes évite qu'un
    encadré ou une marge irrégulière referme une gouttière réelle.
    """
    np = _numpy()
    h = gris.shape[0]
    profils = []
    for y in range(0, h - bande, bande // 2):
        d = (gris[y:y + bande, x0:x1] < 120).astype(float)
        if d.mean() > 0.30:
            continue
        p = _lisser(d.mean(0), 5)
        trous = [r for r in plages(p < 0.01, 12) if r[0] > 20 and r[1] < len(p) - 20]
        if len(trous) >= 2:
            profils.append(p)
    if not profils:
        return []
    profil = np.percentile(np.array(profils), 70, axis=0)
    fusion: list[tuple[int, int]] = []
    for a, b in plages(profil > 0.02, 40):
        if fusion and a - fusion[-1][1] < 14:
            fusion[-1] = (fusion[-1][0], b)
        else:
            fusion.append((a, b))
    return [(max(x0 + a - 6, 0), x0 + b + 6) for a, b in fusion if b - a > 120]


def decouper_segments(gris_colonne) -> list[tuple[str, int, int]]:
    """
    ('texte'|'encadre', y0, y1) selon le fond : papier ≈ 231, encadré blanc ≈ 255.
    Le fond est le 85ᵉ centile de chaque ligne, insensible à l'encre.
    """
    np = _numpy()
    fond = _lisser(np.percentile(gris_colonne, 85, axis=1), 5)
    boite = fond >= 248
    sortie, y = [], 0
    for a, b in plages(boite, 30):
        if a > y:
            sortie.append(("texte", y, a))
        sortie.append(("encadre", a, b))
        y = b
    if y < len(boite):
        sortie.append(("texte", y, len(boite)))
    return sortie


def bandes_titres(gris_segment) -> list[tuple[int, int]]:
    """
    Bandes de lignes (projection horizontale de l'encre). Un titre = une bande
    nettement plus haute que la médiane : le gros numéro de section.
    """
    encre = _lisser((gris_segment < 130).mean(1), 3) > 0.012
    bandes = plages(encre, 6)
    if len(bandes) < 3:
        return []
    mediane = st.median(b - a for a, b in bandes)
    return [(a, b) for a, b in bandes if 1.2 * mediane <= (b - a) <= 3.2 * mediane]


# ── Tesseract ────────────────────────────────────────────────────────────────

def tesseract_disponible() -> bool:
    return shutil.which("tesseract") is not None


def langues_tesseract() -> list[str]:
    if not tesseract_disponible():
        return []
    sortie = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True).stdout
    return [l.strip() for l in sortie.splitlines()[1:] if l.strip()]


def _tesseract(image, psm: int, langue: str, tsv: bool = False) -> str:
    """Appelle Tesseract sur une image PIL agrandie, via un fichier temporaire PROPRE
    à l'appel (plusieurs pages tournent en parallèle)."""
    from PIL import Image
    grande = image.resize((image.width * ECHELLE_OCR, image.height * ECHELLE_OCR), Image.LANCZOS)
    with tempfile.TemporaryDirectory(prefix="scan_") as dossier:
        chemin = os.path.join(dossier, "r.png")
        grande.save(chemin)
        cmd = ["tesseract", chemin, "stdout", "-l", langue, "--psm", str(psm)]
        if tsv:
            cmd.append("tsv")
        env = {**os.environ, "OMP_THREAD_LIMIT": "1"}   # un fil par appel : le parallélisme est au niveau page
        res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if res.returncode != 0:
        raise ScanColonnesEchec(f"Tesseract a échoué : {res.stderr.strip()[:200]}")
    return res.stdout


def ocr_lignes(image, langue: str, y_decalage: int = 0) -> list[dict]:
    """Lignes OCR d'un segment : mots (avec géométrie et confiance), position absolue."""
    lignes: dict[tuple, list[dict]] = {}
    for ligne in _tesseract(image, 6, langue, tsv=True).splitlines()[1:]:
        f = ligne.split("\t")
        if len(f) < 12 or not f[11].strip():
            continue
        lignes.setdefault((f[2], f[3], f[4]), []).append(
            dict(l=int(f[6]), t=int(f[7]), w=int(f[8]), h=int(f[9]), c=float(f[10]), txt=f[11])
        )
    res = []
    for mots in lignes.values():
        if sum(m["c"] for m in mots) / len(mots) < CONFIANCE_MIN_LIGNE:
            continue
        # sans un vrai mot (≥ 3 lettres), c'est du bruit d'illustration
        if not any(len(re.sub(r"[^A-Za-z]", "", m["txt"])) >= 3 for m in mots):
            continue
        res.append(dict(
            mots=mots,
            gauche=min(m["l"] for m in mots) / ECHELLE_OCR,
            droite=max(m["l"] + m["w"] for m in mots) / ECHELLE_OCR,
            haut=min(m["t"] for m in mots) / ECHELLE_OCR + y_decalage,
        ))
    res.sort(key=lambda L: L["haut"])
    return res


def ocr_titre(image, langue: str) -> tuple[str, float]:
    """OCR d'une bande de titre (une ligne) → (texte, confiance moyenne des mots). Le numéro
    de section est normalisé. La confiance sert à écarter le bruit d'illustration qui, lu par
    Tesseract, ressemble parfois à « 00 SO ae a tery » : un vrai titre est net."""
    mots = []
    for ligne in _tesseract(image, 7, langue, tsv=True).splitlines()[1:]:
        f = ligne.split("\t")
        if len(f) >= 12 and f[11].strip():
            mots.append((float(f[10]), f[11]))
    if not mots:
        return "", 0.0
    conf = sum(c for c, _ in mots) / len(mots)
    t = " ".join(w for _, w in mots)
    num, _, reste = t.partition(" ")
    num = re.sub(r"[^A-Za-z0-9]", "", num)
    if len(num) <= 2:  # confusions courantes de chiffres dans un numéro de section
        num = num.translate(str.maketrans("OolI|QBSZ", "001119852"))
    return f"{num} {reste}".strip(), conf


# ── Texte ────────────────────────────────────────────────────────────────────

def nettoyer_mot(mot: dict, suivant: str = "") -> str:
    """Symbole de jeu lu comme du bruit → marqueur ; « I » mal lu → « I »."""
    t = mot["txt"]
    coeur = re.sub(r"[^A-Za-z0-9]", "", t)
    if not coeur:
        if all(ch in PONCTUATION_OK for ch in t):
            return t
        debut = re.match(r"^[“\"‘'*]*", t).group(0)
        reste = t[len(debut):]
        mince = mot["w"] / max(mot["h"], 1) < 0.5
        if reste and set(reste) <= set("|{}[]()!/\\") and (mince or suivant[:1].islower()):
            return debut.replace("*", "“") + "I"
        return MARQUEUR_ICONE
    if len(coeur) == 1 and not coeur.isdigit() and coeur not in "aAIXYPOSN" and mot["c"] < 60:
        return MARQUEUR_ICONE
    return t


def texte_ligne(ligne: dict) -> str:
    mots = ligne["mots"]
    jetons = [nettoyer_mot(m, mots[i + 1]["txt"] if i + 1 < len(mots) else "") for i, m in enumerate(mots)]
    s = " ".join(jetons)
    return re.sub(rf"(?:{re.escape(MARQUEUR_ICONE)}\s*)+", MARQUEUR_ICONE + " ", s).strip()


def est_pied_de_page(texte: str, y_abs: float, hauteur_page: int) -> bool:
    """En-tête/pied courant (« 92 Titre du livre ») : court, numéroté, en bord de page."""
    if not (y_abs > 0.93 * hauteur_page or y_abs < 0.04 * hauteur_page):
        return False
    return bool(_RE_PIED.match(texte)) and len(texte.split()) <= 6


def lignes_vers_blocs(lignes: list[dict], largeur_col: float, hauteur_page: int) -> list[tuple[str, str]]:
    """Lignes → paragraphes. Nouveau paragraphe sur retrait ou sur ligne courte finissant
    une phrase ; les mots coupés en fin de ligne sont recollés."""
    blocs: list[tuple[str, str]] = []
    cur, prec = "", None
    gauche0 = min((L["gauche"] for L in lignes), default=0)

    def vider():
        nonlocal cur
        if cur:
            blocs.append(("para", _RE_NUMERO_ECLATE.sub(lambda m: m.group(1).replace(" ", ""), cur, count=1)))
            cur = ""

    for L in lignes:
        txt = texte_ligne(L)
        if not txt or est_pied_de_page(txt, L["haut"], hauteur_page):
            continue
        retrait = L["gauche"] - gauche0 > 0.05 * largeur_col
        courte = prec and (prec["droite"] - gauche0) < 0.80 * largeur_col and re.search(r"[.!?”\"]$", cur)
        if cur and (retrait or courte):
            vider()
        if cur.endswith("-") and txt[:1].islower():
            cur = cur[:-1] + txt
        else:
            cur = (cur + " " + txt).strip()
        prec = L
    vider()
    return [(k, t) for k, t in blocs if len(re.findall(r"[A-Za-z]{3,}", t)) >= 2]


def reparer_titres(blocs: list[tuple[str, str]], journal: list[str]) -> list[tuple[str, str]]:
    """
    Titres sur deux lignes (« 7 Blood of the » / « Mother … ») et numéro isolé mal lu.

    Un numéro n'est corrigé que s'il est CONFIRMÉ des deux côtés : le précédent est N, le
    suivant est N+2, donc celui-ci ne peut être que N+1 (« 10, 1, 12 » → 11). Sans ces deux
    voisins consécutifs, on ne touche à rien : le document complet de test porte d'autres
    titres numérotés par plages (« 3-4 », lus « 34 ») où une règle « la séquence attendue »
    ABÎMAIT des numéros corrects (mesuré : « 95 → 1 », « 12 → 2 »…). Toute réparation est
    journalisée.
    """
    # 1. titres coupés sur deux lignes
    fus: list[list] = []   # [type, texte, titre_incomplet]
    for k, t in blocs:
        if k == "para" and fus and fus[-1][0] == "titre" and fus[-1][2]:
            mot, _, reste = t.partition(" ")
            fus[-1][1] += " " + mot
            fus[-1][2] = False
            journal.append(f"titre complété par le mot suivant : {fus[-1][1]!r}")
            if reste:
                fus.append(["para", reste, False])
            continue
        fus.append([k, t, k == "titre" and t.split()[-1].lower() in CONNECTEURS])

    # 2. numéro isolé confirmé par ses voisins
    idx = [i for i, (k, _, _) in enumerate(fus) if k == "titre" and fus[i][1].split()[0].isdigit()]
    for pos in range(1, len(idx) - 1):
        i = idx[pos]
        prec = int(fus[idx[pos - 1]][1].split()[0])
        suiv = int(fus[idx[pos + 1]][1].split()[0])
        num, _, reste = fus[i][1].partition(" ")
        if int(num) != prec + 1 and suiv == prec + 2:
            journal.append(f"numéro {num} → {prec + 1} ({reste}) : confirmé par {prec} et {suiv}")
            fus[i][1] = f"{prec + 1} {reste}"
    return [(k, t) for k, t, _ in fus]


def blocs_vers_markdown(blocs: list[tuple[str, str]]) -> str:
    md = []
    for k, t in blocs:
        if k == "titre":
            md.append(f"\n## {t}\n")
        elif k == "encadre":
            md.append("> " + t + "\n")
        else:
            md.append(t + "\n")
    return "\n".join(md)


# ── Une page ─────────────────────────────────────────────────────────────────

def _decouper_titres(gris_segment, image_segment, langue: str):
    """[(type, y0, y1, texte)] — un candidat n'est retenu que si son OCR ressemble à un
    titre numéroté ; les rejets restent dans le corps du texte."""
    sortie, y = [], 0
    for a, b in bandes_titres(gris_segment):
        a2, b2 = max(a - 3, 0), min(b + 3, len(gris_segment))
        t, conf = ocr_titre(image_segment.crop((0, a2, image_segment.width, b2)), langue)
        if not _RE_TITRE.match(t) or len(t) > 60 or conf < CONFIANCE_MIN_TITRE:
            continue
        if a2 > y:
            sortie.append(("texte", y, a2, ""))
        sortie.append(("titre", a2, b2, t))
        y = b2
    if y < len(gris_segment):
        sortie.append(("texte", y, len(gris_segment), ""))
    return sortie or [("texte", 0, len(gris_segment), "")]


def analyser_mise_en_page(image, double_page: bool | None = None) -> dict:
    """Géométrie seule (aucun OCR) : gouttière, colonnes par demi-page. Sert à l'aperçu
    et à la page elle-même. `double_page` : True/False impose le réglage ; None = auto
    (largeur/hauteur ≥ RATIO_DOUBLE_PAGE). L'auto est une heuristique : une page SIMPLE en
    paysage (ex. A4 à l'italienne) serait prise pour une double page — d'où le réglage
    explicite, mesuré en construisant le test."""
    np = _numpy()
    gris = np.array(image.convert("L"))
    h, w = gris.shape
    double = (w / h >= RATIO_DOUBLE_PAGE) if double_page is None else double_page
    if double:
        gx = detecter_gouttiere(gris)
        moities = [(0, gx), (gx, w)]
    else:
        gx, moities = None, [(0, w)]
    res = []
    for x0, x1 in moities:
        cols = detecter_colonnes(gris, x0, x1)
        pleine_largeur = not cols
        if pleine_largeur:
            m = int((x1 - x0) * 0.05)
            cols = [(x0 + m, x1 - m)]
        res.append(dict(x0=x0, x1=x1, colonnes=cols, pleine_largeur=pleine_largeur))
    return dict(gris=gris, double_page=double, gouttiere=gx, moities=res, h=h, w=w)


def traiter_page(image, langue: str = "eng", double_page: bool | None = None) -> dict:
    """
    Une page → {'blocs': [(type, texte)], 'avertissements': [...]}.
    Types : 'titre', 'encadre', 'para'.
    """
    mise = analyser_mise_en_page(image, double_page)
    gris, h = mise["gris"], mise["h"]
    blocs, avert = [], []
    for moitie in mise["moities"]:
        if moitie["pleine_largeur"]:
            avert.append("aucune colonne détectée : lecture en pleine largeur")
        for a, b in moitie["colonnes"]:
            for genre, y0, y1 in decouper_segments(gris[:, a:b]):
                if y1 - y0 < 25:
                    continue
                sous = (_decouper_titres(gris[y0:y1, a:b], image.crop((a, y0, b, y1)), langue)
                        if genre == "texte" else [("encadre", 0, y1 - y0, "")])
                for sk, s0, s1, ttxt in sous:
                    if s1 - s0 < 8:
                        continue
                    if sk == "titre":
                        blocs.append(("titre", ttxt))
                        continue
                    marge = 4 if sk == "encadre" else 0   # évite les coins arrondis du cadre
                    if s1 - s0 < 2 * marge + 8:
                        continue
                    lignes = ocr_lignes(image.crop((a + marge, y0 + s0 + marge, b - marge, y0 + s1 - marge)),
                                        langue, y_decalage=y0 + s0 + marge)
                    if not lignes:
                        continue
                    for _, t in lignes_vers_blocs(lignes, b - a, h):
                        blocs.append(("encadre" if sk == "encadre" else "para", t))
                    # NB : lignes_vers_blocs renvoie des 'para' ; l'encadré est requalifié ici
    return {"blocs": blocs, "avertissements": avert}


def apercu_page(image, double_page: bool | None = None) -> bytes:
    """PNG de la page avec les régions détectées (gouttière rouge, colonnes vertes,
    encadrés bleus, titres orange) — c'est le moyen de repérer un mauvais découpage
    AVANT de lancer un long traitement. Géométrie seule, aucun OCR sauf les candidats-titres
    (non lancés ici : les bandes candidates sont dessinées en orange pointillé)."""
    from PIL import ImageDraw
    mise = analyser_mise_en_page(image, double_page)
    img = image.convert("RGB").copy()
    d = ImageDraw.Draw(img)
    if mise["gouttiere"] is not None:
        d.line([(mise["gouttiere"], 0), (mise["gouttiere"], mise["h"])], fill=(220, 0, 0), width=3)
    gris = mise["gris"]
    for moitie in mise["moities"]:
        for a, b in moitie["colonnes"]:
            d.rectangle([a, 0, b, mise["h"] - 1], outline=(0, 160, 0), width=2)
            for genre, y0, y1 in decouper_segments(gris[:, a:b]):
                if genre == "encadre":
                    d.rectangle([a + 2, y0, b - 2, y1], outline=(0, 90, 220), width=3)
                else:
                    for p, q in bandes_titres(gris[y0:y1, a:b]):
                        d.rectangle([a, y0 + p - 2, b, y0 + q + 2], outline=(240, 140, 0), width=2)
    tampon = io.BytesIO()
    img.save(tampon, format="PNG")
    return tampon.getvalue()


# ── Document : rendu PDF, travail par pages, reprise ─────────────────────────

def _ouvrir_pdf(chemin_pdf: str):
    import pymupdf
    try:
        return pymupdf.open(chemin_pdf)
    except Exception as e:
        raise ScanColonnesEchec(f"PDF illisible : {e}")


def rendre_page(doc, numero: int):
    """Page (1-indexée) → image PIL RGB à DPI_RENDU. Le scan porte une image par page à
    ~200 ppi : le rendu à 200 dpi la reprend sans rééchantillonnage."""
    from PIL import Image
    pix = doc[numero - 1].get_pixmap(dpi=DPI_RENDU, colorspace="rgb", alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def nombre_de_pages(chemin_pdf: str) -> int:
    doc = _ouvrir_pdf(chemin_pdf)
    try:
        return doc.page_count
    finally:
        doc.close()


def apercu_pdf(chemin_pdf: str, page: int, double_page: bool | None = None) -> bytes:
    doc = _ouvrir_pdf(chemin_pdf)
    try:
        if not 1 <= page <= doc.page_count:
            raise ScanColonnesEchec(f"Page {page} hors du document ({doc.page_count} pages).")
        return apercu_page(rendre_page(doc, page), double_page)
    finally:
        doc.close()


def chemins_sortie(chemin_pdf: str) -> dict:
    base, _ = os.path.splitext(chemin_pdf)
    return {
        "markdown": f"{base}_scan_colonnes.md",
        "pages": f"{base}_scan_colonnes_pages",
        "etat": f"{base}_scan_colonnes.state.json",
    }


def _chemin_page(dossier: str, numero: int) -> str:
    return os.path.join(dossier, f"page-{numero:04d}.json")


def lire_etat(chemin_pdf: str) -> dict | None:
    etat = lire_json_tolerant(chemins_sortie(chemin_pdf)["etat"])
    return etat if isinstance(etat, dict) else None


def _sauvegarder_etat(etat: dict) -> None:
    ecrire_json_atomique(etat["chemin_etat"], etat)


def assembler_markdown(dossier_pages: str, pages: list[int]) -> tuple[str, list[str]]:
    """
    Concatène les pages DANS L'ORDRE, puis répare titres et numérotation sur
    l'ensemble (la séquence traverse les pages). Retourne (markdown, journal).
    """
    blocs: list[tuple[str, str]] = []
    for n in pages:
        donnees = lire_json_tolerant(_chemin_page(dossier_pages, n))
        if isinstance(donnees, dict):
            blocs += [tuple(b) for b in donnees.get("blocs", [])]
    journal: list[str] = []
    blocs = reparer_titres(blocs, journal)
    return blocs_vers_markdown(blocs), journal


def _traiter_et_ecrire_page(chemin_pdf: str, numero: int, dossier: str, langue: str,
                            double_page: bool | None = None) -> dict:
    """Une page = une unité de reprise : son résultat est écrit atomiquement, et une page
    déjà présente n'est jamais refaite — SAUF si elle a été faite avec d'autres réglages
    (langue, double page) : réutiliser ce résultat serait le servir pour la mauvaise demande."""
    chemin = _chemin_page(dossier, numero)
    reglages = {"langue": langue, "double_page": double_page, "algo": VERSION_ALGO}
    existant = lire_json_tolerant(chemin)
    if isinstance(existant, dict) and "blocs" in existant and existant.get("reglages") == reglages:
        return existant
    doc = _ouvrir_pdf(chemin_pdf)   # un handle par fil : PyMuPDF n'est pas thread-safe
    try:
        resultat = traiter_page(rendre_page(doc, numero), langue, double_page)
    finally:
        doc.close()
    resultat["page"] = numero
    resultat["reglages"] = reglages
    ecrire_json_atomique(chemin, resultat)
    return resultat


def _executer(etat: dict) -> None:
    """Exécuté par le worker de la file. Annulation vérifiée entre les pages."""
    etat["statut"] = "en_cours"
    _sauvegarder_etat(etat)
    debut = time.time()
    pages = list(range(etat["page_debut"], etat["page_fin"] + 1))
    os.makedirs(etat["dossier_pages"], exist_ok=True)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=etat["travailleurs"]) as pool:
            en_vol: dict = {}
            file_pages = iter(pages)
            annule = False

            def lancer_suivante() -> bool:
                n = next(file_pages, None)
                if n is None:
                    return False
                en_vol[pool.submit(_traiter_et_ecrire_page, etat["chemin_pdf"], n,
                                   etat["dossier_pages"], etat["langue_ocr"], etat.get("double_page"))] = n
                return True

            for _ in range(etat["travailleurs"]):
                lancer_suivante()
            while en_vol:
                faits, _ = concurrent.futures.wait(en_vol, return_when=concurrent.futures.FIRST_COMPLETED)
                for f in faits:
                    n = en_vol.pop(f)
                    try:
                        f.result()
                    except Exception as e:
                        etat["pages_en_erreur"].append({"page": n, "erreur": str(e)[:200]})
                    etat["pages_faites"] += 1
                    etat["temps_ecoule_secondes"] = time.time() - debut
                    _sauvegarder_etat(etat)
                    if not annule and est_annule(etat["job_id"]):
                        annule = True
                    if not annule:
                        lancer_suivante()
            if annule:
                etat["statut"] = "annule"
                _sauvegarder_etat(etat)
                return

        markdown, journal = assembler_markdown(etat["dossier_pages"], pages)
        with open(etat["chemin_sortie"], "w", encoding="utf-8") as f:
            f.write(markdown)
        etat["reparations"] = journal
        etat["titres"] = len(re.findall(r"^## ", markdown, re.M))
        # une page en erreur ne rend pas le résultat « terminé » : même règle que la traduction
        etat["statut"] = "erreur" if etat["pages_en_erreur"] else "termine"
        etat["termine_a"] = datetime.datetime.now().isoformat()
        _sauvegarder_etat(etat)
    except Exception as e:
        etat["statut"] = "erreur"
        etat["erreur"] = str(e)
        _sauvegarder_etat(etat)
    finally:
        supprimer_job_registre(etat["job_id"])


def demarrer_scan(chemin_pdf: str, page_debut: int | None = None, page_fin: int | None = None,
                  langue_ocr: str = "eng", travailleurs: int = TRAVAILLEURS_PAR_DEFAUT,
                  double_page: bool | None = None) -> dict:
    """Enfile le nettoyage d'un PDF scanné (file unique : jamais en même temps qu'une
    traduction). Reprend les pages déjà faites d'un passage précédent."""
    if not tesseract_disponible():
        raise ScanColonnesEchec("Tesseract n'est pas installé (brew install tesseract).")
    if langue_ocr not in langues_tesseract():
        raise ScanColonnesEchec(
            f"Langue OCR « {langue_ocr} » absente de Tesseract "
            f"(installées : {', '.join(langues_tesseract())}). brew install tesseract-lang.")
    total = nombre_de_pages(chemin_pdf)
    debut = max(1, page_debut or 1)
    fin = min(total, page_fin or total)
    if debut > fin:
        raise ScanColonnesEchec(f"Plage de pages vide ({debut}–{fin}) pour un document de {total} pages.")
    sorties = chemins_sortie(chemin_pdf)
    etat = {
        "job_id": str(uuid.uuid4()),
        "chemin_pdf": chemin_pdf,
        "chemin_sortie": sorties["markdown"],
        "dossier_pages": sorties["pages"],
        "chemin_etat": sorties["etat"],
        "langue_ocr": langue_ocr,
        "double_page": double_page,
        "travailleurs": max(1, min(travailleurs, 8)),
        "page_debut": debut,
        "page_fin": fin,
        "pages_total": fin - debut + 1,
        "pages_faites": 0,
        "pages_en_erreur": [],
        "statut": "en_attente",
        "temps_ecoule_secondes": 0.0,
        "demarre_a": time.time(),
    }
    _sauvegarder_etat(etat)
    enregistrer_job(etat["job_id"])
    soumettre_travail(etat["job_id"], lambda: _executer(etat))
    return etat
