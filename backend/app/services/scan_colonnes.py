"""
Nettoyage d'un PDF SCANNÉ à plusieurs colonnes (Laboratoire, 10/10/2026).

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
import threading
import time
import uuid

from app.services.job_manager import (
    enregistrer_job,
    job_actif,
    est_annule,
    soumettre_travail,
    supprimer_job_registre,
)
from app.services.persistance import ecrire_json_atomique, ecrire_texte_atomique, lire_json_tolerant

DPI_RENDU = 200
ECHELLE_OCR = 3            # agrandissement avant Tesseract (mesuré : ×3 suffit, ×4/×5 sans gain)
CONFIANCE_MIN_LIGNE = 55   # sous ce seuil, la ligne est du bruit (illustration, ombre de reliure)
CONFIANCE_MIN_ZONE = 40    # dans une zone déclarée texte par l'utilisateur (voir ocr_lignes)
CONFIANCE_MIN_TITRE = 70   # idem pour une bande de titre (voir ocr_titre)
VERSION_ALGO = 4           # à incrémenter quand un résultat de page n'est plus valable (invalide le cache)
RATIO_DOUBLE_PAGE = 1.25   # largeur/hauteur au-delà duquel la page est une double page
# Page qui n'est pas du texte courant (page titre, couverture) : peu de bandes horizontales ont la forme
# d'un texte en colonnes. Mesuré sur le Cycle I : page titre 5/41, page avec grande illustration 7/20,
# pages de texte ≥ 44/45. En dessous du seuil, la demi-page est lue en pleine largeur.
PART_BANDES_TEXTE_MIN = 0.25
GOUTTIERE_CENTRALE_MIN, GOUTTIERE_CENTRALE_MAX = 0.42, 0.58     # voir _gouttiere_centrale
PART_MAX_ENCADRE = 0.95        # au-delà, la colonne n'a pas d'encadré : son papier est blanc (Cycle I : vrais encadrés jusqu'à 81 %, page blanche 100 %)
# Encre clairsemée (numéros d'une table des matières) : invisible au 70ᵉ centile, visible au 85ᵉ.
CENTILE_ENCRE_CLAIRSEMEE = 85
LARGEUR_MIN_CLAIRSEMEE = 40    # px ; en dessous, c'est un trait ou une ombre
ECART_MAX_RATTACHEMENT = 60    # px entre la colonne et la bande clairsemée qui la prolonge
MODES_MOITIE = ("auto", "colonnes", "image", "ignorer")
LARGEUR_MIN_COLONNE = 30       # px : une colonne réglée à la main plus étroite est ignorée
# Zones dessinées à la main dans l'aperçu. Leur contenu n'est plus lu par les colonnes (il est masqué) ;
# chaque zone est rendue selon son type, à sa place dans l'ordre de lecture :
#   illustration : recopiée telle quelle (reconstruction), rien n'est lu ;
#   titre        : lue et rendue en titre (## …, donc un chapitre) ;
#   texte        : lue à part, en paragraphes (ex. un tableau à cheval sur deux colonnes) ;
#   conserver    : lue, mais JAMAIS traduite (voir MARQUE_CONSERVER) ;
#   ignorer      : ni lue ni recopiée.
TYPES_ZONE = ("illustration", "titre", "texte", "conserver", "ignorer")
COTE_MIN_ZONE = 10             # px
NB_MAX_ZONES = 60
# Un texte à ne pas traduire est écrit entre ces deux lignes dans le Markdown nettoyé : translator les masque
# (le modèle ne voit jamais ce texte) et le lecteur de la Bibliothèque n'affiche pas les commentaires.
MARQUE_CONSERVER = ("<!-- conserver -->", "<!-- /conserver -->")
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
    _numpy()  # vérifie que numpy est installé (erreur claire sinon) ; seul le tableau gris est utilisé ici
    h, w = gris.shape
    sombre = (gris < 120).astype(float)[int(h * 0.5):]
    profil = _lisser(sombre.mean(0), 9)
    bas, haut = int(w * 0.44), int(w * 0.56)
    zone = profil[bas:haut]
    # la zone la plus vide peut être large (marges + reliure) : on vise le MILIEU de la plus
    # longue plage vide plutôt que son premier pixel, pour ne pas couper contre une colonne
    plage = max(plages(zone <= zone.min() + 0.002, 1), key=lambda r: r[1] - r[0])
    return bas + (plage[0] + plage[1]) // 2


def _gouttiere_centrale(trou: tuple[int, int], largeur: int) -> bool:
    """
    Une seule gouttière intérieure ne vote que si elle tombe au MILIEU de la demi-page : c'est une page à deux
    colonnes. Exiger deux gouttières (trois colonnes, le Cycle I) laissait une page simple à deux colonnes en
    pleine largeur, lue en travers (audit extraction, 13/11/2026). Fenêtre étroite (42–58 %) : dans une page à
    trois colonnes, les gouttières tombent vers 33 % et 66 %, et une illustration n'a pas de trou centré net.
    """
    milieu = (trou[0] + trou[1]) / 2
    return GOUTTIERE_CENTRALE_MIN * largeur <= milieu <= GOUTTIERE_CENTRALE_MAX * largeur


def detecter_colonnes(gris, x0: int, x1: int, bande: int = 50,
                      journal: list[str] | None = None) -> list[tuple[int, int]]:
    """
    Bornes x des colonnes d'une demi-page. Ne vote que sur les bandes
    horizontales qui ressemblent à du texte (≥ 2 gouttières verticales nettes) :
    une illustration n'en a pas. Le 70ᵉ centile entre bandes évite qu'un
    encadré ou une marge irrégulière referme une gouttière réelle.
    `journal` reçoit, en clair, ce que la détection a dû deviner (affiché comme « page à vérifier »).
    """
    np = _numpy()
    h = gris.shape[0]
    profils, deux_colonnes, examinees = [], [], 0
    for y in range(0, h - bande, bande // 2):
        d = (gris[y:y + bande, x0:x1] < 120).astype(float)
        if d.mean() > 0.30:
            continue
        examinees += 1
        p = _lisser(d.mean(0), 5)
        trous = [r for r in plages(p < 0.01, 12) if r[0] > 20 and r[1] < len(p) - 20]
        if len(trous) >= 2:
            profils.append(p)
        elif len(trous) == 1 and _gouttiere_centrale(trous[0], len(p)):
            deux_colonnes.append(p)
    # Les bandes à une seule gouttière centrale ne servent que si celles à deux gouttières ne suffisent pas : sur
    # le Cycle I complet (61 pages), les mêler aux autres faisait sauter la colonne du milieu d'une demi-page à
    # trois colonnes illustrée (p. 45). Une page qui votait déjà ne change pas.
    if len(profils) < PART_BANDES_TEXTE_MIN * examinees and len(deux_colonnes) >= PART_BANDES_TEXTE_MIN * examinees:
        profils = deux_colonnes
    if not profils:
        return []
    if len(profils) < PART_BANDES_TEXTE_MIN * examinees:
        # Page titre, couverture : quelques grosses lettres font une « colonne » étroite qui coupait le titre.
        if journal is not None:
            journal.append("peu de texte en colonnes (page titre ?) : lue en pleine largeur")
        return []
    profil = np.percentile(np.array(profils), 70, axis=0)
    fusion: list[tuple[int, int]] = []
    for a, b in plages(profil > 0.02, 40):
        if fusion and a - fusion[-1][1] < 14:
            fusion[-1] = (fusion[-1][0], b)
        else:
            fusion.append((a, b))
    colonnes = [(a, b) for a, b in fusion if b - a > 120]
    colonnes = _rattacher_encre_clairsemee(np.array(profils), colonnes, journal)
    return [(max(x0 + a - 6, 0), x0 + b + 6) for a, b in colonnes]


def _rattacher_encre_clairsemee(profils, colonnes: list[tuple[int, int]],
                                journal: list[str] | None) -> list[tuple[int, int]]:
    """
    Table des matières : les numéros de page, alignés à droite et espacés, n'apparaissent pas au 70ᵉ centile ;
    la colonne s'arrêtait au bout des titres et les numéros n'étaient jamais lus (Cycle I, p. 1). Une bande
    d'encre clairsemée qui ne touche aucune colonne ni le bord de la demi-page prolonge la colonne à sa
    GAUCHE (le numéro suit le titre), si elle en est proche. Les bords sont exclus : l'ombre de reliure et
    le bord d'une illustration y forment des bandes du même genre (mesuré p. 1, 2, 5).
    """
    if not colonnes:
        return colonnes
    largeur = profils.shape[1]
    clair = _numpy().percentile(profils, CENTILE_ENCRE_CLAIRSEMEE, axis=0)
    sortie = list(colonnes)
    for a, b in plages(clair > 0.02, LARGEUR_MIN_CLAIRSEMEE):
        if a <= 5 or b >= largeur - 5:
            continue
        if any(min(b, q) > max(a, p) for p, q in sortie):
            continue
        gauche = [i for i, (p, q) in enumerate(sortie) if q <= a and a - q <= ECART_MAX_RATTACHEMENT]
        if not gauche:
            continue
        i = gauche[-1]
        suivante = [p for p, _ in sortie if p >= b]
        if suivante and min(suivante) - b < 10:
            continue        # collée à la colonne suivante : rien ne dit à laquelle elle appartient
        sortie[i] = (sortie[i][0], b)
        if journal is not None:
            journal.append("colonne prolongée par des numéros isolés (table des matières ?)")
    return sortie


def decouper_segments(gris_colonne) -> list[tuple[str, int, int]]:
    """
    ('texte'|'encadre', y0, y1) selon le fond : papier ≈ 231, encadré blanc ≈ 255.
    Le fond est le 85ᵉ centile de chaque ligne, insensible à l'encre.
    Une colonne « blanche » presque partout n'a pas d'encadré : c'est le PAPIER qui est blanc (scan binarisé,
    blanchi). Le seuil seul en faisait un unique encadré rendu en citation, titres jamais cherchés (audit
    extraction, 13/11/2026). Un seuil relatif au papier effaçait de vrais encadrés sur le Cycle I (p. 28, 38) :
    on ne neutralise que le cas où la « boîte » couvre presque toute la colonne (PART_MAX_ENCADRE).
    """
    np = _numpy()
    fond = _lisser(np.percentile(gris_colonne, 85, axis=1), 5)
    boite = fond >= 248
    if len(boite) and boite.mean() > PART_MAX_ENCADRE:
        boite = np.zeros_like(boite)
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
    return [ligne.strip() for ligne in sortie.splitlines()[1:] if ligne.strip()]


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


def ocr_lignes(image, langue: str, y_decalage: int = 0, zone: bool = False) -> list[dict]:
    """Lignes OCR d'un segment : mots (avec géométrie et confiance), position absolue.
    `zone=True` : zone que l'utilisateur a déclarée texte. Mesuré sur un tableau aéré (Cycle I p. 1) : le mode
    bloc (psm 6) n'y lisait que l'en-tête ; psm 4 lit chaque rangée. Les lignes de chiffres (« 1-3 1-2 »),
    écartées des colonnes comme du bruit d'illustration, et les lignes un peu moins sûres y sont gardées."""
    lignes: dict[tuple, list[dict]] = {}
    for ligne in _tesseract(image, 4 if zone else 6, langue, tsv=True).splitlines()[1:]:
        f = ligne.split("\t")
        if len(f) < 12 or not f[11].strip():
            continue
        lignes.setdefault((f[2], f[3], f[4]), []).append(
            dict(l=int(f[6]), t=int(f[7]), w=int(f[8]), h=int(f[9]), c=float(f[10]), txt=f[11])
        )
    res = []
    for mots in lignes.values():
        if sum(m["c"] for m in mots) / len(mots) < (CONFIANCE_MIN_ZONE if zone else CONFIANCE_MIN_LIGNE):
            continue
        # sans un vrai mot (≥ 3 lettres), c'est du bruit d'illustration
        if not zone and not any(len(re.sub(r"[^A-Za-z]", "", m["txt"])) >= 3 for m in mots):
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


def lignes_vers_blocs(lignes: list[dict], largeur_col: float, hauteur_page: int,
                      filtrer: bool = True) -> list[tuple[str, str]]:
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
    return [(k, t) for k, t in blocs if not filtrer or len(re.findall(r"[A-Za-z]{3,}", t)) >= 2]


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
        elif k == "conserve":
            md.append(f"{MARQUE_CONSERVER[0]}\n{t}\n{MARQUE_CONSERVER[1]}\n")
        else:
            md.append(t + "\n")
    return "\n".join(md)


# ── Une page ─────────────────────────────────────────────────────────────────

def _decouper_titres(gris_segment, image_segment, langue: str, origine: tuple[int, int] = (0, 0),
                     retirees: list | None = None):
    """[(type, y0, y1, texte)] — un candidat n'est retenu que si son OCR ressemble à un
    titre numéroté ; les rejets restent dans le corps du texte. `origine` : (x, y) du segment dans la page,
    pour reconnaître les titres que l'utilisateur a retirés (`retirees`, voir detections_retirees)."""
    sortie, y = [], 0
    for a, b in _bandes_retenues(gris_segment, origine[0], origine[0] + image_segment.width, origine[1], retirees):
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


def meme_zone(r, q) -> bool:
    """Deux rectangles désignent la même zone s'ils se recouvrent à plus de moitié (intersection / union) : une
    détection retirée est reconnue même si un réglage voisin l'a décalée de quelques pixels."""
    ix = max(0, min(r[2], q[2]) - max(r[0], q[0]))
    iy = max(0, min(r[3], q[3]) - max(r[1], q[1]))
    inter = ix * iy
    union = (r[2] - r[0]) * (r[3] - r[1]) + (q[2] - q[0]) * (q[3] - q[1]) - inter
    return union > 0 and inter / union >= 0.5


def _retiree(rect, retirees: list | None) -> bool:
    return bool(retirees) and any(meme_zone(rect, q) for q in retirees)


def _segments_lus(gris, a: int, b: int, retirees: list | None):
    """decouper_segments d'une colonne, où un encadré que l'utilisateur a retiré redevient du texte courant."""
    for genre, y0, y1 in decouper_segments(gris[:, a:b]):
        if genre == "encadre" and _retiree([a, y0, b, y1], retirees):
            genre = "texte"
        yield genre, y0, y1


def _bandes_retenues(gris_segment, a: int, b: int, y0: int, retirees: list | None) -> list[tuple[int, int]]:
    """bandes_titres d'un segment (coordonnées du segment), moins les titres que l'utilisateur a retirés."""
    return [(p, q) for p, q in bandes_titres(gris_segment) if not _retiree([a, y0 + p - 3, b, y0 + q + 3], retirees)]


def detections_texte(mise: dict, gris, retirees: list | None = None) -> list[dict]:
    """Titres candidats (orange) et encadrés (bleus) de la détection, en coordonnées de la page : ce que l'aperçu
    entoure, et ce que l'éditeur rend modifiable. `gris` : la page où les zones dessinées sont déjà blanchies.
    Un encadré est présenté comme une zone « texte » (lu à part, en paragraphes)."""
    sortie = []
    for moitie in mise["moities"]:
        for a, b in moitie["colonnes"]:
            for genre, y0, y1 in _segments_lus(gris, a, b, retirees):
                if genre == "encadre":
                    sortie.append({"type": "texte", "rect": [a, y0, b, y1]})
                    continue
                for p, q in _bandes_retenues(gris[y0:y1, a:b], a, b, y0, retirees):
                    sortie.append({"type": "titre", "rect": [a, max(y0 + p - 3, 0), b, y0 + q + 3]})
    return sortie


def analyser_mise_en_page(image, double_page: bool | None = None, reglage: dict | None = None) -> dict:
    """Géométrie seule (aucun OCR) : gouttière, colonnes par demi-page. Sert à l'aperçu
    et à la page elle-même. `double_page` : True/False impose le réglage ; None = auto
    (largeur/hauteur ≥ RATIO_DOUBLE_PAGE). L'auto est une heuristique : une page SIMPLE en
    paysage (ex. A4 à l'italienne) serait prise pour une double page — d'où le réglage
    explicite, mesuré en construisant le test.
    `reglage` : réglage manuel de CETTE page (voir normaliser_reglage), prioritaire sur la détection.
    Mode d'une demi-page : 'auto' (détection), 'colonnes' (bornes posées à la main), 'image' (recopiée
    telle quelle, rien n'est lu), 'ignorer' (ni lue ni recopiée)."""
    np = _numpy()
    gris = np.array(image.convert("L"))
    h, w = gris.shape
    reglage = reglage or {}
    double = (w / h >= RATIO_DOUBLE_PAGE) if double_page is None else double_page
    if double:
        gx_manuelle = reglage.get("gouttiere")
        gx = gx_manuelle if isinstance(gx_manuelle, int) and 0.2 * w <= gx_manuelle <= 0.8 * w \
            else detecter_gouttiere(gris)
        moities = [(0, gx), (gx, w)]
    else:
        gx, moities = None, [(0, w)]
    manuels = reglage.get("moities")
    if not isinstance(manuels, list) or len(manuels) != len(moities):
        manuels = [{}] * len(moities)       # réglage fait pour l'autre découpage (simple/double) : ignoré
    res = []
    for (x0, x1), manuel in zip(moities, manuels):
        mode = manuel.get("mode") if manuel.get("mode") in MODES_MOITIE else "auto"
        alertes: list[str] = []
        cols: list[tuple[int, int]] = []
        if mode == "colonnes":
            cols = [(max(a, x0), min(b, x1)) for a, b in manuel.get("colonnes") or []]
            cols = [(a, b) for a, b in cols if b - a >= LARGEUR_MIN_COLONNE]
            if not cols:
                mode = "auto"
        if mode == "auto":
            cols = detecter_colonnes(gris, x0, x1, journal=alertes)
        pleine_largeur = mode == "auto" and not cols
        if pleine_largeur:
            m = int((x1 - x0) * 0.05)
            cols = [(x0 + m, x1 - m)]
            if not alertes:
                alertes.append("aucune colonne détectée : lue en pleine largeur")
        res.append(dict(x0=x0, x1=x1, colonnes=cols, pleine_largeur=pleine_largeur, mode=mode, alertes=alertes))
    zones = []      # rognées à la page (un recadrage PIL hors de l'image ajouterait du noir)
    for z in reglage.get("zones") or []:
        x0, y0, x1, y1 = z["rect"]
        x0, x1, y0, y1 = max(x0, 0), min(x1, w), max(y0, 0), min(y1, h)
        if x1 - x0 >= COTE_MIN_ZONE and y1 - y0 >= COTE_MIN_ZONE:
            zones.append({"type": z["type"], "rect": [x0, y0, x1, y1]})
    return dict(gris=gris, double_page=double, gouttiere=gx, moities=res, h=h, w=w, zones=zones)


def traiter_page(image, langue: str = "eng", double_page: bool | None = None, reglage: dict | None = None) -> dict:
    """
    Une page → {'blocs': [(type, texte)], 'avertissements': [...]}.
    Types : 'titre', 'encadre', 'para'. Une demi-page en mode 'image' ou 'ignorer' n'a pas de colonne :
    rien n'y est lu.
    """
    mise = analyser_mise_en_page(image, double_page, reglage)
    zones = mise["zones"]
    gris, image_lue = (_masquer_zones(mise["gris"], image, zones) if zones else (mise["gris"], image))
    h = mise["h"]
    retirees = (reglage or {}).get("detections_retirees")
    blocs, avert = [], []      # blocs : (clé d'ordre de lecture, type, texte)
    for mi, moitie in enumerate(mise["moities"]):
        avert += moitie["alertes"]
        for ci, (a, b) in enumerate(moitie["colonnes"]):
            for genre, y0, y1 in _segments_lus(gris, a, b, retirees):
                if y1 - y0 < 25:
                    continue
                sous = (_decouper_titres(gris[y0:y1, a:b], image_lue.crop((a, y0, b, y1)), langue, (a, y0), retirees)
                        if genre == "texte" else [("encadre", 0, y1 - y0, "")])
                for sk, s0, s1, ttxt in sous:
                    if s1 - s0 < 8:
                        continue
                    cle = (mi, ci, y0 + s0)
                    if sk == "titre":
                        blocs.append((cle, "titre", ttxt))
                        continue
                    marge = 4 if sk == "encadre" else 0   # évite les coins arrondis du cadre
                    if s1 - s0 < 2 * marge + 8:
                        continue
                    lignes = ocr_lignes(image_lue.crop((a + marge, y0 + s0 + marge, b - marge, y0 + s1 - marge)),
                                        langue, y_decalage=y0 + s0 + marge)
                    if not lignes:
                        continue
                    for _, t in lignes_vers_blocs(lignes, b - a, h):
                        blocs.append((cle, "encadre" if sk == "encadre" else "para", t))
                    # NB : lignes_vers_blocs renvoie des 'para' ; l'encadré est requalifié ici
    for z in zones:
        blocs += [(_cle_zone(mise, z["rect"]), k, t) for k, t in _lire_zone(image, z, langue, h, avert)]
    blocs.sort(key=lambda b: b[0])      # tri stable : sans zone, l'ordre est celui de la boucle
    return {"blocs": [(k, t) for _, k, t in blocs], "avertissements": avert}


def _masquer_zones(gris, image, zones: list[dict]):
    """Copies de la page où chaque zone dessinée à la main est passée en blanc : les colonnes ne la lisent plus
    (c'est la zone elle-même qui est lue, ou recopiée, ou ignorée)."""
    from PIL import ImageDraw
    gris = gris.copy()
    image = image.copy()
    d = ImageDraw.Draw(image)
    for z in zones:
        x0, y0, x1, y1 = z["rect"]
        gris[y0:y1, x0:x1] = 255
        d.rectangle([x0, y0, x1 - 1, y1 - 1], fill=(255, 255, 255) if image.mode == "RGB" else 255)
    return gris, image


def _cle_zone(mise: dict, rect) -> tuple[int, int, int]:
    """Place d'une zone dans l'ordre de lecture : sa demi-page (centre), la première colonne qu'elle recouvre (ou
    la première à sa droite), son haut. Une zone à cheval sur deux colonnes (un tableau) est lue avec la première,
    à sa hauteur."""
    x0, y0, x1, _ = rect
    cx = (x0 + x1) / 2
    mi = next((i for i, m in enumerate(mise["moities"]) if m["x0"] <= cx < m["x1"]), len(mise["moities"]) - 1)
    cols = mise["moities"][mi]["colonnes"]
    ci = next((i for i, (a, b) in enumerate(cols) if b > x0 and a < x1), None)
    if ci is None:
        ci = next((i for i, (a, _) in enumerate(cols) if a >= cx), len(cols))
    return (mi, ci, y0)


def _lire_zone(image, zone: dict, langue: str, hauteur_page: int, avert: list[str]) -> list[tuple[str, str]]:
    """Blocs d'une zone dessinée à la main (voir TYPES_ZONE). Une zone à lire où l'OCR ne trouve rien est signalée."""
    if zone["type"] in ("illustration", "ignorer"):
        return []
    x0, y0, x1, y1 = zone["rect"]
    lignes = ocr_lignes(image.crop((x0, y0, x1, y1)), langue, y_decalage=y0, zone=True)
    texte = " ".join(t for t in (texte_ligne(L) for L in lignes) if t).strip()
    if not texte:
        avert.append(f"zone « {zone['type']} » ({x0}, {y0}) : aucun texte lu")
        return []
    if zone["type"] == "titre":
        return [("titre", texte)]
    if zone["type"] == "conserver":
        return [("conserve", texte)]
    if _ressemble_a_un_tableau(lignes):     # une rangée par paragraphe : fusionnées, les cellules se mélangent
        return [("para", t) for t in (texte_ligne(L) for L in lignes) if t]
    return lignes_vers_blocs(lignes, x1 - x0, hauteur_page, filtrer=False) or [("para", texte)]


def _ressemble_a_un_tableau(lignes: list[dict]) -> bool:
    """Au moins la moitié des lignes ont un grand blanc entre deux mots (plus de 2,5 hauteurs de lettre) : des
    cellules, pas de la prose."""
    def rangee(L) -> bool:
        mots = sorted(L["mots"], key=lambda m: m["l"])
        h = sorted(m["h"] for m in mots)[len(mots) // 2] or 1
        return any(b["l"] - (a["l"] + a["w"]) > 2.5 * h for a, b in zip(mots, mots[1:]))
    return len(lignes) >= 2 and sum(rangee(L) for L in lignes) * 2 >= len(lignes)


def geometrie_page(image, double_page: bool | None = None, reglage: dict | None = None) -> dict:
    """Ce que montre l'aperçu, en données : sert au dessin du PNG ET à l'éditeur de régions du navigateur
    (lignes déplaçables). Coordonnées en pixels du rendu à DPI_RENDU."""
    from app.services.reconstruction import illustrations_page   # import local : reconstruction importe ce module
    mise = analyser_mise_en_page(image, double_page, reglage)
    gris = _masquer_zones(mise["gris"], image, mise["zones"])[0] if mise["zones"] else mise["gris"]
    return {
        "mise": mise,
        "illustrations": illustrations_page(image, mise, reglage),
        "detections": detections_texte(mise, gris, (reglage or {}).get("detections_retirees")),
    }


def geometrie_publique(geo: dict, reglage: dict | None) -> dict:
    """La géométrie sans le tableau de pixels, sérialisable en JSON. `illustrations` : celles de la DÉTECTION
    (et l'encre d'une demi-page « image ») ; les zones dessinées à la main sont dans `zones`."""
    mise = geo["mise"]
    zones = mise["zones"]
    manuelles = [z["rect"] for z in zones if z["type"] == "illustration"]
    return {
        "largeur": mise["w"], "hauteur": mise["h"], "double_page": mise["double_page"],
        "gouttiere": mise["gouttiere"],
        "moities": [{k: m[k] for k in ("x0", "x1", "mode", "pleine_largeur", "alertes")}
                    | {"colonnes": [list(c) for c in m["colonnes"]]} for m in mise["moities"]],
        "illustrations": [list(r) for r in geo["illustrations"] if list(r) not in manuelles],
        "detections": geo.get("detections", []),
        "zones": zones,
        "reglage": reglage,
    }


COULEURS_ZONES = {"illustration": (170, 0, 200), "titre": (240, 140, 0), "texte": (0, 120, 200),
                  "conserver": (0, 150, 140), "ignorer": (110, 110, 110)}


def apercu_page(image, double_page: bool | None = None, reglage: dict | None = None, edition: bool = False) -> bytes:
    """PNG de la page avec les régions détectées (gouttière rouge, colonnes vertes,
    encadrés bleus, titres orange, illustrations violettes) — c'est le moyen de repérer un
    mauvais découpage AVANT de lancer un long traitement. Géométrie seule, aucun OCR.
    Le violet vient de la reconstruction (reconstruction.illustrations_page) : ces zones ne
    sont pas traduites mais recopiées telles quelles dans le PDF et le HTML recréés.
    Une demi-page ignorée est voilée de gris ; les zones dessinées à la main sont pleines, de la couleur de leur type.
    `edition` : l'éditeur du Laboratoire dessine lui-même illustrations, titres et encadrés détectés et zones
    (déplaçables) : ils sont omis."""
    from PIL import Image, ImageDraw

    geo = geometrie_page(image, double_page, reglage)
    mise = geo["mise"]
    zones = mise["zones"]
    manuelles = [z["rect"] for z in zones if z["type"] == "illustration"]
    illustrations = [] if edition else [r for r in geo["illustrations"] if list(r) not in manuelles]
    img = image.convert("RGB").copy()
    for moitie in mise["moities"]:
        if moitie["mode"] == "ignorer":
            zone = img.crop((moitie["x0"], 0, moitie["x1"], mise["h"]))
            img.paste(Image.blend(zone, Image.new("RGB", zone.size, (128, 128, 128)), 0.6), (moitie["x0"], 0))
    if not edition:
        for z in zones:
            x0, y0, x1, y1 = z["rect"]
            zone = img.crop((x0, y0, x1, y1))
            img.paste(Image.blend(zone, Image.new("RGB", zone.size, COULEURS_ZONES[z["type"]]), 0.25), (x0, y0))
    d = ImageDraw.Draw(img)
    if not edition:
        for z in zones:
            d.rectangle(z["rect"], outline=COULEURS_ZONES[z["type"]], width=5)
    if mise["gouttiere"] is not None:
        d.line([(mise["gouttiere"], 0), (mise["gouttiere"], mise["h"])], fill=(220, 0, 0), width=3)
    for moitie in mise["moities"]:
        for a, b in moitie["colonnes"]:
            d.rectangle([a, 0, b, mise["h"] - 1], outline=(0, 160, 0), width=2)
    for det in ([] if edition else geo["detections"]):
        x0, y0, x1, y1 = det["rect"]
        if det["type"] == "texte":
            d.rectangle([x0 + 2, y0, x1 - 2, y1], outline=(0, 90, 220), width=3)
        else:
            d.rectangle(det["rect"], outline=(240, 140, 0), width=2)
    for x0, y0, x1, y1 in illustrations:
        d.rectangle([x0 + 4, y0 + 4, x1 - 4, y1 - 4], outline=(170, 0, 200), width=5)
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


def _image_de_page(chemin_pdf: str, page: int):
    doc = _ouvrir_pdf(chemin_pdf)
    try:
        if not 1 <= page <= doc.page_count:
            raise ScanColonnesEchec(f"Page {page} hors du document ({doc.page_count} pages).")
        return rendre_page(doc, page)
    finally:
        doc.close()


def apercu_pdf(chemin_pdf: str, page: int, double_page: bool | None = None, edition: bool = False) -> bytes:
    return apercu_page(_image_de_page(chemin_pdf, page), double_page, reglage_page(chemin_pdf, page), edition)


def geometrie_pdf(chemin_pdf: str, page: int, double_page: bool | None = None) -> dict:
    reglage = reglage_page(chemin_pdf, page)
    return geometrie_publique(geometrie_page(_image_de_page(chemin_pdf, page), double_page, reglage), reglage)


def chemins_sortie(chemin_pdf: str) -> dict:
    base, _ = os.path.splitext(chemin_pdf)
    return {
        "markdown": f"{base}_scan_colonnes.md",
        "pages": f"{base}_scan_colonnes_pages",
        "etat": f"{base}_scan_colonnes.state.json",
        # Réglages faits à la main dans l'aperçu : un travail de l'utilisateur, jamais effacé par `reinitialiser`.
        "reglages": f"{base}_scan_colonnes_regions.json",
    }


# ── Réglages manuels par page (aperçu des régions) ───────────────────────────

_VERROU_REGLAGES = threading.Lock()


def _entier(v) -> int | None:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 100_000 else None


def normaliser_reglage(brut) -> dict | None:
    """
    Réglage d'une page tel qu'il est enregistré, ou None s'il n'y a rien à retenir (tout en automatique) :
      {"gouttiere": int | absent,
       "moities": [{"mode": "auto"|"colonnes"|"image"|"ignorer", "colonnes": [[x0, x1], ...]}, ...],
       "illustrations_retirees": [[x0, y0, x1, y1], ...],
       "detections_retirees": [[x0, y0, x1, y1], ...],   # titres et encadrés détectés : lus comme texte courant
       "zones": [{"type": TYPES_ZONE, "rect": [x0, y0, x1, y1]}, ...]}   # dessinées à la main, dans l'ordre
    Les bornes sont validées contre la page au moment de l'appliquer, pas ici (une zone hors page est rognée).
    """
    if not isinstance(brut, dict):
        return None
    sortie: dict = {}
    if (gx := _entier(brut.get("gouttiere"))) is not None:
        sortie["gouttiere"] = gx
    moities = []
    for m in (brut.get("moities") or [])[:2] if isinstance(brut.get("moities"), list) else []:
        m = m if isinstance(m, dict) else {}
        mode = m.get("mode") if m.get("mode") in MODES_MOITIE else "auto"
        entree: dict = {"mode": mode}
        if mode == "colonnes":
            cols = []
            for c in (m.get("colonnes") or [])[:6]:
                if isinstance(c, (list, tuple)) and len(c) == 2:
                    a, b = _entier(c[0]), _entier(c[1])
                    if a is not None and b is not None and b - a >= LARGEUR_MIN_COLONNE:
                        cols.append([a, b])
            if not cols:
                entree = {"mode": "auto"}
            else:
                entree["colonnes"] = sorted(cols)
        moities.append(entree)
    if any(m["mode"] != "auto" for m in moities):
        sortie["moities"] = moities
    for cle in ("illustrations_retirees", "detections_retirees"):
        retirees = []
        for r in (brut.get(cle) or [])[:50] if isinstance(brut.get(cle), list) else []:
            if isinstance(r, (list, tuple)) and len(r) == 4 and all(_entier(v) is not None for v in r):
                retirees.append([int(v) for v in r])
        if retirees:
            sortie[cle] = retirees
    zones = []
    for z in (brut.get("zones") or [])[:NB_MAX_ZONES] if isinstance(brut.get("zones"), list) else []:
        if not isinstance(z, dict) or z.get("type") not in TYPES_ZONE:
            continue
        r = z.get("rect")
        if not (isinstance(r, (list, tuple)) and len(r) == 4 and all(_entier(v) is not None for v in r)):
            continue
        x0, y0, x1, y1 = (int(v) for v in r)
        x0, x1, y0, y1 = min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)
        if x1 - x0 >= COTE_MIN_ZONE and y1 - y0 >= COTE_MIN_ZONE:
            zones.append({"type": z["type"], "rect": [x0, y0, x1, y1]})
    if zones:
        sortie["zones"] = zones
    return sortie or None


def lire_reglages(chemin_pdf: str) -> dict:
    donnees = lire_json_tolerant(chemins_sortie(chemin_pdf)["reglages"])
    pages = donnees.get("pages") if isinstance(donnees, dict) else None
    return pages if isinstance(pages, dict) else {}


def reglage_page(chemin_pdf: str, numero: int) -> dict | None:
    return normaliser_reglage(lire_reglages(chemin_pdf).get(str(numero)))


def enregistrer_reglage_page(chemin_pdf: str, numero: int, brut) -> dict | None:
    """Enregistre (ou efface, si `brut` ne retient rien) le réglage manuel d'une page. Refusé pendant un
    nettoyage : les pages pas encore traitées liraient un réglage que l'utilisateur n'a pas fini de poser."""
    etat = lire_etat(chemin_pdf)
    if etat and etat.get("statut") in ("en_attente", "en_cours"):
        raise ScanColonnesEchec("Un nettoyage est en cours pour ce PDF : arrêtez-le avant de modifier les régions.")
    reglage = normaliser_reglage(brut)
    with _VERROU_REGLAGES:
        pages = lire_reglages(chemin_pdf)
        if reglage is None:
            pages.pop(str(numero), None)
        else:
            pages[str(numero)] = reglage
        ecrire_json_atomique(chemins_sortie(chemin_pdf)["reglages"], {"version": 1, "pages": pages})
    return reglage


def reinitialiser(chemin_pdf: str) -> list[str]:
    """
    Efface les résultats du nettoyage de CE PDF (pages en cache, Markdown produit, état) pour repartir de
    zéro : sans cela, relancer reprend les pages déjà traitées. Le PDF d'origine n'est jamais touché ; seuls
    les trois chemins dérivés de `chemins_sortie` sont supprimés. Refuse si un nettoyage est en cours, ou si
    une traduction du registre a ce Markdown pour source : la reprendre, y ajouter des chapitres ou refaire
    sa mise en page deviendrait impossible. Retourne la liste des éléments réellement supprimés.
    """
    from app.services.bibliotheque import lister_documents

    etat = lire_etat(chemin_pdf)
    if etat and etat.get("statut") in ("en_attente", "en_cours"):
        raise ScanColonnesEchec("Un nettoyage est en cours pour ce PDF : arrêtez-le d'abord.")
    sorties = chemins_sortie(chemin_pdf)
    if any(d.get("chemin_source") == sorties["markdown"] for d in lister_documents()):
        raise ScanColonnesEchec("Une traduction de « Vos traductions » vient de ce fichier nettoyé : l'effacer "
                                "empêcherait de la reprendre. Retirez-la d'abord de la liste.")
    supprimes = []
    for cle in ("markdown", "etat"):
        if os.path.isfile(sorties[cle]):
            os.remove(sorties[cle])
            supprimes.append(sorties[cle])
    if os.path.isdir(sorties["pages"]):
        shutil.rmtree(sorties["pages"])
        supprimes.append(sorties["pages"])
    return supprimes


def _chemin_page(dossier: str, numero: int) -> str:
    return os.path.join(dossier, f"page-{numero:04d}.json")


def lire_etat(chemin_pdf: str) -> dict | None:
    """
    État du nettoyage de ce PDF. Un état « actif » dont le job n'existe plus dans ce process vient d'un arrêt du
    backend : il devient `annule`, donc reprenable, sinon Arrêter et Recommencer restent bloqués pour toujours.
    C'est fait à la lecture et non dans `recuperation.recuperer_tout` : un PDF nettoyé mais pas encore traduit
    n'est pas au registre, le balayage du démarrage ne le trouverait pas.
    """
    chemin_etat = chemins_sortie(chemin_pdf)["etat"]
    etat = lire_json_tolerant(chemin_etat)
    if not isinstance(etat, dict):
        return None
    if etat.get("statut") in ("en_attente", "en_cours") and not job_actif(etat.get("job_id")):
        etat["statut"] = "annule"
        ecrire_json_atomique(chemin_etat, etat)
    return etat


def _sauvegarder_etat(etat: dict) -> None:
    ecrire_json_atomique(etat["chemin_etat"], etat)


def reglages_du_markdown(etat: dict | None) -> dict | None:
    """
    Plage et `double_page` du scan qui a ÉCRIT le Markdown nettoyé, à ne pas confondre avec ceux du dernier scan
    LANCÉ : l'état est écrasé dès le lancement, le Markdown seulement à la fin, et jamais si le scan est arrêté.
    Un état écrit avant ce champ n'est fiable que s'il est `termine`. None : inconnu.
    """
    if not isinstance(etat, dict):
        return None
    if isinstance(etat.get("markdown_ecrit"), dict):
        return etat["markdown_ecrit"]
    if etat.get("statut") == "termine":
        return {"page_debut": etat.get("page_debut"), "page_fin": etat.get("page_fin"),
                "double_page": etat.get("double_page")}
    return None


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
    (langue, double page, régions posées à la main) : réutiliser ce résultat serait le servir pour la
    mauvaise demande."""
    chemin = _chemin_page(dossier, numero)
    manuel = reglage_page(chemin_pdf, numero)
    reglages = {"langue": langue, "double_page": double_page, "algo": VERSION_ALGO, "regions": manuel}
    existant = lire_json_tolerant(chemin)
    if isinstance(existant, dict) and "blocs" in existant and existant.get("reglages") == reglages:
        return existant
    doc = _ouvrir_pdf(chemin_pdf)   # un handle par fil : PyMuPDF n'est pas thread-safe
    try:
        resultat = traiter_page(rendre_page(doc, numero), langue, double_page, manuel)
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
        # Contenu identique : on ne réécrit pas, la date du fichier dit si une traduction est périmée (reconstruction).
        existant = None
        if os.path.isfile(etat["chemin_sortie"]):
            with open(etat["chemin_sortie"], encoding="utf-8") as f:
                existant = f.read()
        if existant != markdown:
            ecrire_texte_atomique(etat["chemin_sortie"], markdown)
        etat["markdown_ecrit"] = {"page_debut": etat["page_debut"], "page_fin": etat["page_fin"],
                                  "double_page": etat.get("double_page")}
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
    precedent = reglages_du_markdown(lire_json_tolerant(sorties["etat"]))
    if precedent:       # ce scan peut s'arrêter avant d'écrire : le Markdown en place reste celui du précédent
        etat["markdown_ecrit"] = precedent
    enregistrer_job(etat["job_id"])     # avant l'état : un sondage ne doit jamais voir un état actif sans job
    _sauvegarder_etat(etat)
    soumettre_travail(etat["job_id"], lambda: _executer(etat))
    return etat
