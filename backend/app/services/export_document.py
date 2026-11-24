"""
Export d'un document traduit NATIF (PDF texte ou Markdown) — tout ce qui ne vient pas du Scan multi-colonnes.

Deux sorties, écrites à côté de la traduction sous les MÊMES noms que la reconstruction d'un scan
(services/reconstruction.py) : les boutons de la Bibliothèque, /telecharger et la signature les reconnaissent déjà.
  1. `<traduction>_interactif.html` : page autonome (images en data-URI, aucune ressource externe) — sommaire avec
     intertitres, chapitre suivi au défilement, recherche avec compteur, taille, thème, et la version d'origine en
     regard de chaque chapitre quand la source se lit ;
  2. `<traduction>_mise_en_page.pdf` : PDF A4 paginé (PyMuPDF Story), sommaire cliquable, signets par chapitre et
     intertitre, folios.

Il n'y a pas de géométrie de page à reproduire ici (pas de scan) : le texte est recomposé, pas replacé.

Chapitres : ceux de la Bibliothèque (`chapitres_avec_contenu`). Un sous-chapitre « couvert » est déjà dans le texte
de sa partie : il n'est pas répété. La version d'origine est lue comme le fait « ⇄ Comparer » (document source, avec
l'extracteur du travail) : le chapitre n de la traduction est le chapitre n de la source.

Sécurité : le texte vient d'un LLM et la page est ouverte dans un navigateur. Tout est ÉCHAPPÉ d'abord, puis une
liste blanche fermée de balises sans attribut est rétablie — même politique que le frontend
(module-bibliotheque.js › markdownEnLigne). Une image n'est lue que DANS le dossier de la traduction.
"""

from __future__ import annotations

import base64
import html as html_mod
import os
import re
import time
from typing import Callable

from app.services.controle_langue import MARQUE_NON_TRADUIT
from app.services.persistance import ecrire_binaire_atomique, ecrire_texte_atomique

LANGUES_HTML = {"français": "fr", "anglais": "en", "espagnol": "es", "allemand": "de", "italien": "it",
                "portugais": "pt"}
BALISES_AUTORISEES = ("sup", "sub", "em", "strong", "i", "b")
MIME_IMAGES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
               ".webp": "image/webp"}
# PDF : A4, marges de 2 cm ; le folio est écrit dans la marge basse.
MARGE_PDF_PT = 56.7


class ExportImpossible(Exception):
    """Le document ne peut pas être exporté (fichier illisible, aucun chapitre…)."""


# ── Markdown → HTML sûr ──────────────────────────────────────────────────────

def en_ligne(texte: str) -> str:
    """Markdown inline → HTML. Échappe TOUT, puis rétablit la liste blanche (portage de markdownEnLigne)."""
    s = html_mod.escape(texte, quote=True)
    codes: list[str] = []

    # Code littéral mis de côté AVANT la liste blanche : `<b>` dans du code reste du texte.
    def garder_code(m: re.Match) -> str:
        codes.append(m.group(1))
        return f"\x00CODE{len(codes) - 1}\x00"
    s = re.sub(r"`([^`]+)`", garder_code, s)
    for b in BALISES_AUTORISEES:
        s = re.sub(rf"&lt;(/?){b}&gt;", rf"<\1{b}>", s, flags=re.IGNORECASE)
    # Saut de ligne dans une cellule de tableau (pymupdf4llm écrit `<br>`) : balise vide, sans attribut.
    s = re.sub(r"&lt;br\s*/?&gt;", "<br>", s, flags=re.IGNORECASE)

    # Liens : protocole restreint (un `javascript:` serait exécutable au clic).
    s = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)",
               lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>'
               if re.match(r"^(https?://|#|mailto:)", m.group(2), re.IGNORECASE) else m.group(0), s)
    # Gras avant italique, sinon `**x**` serait lu comme deux italiques.
    s = re.sub(r"\*\*\*([^*]+)\*\*\*", r"<strong><em>\1</em></strong>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:!?]|$)", r"\1<em>\2</em>", s)
    # Tirets bas bornés par une frontière : `nom_de_variable` reste tel quel.
    s = re.sub(r"(^|[\s(])__([^_\n]+)__(?=[\s).,;:!?]|$)", r"\1<strong>\2</strong>", s)
    s = re.sub(r"(^|[\s(<>])_([^_\n]+)_(?=[\s).,;:!?<]|$)", r"\1<em>\2</em>", s)
    s = re.sub(r"~~([^~]+)~~", r"<del>\1</del>", s)
    return re.sub(r"\x00CODE(\d+)\x00", lambda m: f"<code>{codes[int(m.group(1))]}</code>", s)


def texte_brut(texte: str) -> str:
    """Titre sans marques Markdown (sommaire, signets) — comme titreChapitreListe côté frontend."""
    t = re.sub(r"<[^>]+>", "", texte or "")
    t = re.sub(r"\*\*|__|`", "", t)
    t = re.sub(r"^#+\s*", "", t)
    t = re.sub(r"(^|[^\w])[*_](\S(?:.*?\S)?)[*_](?=[^\w]|$)", r"\1\2", t)
    return re.sub(r"\s+", " ", t).strip()


def _norm(t: str) -> str:
    return re.sub(r"[\W_]+", " ", texte_brut(t)).strip().lower()


_RE_NUMERO_TITRE = re.compile(r"^(\d+(?:\.\d+)+)\b")


def _balise_titre(niveau_md: int, texte: str) -> str:
    """h3 à h5. Les titres extraits d'un PDF sont presque tous en `######` : la numérotation (« 5.4.1 ») dit mieux
    la profondeur que le nombre de dièses."""
    m = _RE_NUMERO_TITRE.match(texte_brut(texte))
    if m:
        return f"h{min(2 + m.group(1).count('.'), 5)}"
    return "h3" if niveau_md <= 2 else "h4"


def markdown_en_html(md: str, attribut_image: Callable[[str], str | None], prefixe: str,
                     titres: list[tuple[int, str, str]] | None = None, titre_chapitre: str = "") -> tuple[str, int]:
    """
    Corps Markdown d'un chapitre → (HTML, nombre d'images). Chaque intertitre reçoit un id `<prefixe>-tN` et est
    ajouté à `titres` (niveau 3‑5, texte brut, id). Le premier titre, s'il répète celui du chapitre (déjà en h2),
    est sauté. `attribut_image(chemin_relatif)` donne l'attribut HTML qui désigne l'image (`src="…"`, ou
    `data-img="k"` dans la page interactive), ou None si elle est introuvable.
    """
    out: list[str] = []
    paragraphe: list[str] = []
    liste: str | None = None
    citation: list[str] = []
    tableau: list[str] = []
    code: list[str] | None = None
    non_traduit = False
    titre_vu = False
    nb_images = 0
    nb_titres = 0

    def vider():
        nonlocal paragraphe, non_traduit
        if paragraphe:
            cls = ' class="non-traduit" title="Laissé en langue d\'origine"' if non_traduit else ""
            out.append(f"<p{cls}>{en_ligne(' '.join(paragraphe))}</p>")
            non_traduit = False
        paragraphe = []

    def fermer_liste():
        nonlocal liste
        if liste:
            out.append(f"</{liste}>")
            liste = None

    def fermer_citation():
        nonlocal citation
        if citation:
            out.append(f"<blockquote><p>{en_ligne(' '.join(citation))}</p></blockquote>")
            citation = []

    def fermer_tableau():
        nonlocal tableau
        if not tableau:
            return
        def cellules(ligne: str) -> list[str]:
            return [c.strip() for c in re.sub(r"^\||\|$", "", ligne.strip()).split("|")]
        def est_separation(ligne: str) -> bool:
            return bool(re.match(r"^\|?[\s:|-]+\|[\s:|-]*$", ligne.strip()))
        avec_entete = len(tableau) > 1 and est_separation(tableau[1])
        lignes = [l for l in tableau if not est_separation(l)]
        rangées = []
        for i, l in enumerate(lignes):
            b = "th" if avec_entete and i == 0 else "td"
            rangées.append("<tr>" + "".join(f"<{b}>{en_ligne(c)}</{b}>" for c in cellules(l)) + "</tr>")
        out.append('<div class="tableau"><table>' + "".join(rangées) + "</table></div>")
        tableau = []

    def tout_fermer():
        vider(); fermer_liste(); fermer_citation(); fermer_tableau()

    for ligne in md.split("\n"):
        t = ligne.strip()
        if code is not None:                       # bloc de code clôturé : rien n'est interprété dedans
            if t.startswith("```"):
                out.append(f"<pre><code>{html_mod.escape(chr(10).join(code))}</code></pre>")
                code = None
            else:
                code.append(ligne)
            continue
        if t.startswith("```"):
            tout_fermer()
            code = []
            continue
        if t == MARQUE_NON_TRADUIT:
            vider()
            non_traduit = True
            continue
        if re.match(r"^<!--.*-->$", t):            # marqueurs de chapitre, marques internes : invisibles
            continue
        image = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)$", t)
        titre = re.match(r"^(#{1,6})\s+(.*)", ligne)
        puce = re.match(r"^\s*[-*+]\s+(.*)", ligne)
        numero = re.match(r"^\s*\d+[.)]\s+(.*)", ligne)
        cite = re.match(r"^\s*>\s?(.*)", ligne)
        if image:
            tout_fermer()
            attribut = attribut_image(image.group(2))
            alt = html_mod.escape(image.group(1))
            if attribut:
                out.append(f'<figure><img {attribut} alt="{alt}"></figure>')
                nb_images += 1
            else:
                out.append(f'<p class="image-absente">[image introuvable : {html_mod.escape(image.group(2))}]</p>')
        elif t.startswith("|") and t.endswith("|") and len(t) > 1:
            vider(); fermer_liste(); fermer_citation()
            tableau.append(ligne)
        elif re.match(r"^([-*_]\s*){3,}$", t):
            tout_fermer()
            out.append("<hr>")
        elif titre:
            tout_fermer()
            texte = titre.group(2).strip()
            premier = not titre_vu
            titre_vu = True
            if premier and not out and _norm(texte) == _norm(titre_chapitre):
                continue
            balise = _balise_titre(len(titre.group(1)), texte)
            nb_titres += 1
            ident = f"{prefixe}-t{nb_titres}"
            out.append(f'<{balise} id="{ident}">{en_ligne(texte)}</{balise}>')
            if titres is not None and texte_brut(texte):
                titres.append((int(balise[1]), texte_brut(texte), ident))
        elif cite:
            vider(); fermer_liste(); fermer_tableau()
            citation.append(cite.group(1))
        elif puce or numero:
            vider(); fermer_citation(); fermer_tableau()
            voulue = "ul" if puce else "ol"
            if liste != voulue:
                fermer_liste()
                out.append(f"<{voulue}>")
                liste = voulue
            retrait = (len(ligne) - len(ligne.lstrip(" "))) // 2
            style = f' style="margin-left:{retrait * 1.2}em"' if retrait else ""
            out.append(f"<li{style}>{en_ligne((puce or numero).group(1))}</li>")
        elif t == "":
            tout_fermer()
        else:
            fermer_liste(); fermer_citation(); fermer_tableau()
            paragraphe.append(t)
    if code is not None:   # bloc jamais refermé : rendu quand même, rien n'est perdu
        out.append(f"<pre><code>{html_mod.escape(chr(10).join(code))}</code></pre>")
    tout_fermer()
    return "\n".join(out), nb_images


# ── Lecture du document ──────────────────────────────────────────────────────

def _chemin_image(dossier: str, relatif: str) -> str | None:
    """Image `![](relatif)` → chemin réel, jamais hors de `dossier` (pas d'absolu, pas de `..`), ou None."""
    racine = os.path.realpath(dossier)
    chemin = os.path.realpath(os.path.join(racine, relatif))
    ok = (chemin.startswith(racine + os.sep) and os.path.splitext(chemin)[1].lower() in MIME_IMAGES
          and os.path.isfile(chemin))
    return chemin if ok else None


def _images_pdf(dossier: str) -> Callable[[str], str | None]:
    """PDF : `src` relatif, lu par l'archive PyMuPDF ouverte sur `dossier`."""
    def attribut(relatif: str) -> str | None:
        chemin = _chemin_image(dossier, relatif)
        return f'src="{html_mod.escape(os.path.relpath(chemin, os.path.realpath(dossier)))}"' if chemin else None
    return attribut


class _RegistreImages:
    """
    Page interactive : chaque image n'est encodée qu'UNE fois (data-URI dans un bloc JSON), puis posée par le script
    quand elle approche de l'écran. Sans cela, la même figure était embarquée deux fois (traduction + original) :
    17 Mo pour Advances in Financial ML, dont 11 d'images.
    """

    def __init__(self) -> None:
        self.cles: dict[str, str] = {}
        self.donnees: dict[str, str] = {}

    def pour(self, dossier: str) -> Callable[[str], str | None]:
        def attribut(relatif: str) -> str | None:
            chemin = _chemin_image(dossier, relatif)
            if not chemin:
                return None
            if chemin not in self.cles:
                cle = str(len(self.cles))
                with open(chemin, "rb") as f:
                    mime = MIME_IMAGES[os.path.splitext(chemin)[1].lower()]
                    self.donnees[cle] = f"data:{mime};base64," + base64.b64encode(f.read()).decode("ascii")
                self.cles[chemin] = cle
            return f'data-img="{self.cles[chemin]}"'
        return attribut

    def json(self) -> str:
        import json
        # `</` échappé : le bloc vit dans un <script>, une donnée ne doit jamais pouvoir le refermer.
        return json.dumps(self.donnees).replace("</", "<\\/")


def _etat(chemin_traduit: str):
    try:
        from app.services.job_manager import charger_etat
        return charger_etat(chemin_traduit)
    except Exception:  # noqa: BLE001 — sans état : pas de version d'origine ni de langue, l'export reste possible
        return None


def _valeur(v) -> str | None:
    return getattr(v, "value", v) if v is not None else None


def lire_document(chemin_traduit: str, avec_source: bool) -> dict:
    """Chapitres à exporter (sommet seulement) + version d'origine de chacun si demandée et lisible."""
    from app.services.pdf_extractor import chapitres_avec_contenu

    try:
        chapitres = chapitres_avec_contenu(chemin_traduit)
    except Exception as e:  # noqa: BLE001
        raise ExportImpossible(f"Traduction illisible : {e}") from e
    chapitres = [c for c in chapitres if not c.get("couvert")]
    if not chapitres:
        raise ExportImpossible("Aucun chapitre dans cette traduction.")

    etat = _etat(chemin_traduit)
    source_par_index: dict[int, str] = {}
    raison_source = None
    if avec_source:
        chemin_source = getattr(etat, "chemin_pdf", None)
        if not chemin_source or not os.path.isfile(chemin_source):
            raison_source = "document d'origine introuvable"
        else:
            try:
                extracteur = _valeur(getattr(etat, "extracteur_pdf", None)) or "pymupdf4llm"
                source_par_index = {c["index"]: c.get("contenu") or ""
                                    for c in chapitres_avec_contenu(chemin_source, extracteur)}
            except Exception as e:  # noqa: BLE001 — la traduction s'exporte quand même, sans original
                raison_source = f"document d'origine illisible ({e})"
    return {
        "chapitres": chapitres,
        "source": source_par_index,
        "raison_source": raison_source,
        "langue_source": _valeur(getattr(etat, "langue_source", None)),
        "langue_cible": _valeur(getattr(etat, "langue_cible", None)),
        "modele": getattr(etat, "modele", None),
    }


def _a_du_texte(md: str) -> bool:
    return any(l.strip() and not re.match(r"^\s*<!--.*-->\s*$", l) for l in (md or "").split("\n"))


def _titre_chapitre(c: dict) -> str:
    return texte_brut(c.get("titre_traduit") or c.get("titre") or f"Chapitre {c['index']}")


def chemins_sortie(chemin_traduit: str) -> dict:
    base = os.path.splitext(chemin_traduit)[0]
    return {"html": f"{base}_interactif.html", "pdf": f"{base}_mise_en_page.pdf"}


def info(chemin_traduit: str) -> dict:
    sorties = chemins_sortie(chemin_traduit)
    return {
        "disponible": True,
        "mode": "document",
        "pdf_genere": sorties["pdf"] if os.path.isfile(sorties["pdf"]) else None,
        "html_genere": sorties["html"] if os.path.isfile(sorties["html"]) else None,
    }


# ── 1. HTML interactif ───────────────────────────────────────────────────────

def generer_html(chemin_traduit: str, titre: str | None = None) -> dict:
    """Écrit `<traduction>_interactif.html`. Retourne {chemin, mode, chapitres, illustrations, original, octets}."""
    doc = lire_document(chemin_traduit, avec_source=True)
    dossier = os.path.dirname(chemin_traduit)
    registre = _RegistreImages()
    images_trad = registre.pour(dossier)
    source_pdf = getattr(_etat(chemin_traduit), "chemin_pdf", None)
    images_src = registre.pour(os.path.dirname(source_pdf)) if source_pdf else (lambda _r: None)

    sections, sommaire, nb_images, avec_original = [], [], 0, 0
    langue_src = doc["langue_source"] or "source"
    for c in doc["chapitres"]:
        md = c.get("contenu") or ""
        if not _a_du_texte(md):
            continue
        ident = f"c{c['index']}"
        titre_c = _titre_chapitre(c)
        titres: list[tuple[int, str, str]] = []
        corps, n = markdown_en_html(md, images_trad, ident, titres, titre_c)
        nb_images += n
        original = doc["source"].get(c["index"])
        bloc_orig = ""
        if original and _a_du_texte(original):
            html_orig, _ = markdown_en_html(original, images_src, f"{ident}o", None, c.get("titre") or "")
            bloc_orig = (f'<div class="orig" lang="{LANGUES_HTML.get(langue_src, "und")}">'
                         f'<p class="etiquette">Version d\'origine ({html_mod.escape(langue_src)})</p>{html_orig}</div>')
            avec_original += 1
        sections.append(f'<section class="chap" id="{ident}"><h2>{html_mod.escape(titre_c)}</h2>'
                        f'<div class="cols"><div class="trad">{corps}</div>{bloc_orig}</div></section>')
        sous = "".join(f'<li class="n{niv}"><a href="#{i}">{html_mod.escape(t)}</a></li>' for niv, t, i in titres)
        entree = f'<a href="#{ident}" data-chap="{ident}">{html_mod.escape(titre_c)}</a>'
        if sous:
            entree = (f'<details><summary>{entree}</summary><ul class="sous">{sous}</ul></details>')
        sommaire.append(f"<li>{entree}</li>")
    if not sections:
        raise ExportImpossible("Aucun chapitre traduit n'a de texte : rien à exporter.")

    titre = titre or os.path.basename(os.path.splitext(chemin_traduit)[0])
    meta = [f"{len(sections)} chapitre(s)", f"{nb_images} image(s)"]
    if doc["langue_source"] and doc["langue_cible"]:
        meta.append(f"{doc['langue_source']} → {doc['langue_cible']}")
    if doc["modele"]:
        meta.append(f"modèle {doc['modele']}")
    meta.append(f"généré le {time.strftime('%d/%m/%Y %H:%M')}")
    if doc["raison_source"]:
        meta.append(f"version d'origine absente : {doc['raison_source']}")
    page = (_GABARIT_HTML
            .replace("{{TITRE}}", html_mod.escape(titre))
            .replace("{{LANG}}", LANGUES_HTML.get(doc["langue_cible"] or "", "und"))
            .replace("{{ORIGINAL}}", "" if avec_original else "hidden")
            .replace("{{META}}", html_mod.escape(" · ".join(meta)))
            .replace("{{SOMMAIRE}}", "\n".join(sommaire))
            .replace("{{IMAGES}}", registre.json())
            .replace("{{CHAPITRES}}", "\n".join(sections)))
    sortie = chemins_sortie(chemin_traduit)["html"]
    ecrire_texte_atomique(sortie, page)
    return {"chemin": sortie, "mode": "document", "chapitres": len(sections), "illustrations": nb_images,
            "original": avec_original, "octets": len(page.encode("utf-8"))}


# ── 2. PDF paginé ────────────────────────────────────────────────────────────

_CSS_PDF = """
body { font-family: serif; font-size: 10.5pt; line-height: 1.4; color: #1a1a1a; }
p { margin: 0 0 5pt 0; text-align: justify; }
h1 { font-size: 22pt; margin: 140pt 0 10pt 0; text-align: center; }
p.meta { text-align: center; color: #666; font-size: 9pt; }
h2 { font-size: 17pt; margin: 0 0 10pt 0; color: #0f5c5a; page-break-before: always; }
h3 { font-size: 13pt; margin: 12pt 0 4pt 0; color: #0f5c5a; }
h4 { font-size: 11.5pt; margin: 10pt 0 3pt 0; }
h5 { font-size: 10.5pt; margin: 8pt 0 2pt 0; font-style: italic; }
h2.sommaire { page-break-before: always; }
ul.sommaire { list-style: none; padding: 0; margin: 0; }
ul.sommaire li { margin: 0 0 3pt 0; }
ul.sommaire a { color: #1a1a1a; text-decoration: none; }
a { color: #0f5c5a; }
img { width: 100%; }
figure { margin: 6pt 0; text-align: center; }
blockquote { margin: 6pt 0 6pt 12pt; color: #444; }
pre { font-family: monospace; font-size: 8pt; background-color: #f2f2f2; padding: 4pt; }
code { font-family: monospace; font-size: 9pt; }
table { border-collapse: collapse; margin: 6pt 0; }
th, td { border: 0.5pt solid #bbb; padding: 2pt 4pt; font-size: 9pt; text-align: left; vertical-align: top; }
th { background-color: #eee; }
.non-traduit { background-color: #fbe3b4; }
.image-absente { color: #a00; font-size: 9pt; }
hr { border: 0; border-top: 0.5pt solid #ccc; }
"""


def generer_pdf(chemin_traduit: str, titre: str | None = None) -> dict:
    """Écrit `<traduction>_mise_en_page.pdf`. Retourne {chemin, mode, pages, chapitres, illustrations}."""
    import pymupdf

    doc = lire_document(chemin_traduit, avec_source=False)
    dossier = os.path.dirname(chemin_traduit)
    images = _images_pdf(dossier)
    titre = titre or os.path.basename(os.path.splitext(chemin_traduit)[0])

    sections, sommaire, nb_images = [], [], 0
    for c in doc["chapitres"]:
        md = c.get("contenu") or ""
        if not _a_du_texte(md):
            continue
        ident = f"c{c['index']}"
        titre_c = _titre_chapitre(c)
        corps, n = markdown_en_html(md, images, ident, None, titre_c)
        nb_images += n
        sections.append(f'<h2 id="{ident}">{html_mod.escape(titre_c)}</h2>{corps}')
        sommaire.append(f'<li><a href="#{ident}">{html_mod.escape(titre_c)}</a></li>')
    if not sections:
        raise ExportImpossible("Aucun chapitre traduit n'a de texte : rien à exporter.")

    meta = " · ".join(x for x in [
        f"{doc['langue_source']} → {doc['langue_cible']}" if doc["langue_source"] and doc["langue_cible"] else "",
        f"modèle {doc['modele']}" if doc["modele"] else "",
        f"généré le {time.strftime('%d/%m/%Y')}",
    ] if x)
    html = (f'<h1>{html_mod.escape(titre)}</h1><p class="meta">{html_mod.escape(meta)}</p>'
            f'<h2 class="sommaire" id="sommaire">Sommaire</h2><ul class="sommaire">{"".join(sommaire)}</ul>'
            + "".join(sections))

    signets: list[list] = []

    def position(p) -> None:
        # Signets : chapitres (h2) au niveau 1, intertitres en dessous. Le titre du document (h1) n'en est pas un.
        if p.heading and p.heading >= 2 and (p.open_close & 1) and p.text.strip():
            signets.append([p.heading - 1, texte_brut(p.text), p.page_num])

    page_rect = pymupdf.paper_rect("a4")
    contenu = page_rect + (MARGE_PDF_PT, MARGE_PDF_PT, -MARGE_PDF_PT, -MARGE_PDF_PT)
    story = pymupdf.Story(html=html, user_css=_CSS_PDF, archive=pymupdf.Archive(dossier))
    sortie = story.write_with_links(lambda _n, _f: (page_rect, contenu, None), position)

    # Un signet ne peut descendre que d'un niveau à la fois (règle de set_toc).
    precedent = 0
    for s in signets:
        s[0] = max(1, min(s[0], precedent + 1))
        precedent = s[0]
    sortie.set_toc(signets)
    total = sortie.page_count
    court = titre if len(titre) <= 70 else titre[:67] + "…"
    for i, page in enumerate(sortie):
        if i == 0:
            continue
        page.insert_text((MARGE_PDF_PT, page_rect.height - 30), court, fontsize=7.5, color=(0.45, 0.45, 0.45))
        folio = f"{i + 1} / {total}"
        largeur = pymupdf.get_text_length(folio, fontsize=8)
        page.insert_text((page_rect.width - MARGE_PDF_PT - largeur, page_rect.height - 30), folio, fontsize=8,
                         color=(0.35, 0.35, 0.35))
    sortie.set_metadata({"title": titre, "creator": "Traducteur PDF (toledo) — export du document traduit"})
    chemin = chemins_sortie(chemin_traduit)["pdf"]
    ecrire_binaire_atomique(chemin, sortie.tobytes(garbage=3, deflate=True))
    sortie.close()
    return {"chemin": chemin, "mode": "document", "pages": total, "chapitres": len(sections),
            "illustrations": nb_images, "pages_suite": []}


_GABARIT_HTML = r"""<!DOCTYPE html>
<html lang="{{LANG}}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{TITRE}}</title>
<style>
:root { --fond:#f6f7f7; --surface:#ffffff; --texte:#1b1f22; --doux:#5f676d; --accent:#0f6e6a; --accent-doux:#d9efed; --bord:#dde2e4; --mark:#f5d76e; --taille:17px; }
:root[data-theme="dark"] { --fond:#121517; --surface:#1b1f22; --texte:#e6eaec; --doux:#9aa4aa; --accent:#5fc4bd; --accent-doux:#1f3a39; --bord:#30363a; --mark:#8a6d00; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --fond:#121517; --surface:#1b1f22; --texte:#e6eaec; --doux:#9aa4aa; --accent:#5fc4bd; --accent-doux:#1f3a39; --bord:#30363a; --mark:#8a6d00; } }
* { box-sizing:border-box; }
html { scroll-behavior:smooth; scroll-padding-top:64px; }
body { margin:0; background:var(--fond); color:var(--texte); font:var(--taille)/1.65 Georgia, "Iowan Old Style", "Palatino Linotype", serif; }
.ui { font-family:-apple-system, system-ui, sans-serif; font-size:14px; }
header { position:sticky; top:0; z-index:5; display:flex; flex-wrap:wrap; gap:6px; align-items:center; padding:8px 16px; background:var(--surface); border-bottom:1px solid var(--bord); }
header h1 { font-size:15px; margin:0 auto 0 0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:36vw; }
header button, header input { font:inherit; padding:5px 10px; border-radius:6px; border:1px solid var(--bord); background:var(--fond); color:var(--texte); cursor:pointer; }
header button[aria-pressed="true"] { background:var(--accent); color:var(--surface); border-color:var(--accent); }
header input { cursor:text; width:160px; }
#compte { color:var(--doux); min-width:4.5em; font-variant-numeric:tabular-nums; }
#progression { position:absolute; left:0; bottom:-1px; height:2px; background:var(--accent); width:0; }
.mise { display:grid; grid-template-columns:280px minmax(0,1fr); max-width:1400px; margin:0 auto; }
nav { position:sticky; top:50px; align-self:start; max-height:calc(100vh - 50px); overflow:auto; padding:16px 12px 40px 16px; }
nav ul { list-style:none; padding-left:0; margin:6px 0; } nav li { margin:3px 0; }
nav a { color:var(--texte); text-decoration:none; display:inline-block; padding:1px 4px; border-radius:4px; }
nav a:hover { color:var(--accent); } nav a.actif { background:var(--accent-doux); color:var(--accent); font-weight:600; }
nav summary { cursor:pointer; } nav summary::marker { color:var(--doux); }
nav ul.sous { padding-left:14px; font-size:12.5px; } nav ul.sous .n4 { padding-left:10px; } nav ul.sous .n5 { padding-left:20px; }
nav .meta { color:var(--doux); font-size:11.5px; margin-top:14px; }
main { padding:16px 28px 120px; min-width:0; }
.chap { background:var(--surface); border:1px solid var(--bord); border-radius:10px; padding:24px 32px; margin:0 0 22px; }
.chap > h2 { font-size:1.6em; line-height:1.25; color:var(--accent); margin:0 0 .6em; }
.cols { display:block; }
.trad, .orig { max-width:44em; margin:0 auto; text-align:justify; hyphens:auto; min-width:0; overflow-wrap:anywhere; }
.orig { display:none; }
body.regard .cols { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:32px; }
body.regard .orig { display:block; border-left:1px solid var(--bord); padding-left:24px; color:var(--doux); }
body.regard .trad, body.regard .orig { max-width:none; font-size:.92em; }
.etiquette { font:600 12px -apple-system, system-ui, sans-serif; text-transform:uppercase; letter-spacing:.05em; color:var(--doux); text-align:left; }
h3, h4, h5 { text-align:left; line-height:1.3; }
h3 { font-size:1.3em; color:var(--accent); margin:1.5em 0 .5em; } h4 { font-size:1.12em; margin:1.3em 0 .4em; } h5 { font-size:1em; font-style:italic; margin:1.1em 0 .3em; }
figure { margin:1.2em 0; text-align:center; }
figure img { max-width:100%; height:auto; min-height:2em; border-radius:6px; cursor:zoom-in; background:#fff; }
blockquote { margin:1em 0; padding:.2em 0 .2em 1em; border-left:3px solid var(--bord); color:var(--doux); }
pre { background:var(--fond); border:1px solid var(--bord); border-radius:6px; padding:.8em 1em; overflow:auto; font-size:.8em; line-height:1.45; text-align:left; }
code { font-family:ui-monospace, Menlo, monospace; font-size:.88em; }
.tableau { overflow-x:auto; margin:1em 0; }
table { border-collapse:collapse; font-size:.88em; }
th, td { border:1px solid var(--bord); padding:.35em .6em; text-align:left; vertical-align:top; }
th { background:var(--fond); }
sup, sub { line-height:0; }
hr { border:0; border-top:1px solid var(--bord); margin:2em 0; }
.non-traduit { background:#fbe3b4; color:#3a2a1c; border-left:4px solid #c77d12; padding-left:.4em; }
.image-absente { color:var(--doux); font-style:italic; }
mark { background:var(--mark); color:inherit; border-radius:2px; }
mark.courant { outline:2px solid var(--accent); }
#haut { position:fixed; right:18px; bottom:18px; z-index:6; width:40px; height:40px; border-radius:50%; border:1px solid var(--bord); background:var(--surface); color:var(--texte); cursor:pointer; font-size:18px; opacity:0; pointer-events:none; transition:opacity .2s; }
#haut.visible { opacity:1; pointer-events:auto; }
#loupe { position:fixed; inset:0; background:rgba(0,0,0,.85); display:none; align-items:center; justify-content:center; z-index:20; cursor:zoom-out; }
#loupe.ouvert { display:flex; } #loupe img { max-width:96vw; max-height:94vh; background:#fff; }
#bascule-sommaire { display:none; }
@media (max-width: 900px) {
  .mise { grid-template-columns:1fr; } nav { position:static; max-height:none; border-bottom:1px solid var(--bord); display:none; }
  body.sommaire-ouvert nav { display:block; } #bascule-sommaire { display:inline-block; }
  main { padding:12px 16px 100px; } .chap { padding:16px; } header h1 { max-width:100%; flex-basis:100%; }
  body.regard .cols { grid-template-columns:1fr; } body.regard .orig { border-left:0; padding-left:0; border-top:1px solid var(--bord); padding-top:12px; }
}
@media print { header, nav, #haut, #loupe { display:none !important; } .mise { display:block; } .chap { border:0; break-before:page; } .orig { display:none !important; } }
</style>
</head>
<body>
<header class="ui">
  <h1>{{TITRE}}</h1>
  <button type="button" id="bascule-sommaire" aria-expanded="false">☰ Sommaire</button>
  <button type="button" id="regard" aria-pressed="false" {{ORIGINAL}} title="Afficher la version d'origine à côté de la traduction, chapitre par chapitre">⇄ Original en regard</button>
  <input type="search" id="chercher" placeholder="Rechercher…" aria-label="Rechercher dans la traduction">
  <span id="compte" aria-live="polite"></span>
  <button type="button" id="prec" aria-label="Occurrence précédente">↑</button>
  <button type="button" id="suiv" aria-label="Occurrence suivante">↓</button>
  <button type="button" id="moins" aria-label="Réduire le texte">A−</button>
  <button type="button" id="plus" aria-label="Agrandir le texte">A+</button>
  <button type="button" id="theme" aria-label="Changer de thème">◐</button>
  <div id="progression"></div>
</header>
<div class="mise">
  <nav class="ui" aria-label="Sommaire"><strong>Sommaire</strong><ul>{{SOMMAIRE}}</ul><p class="meta">{{META}}</p></nav>
  <main>{{CHAPITRES}}</main>
</div>
<button type="button" id="haut" class="ui" aria-label="Revenir en haut">↑</button>
<div id="loupe" role="dialog" aria-label="Image agrandie"><img alt=""></div>
<script type="application/json" id="images">{{IMAGES}}</script>
<script>
(function () {
  var racine = document.documentElement, corps = document.body;
  function lire(k, d) { try { var v = localStorage.getItem(k); return v === null ? d : v; } catch (e) { return d; } }
  function ecrire(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
  function $(id) { return document.getElementById(id); }

  // Original en regard
  var regard = $("regard");
  function appliquerRegard(oui) { corps.classList.toggle("regard", oui); regard.setAttribute("aria-pressed", String(oui)); ecrire("toledo-doc-regard", oui ? "1" : "0"); }
  regard.addEventListener("click", function () { appliquerRegard(!corps.classList.contains("regard")); });
  if (!regard.hidden) appliquerRegard(lire("toledo-doc-regard", "0") === "1");

  // Taille et thème
  var taille = parseFloat(lire("toledo-taille", "17")) || 17;
  function appliquerTaille() { racine.style.setProperty("--taille", taille + "px"); ecrire("toledo-taille", String(taille)); }
  $("moins").onclick = function () { taille = Math.max(12, taille - 1); appliquerTaille(); };
  $("plus").onclick = function () { taille = Math.min(26, taille + 1); appliquerTaille(); };
  appliquerTaille();
  var theme = lire("toledo-theme", "");
  if (theme) racine.dataset.theme = theme;
  $("theme").onclick = function () {
    var sombre = racine.dataset.theme ? racine.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    racine.dataset.theme = sombre ? "light" : "dark"; ecrire("toledo-theme", racine.dataset.theme);
  };

  // Sommaire (petit écran) et chapitre courant
  $("bascule-sommaire").onclick = function () {
    var ouvert = corps.classList.toggle("sommaire-ouvert"); this.setAttribute("aria-expanded", String(ouvert));
  };
  document.querySelector("nav").addEventListener("click", function (e) {
    if (e.target.closest("a") && matchMedia("(max-width: 900px)").matches) { corps.classList.remove("sommaire-ouvert"); }
  });
  var liens = {};
  document.querySelectorAll("nav a[data-chap]").forEach(function (a) { liens[a.dataset.chap] = a; });
  var actif = null;
  if ("IntersectionObserver" in window) {
    var obs = new IntersectionObserver(function (entrees) {
      entrees.forEach(function (en) {
        if (!en.isIntersecting) return;
        var a = liens[en.target.id];
        if (!a || a === actif) return;
        if (actif) actif.classList.remove("actif");
        actif = a; a.classList.add("actif");
        var d = a.closest("details"); if (d) d.open = true;
        a.scrollIntoView({ block: "nearest" });
      });
    }, { rootMargin: "-60px 0px -70% 0px" });
    document.querySelectorAll("section.chap").forEach(function (s) { obs.observe(s); });
  }
  var barre = $("progression"), haut = $("haut");
  window.addEventListener("scroll", function () {
    var max = document.documentElement.scrollHeight - innerHeight;
    barre.style.width = (max > 0 ? scrollY / max * 100 : 0) + "%";
    haut.classList.toggle("visible", scrollY > 800);
  }, { passive: true });
  haut.onclick = function () { scrollTo({ top: 0 }); };

  // Images : une seule copie de chaque (bloc JSON), posée à l'approche de l'écran.
  var images = {};
  try { images = JSON.parse($("images").textContent); } catch (e) {}
  function poser(img) { if (!img.src && images[img.dataset.img]) img.src = images[img.dataset.img]; }
  var toutes = document.querySelectorAll("img[data-img]");
  if ("IntersectionObserver" in window) {
    var obsImg = new IntersectionObserver(function (entrees) {
      entrees.forEach(function (en) { if (en.isIntersecting) { poser(en.target); obsImg.unobserve(en.target); } });
    }, { rootMargin: "800px 0px" });
    toutes.forEach(function (i) { obsImg.observe(i); });
  } else { toutes.forEach(poser); }
  window.addEventListener("beforeprint", function () { toutes.forEach(poser); });

  // Loupe
  var loupe = $("loupe");
  document.addEventListener("click", function (e) {
    if (e.target.matches("figure img")) { loupe.querySelector("img").src = e.target.src; loupe.classList.add("ouvert"); }
    else if (e.target.closest("#loupe")) loupe.classList.remove("ouvert");
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") loupe.classList.remove("ouvert"); });

  // Recherche dans la traduction : surligne, compte, va d'une occurrence à l'autre (Entrée, ↑ ↓).
  var marques = [], courant = -1;
  function effacer() {
    marques.forEach(function (m) { var p = m.parentNode; if (!p) return; p.replaceChild(document.createTextNode(m.textContent), m); p.normalize(); });
    marques = []; courant = -1;
  }
  function aller(i) {
    if (!marques.length) return;
    if (courant >= 0) marques[courant].classList.remove("courant");
    courant = (i + marques.length) % marques.length;
    var m = marques[courant]; m.classList.add("courant");
    var d = m.closest("details"); if (d) d.open = true;
    m.scrollIntoView({ block: "center" });
    $("compte").textContent = (courant + 1) + " / " + marques.length;
  }
  function chercher(q) {
    effacer();
    $("compte").textContent = "";
    if (q.length < 2) return;
    var bas = q.toLowerCase(), noeuds = [];
    var w = document.createTreeWalker(document.querySelector("main"), NodeFilter.SHOW_TEXT, {
      acceptNode: function (n) { return n.parentNode.closest(".orig") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT; }
    });
    while (w.nextNode()) { if (w.currentNode.nodeValue.toLowerCase().indexOf(bas) >= 0) noeuds.push(w.currentNode); }
    noeuds.forEach(function (n) {
      var texte = n.nodeValue, i, depart = 0, frag = document.createDocumentFragment();
      while ((i = texte.toLowerCase().indexOf(bas, depart)) >= 0) {
        frag.appendChild(document.createTextNode(texte.slice(depart, i)));
        var m = document.createElement("mark"); m.textContent = texte.slice(i, i + q.length);
        frag.appendChild(m); marques.push(m); depart = i + q.length;
        if (marques.length >= 2000) break;
      }
      frag.appendChild(document.createTextNode(texte.slice(depart)));
      n.parentNode.replaceChild(frag, n);
    });
    if (marques.length) aller(0); else $("compte").textContent = "0 résultat";
  }
  var minuterie;
  $("chercher").addEventListener("input", function (e) { clearTimeout(minuterie); var q = e.target.value.trim(); minuterie = setTimeout(function () { chercher(q); }, 300); });
  $("chercher").addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); aller(courant + (e.shiftKey ? -1 : 1)); } });
  $("suiv").onclick = function () { aller(courant + 1); };
  $("prec").onclick = function () { aller(courant - 1); };
})();
</script>
</body>
</html>
"""
