"""
Prototype (Laboratoire) — segmentation automatique d'un document SANS structure
(transcription brute : aucun signet PDF, aucun titre Markdown) en chapitres
virtuels, via deux passes LLM sur Ollama :

  1. détection de candidats — fenêtres glissantes, chaque fenêtre propose les
     changements de sujet qu'elle voit ;
  2. consolidation — les candidats bruts (souvent redondants d'une fenêtre à
     l'autre, ou trop mineurs) sont réduits à un nombre raisonnable de VRAIS
     chapitres.

Ne modifie AUCUN fichier et n'insère aucun marqueur — pur outil d'inspection
pour l'onglet Laboratoire. Le branchement sur le pipeline de traduction réel
(marqueurs `<!-- === chapitre N : titre === -->`) est une décision séparée,
volontairement pas prise ici.
"""

import json
import os
import re

import requests

from app.services.pdf_extractor import extraire_texte

_OLLAMA_URL = "http://localhost:11434/api/generate"

# Timeout par appel — court et SANS retry, à dessein. `appeler_ollama()`
# (translator.py) porte un budget de retry de 30 minutes, pensé pour un job de
# traduction en arrière-plan ; ce prototype est un appel SYNCHRONE où un
# humain attend dans le navigateur. Un aléa (Ollama lent une fois) qui
# déclenche ce budget de 30 min transforme un hoquet en une requête qui
# continue de tourner côté serveur bien après que le client ait abandonné —
# constaté en test réel le 15/9/2026 (le backend est resté bloqué après
# l'abandon du client, `/api/health` en est devenu injoignable). Une seule
# tentative, échec rapide et explicite plutôt qu'une attente cachée.
SEGMENTATION_TIMEOUT_SECONDES = 90


class SegmentationEchec(Exception):
    """Ollama injoignable, en erreur, ou trop lent — remonté tout de suite,
    jamais après plusieurs minutes de retry silencieux."""


def _appeler_ollama_labo(payload: dict) -> dict:
    try:
        r = requests.post(_OLLAMA_URL, json=payload, timeout=SEGMENTATION_TIMEOUT_SECONDES)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        raise SegmentationEchec(
            f"Ollama injoignable ou trop lent (>{SEGMENTATION_TIMEOUT_SECONDES}s) : {e}"
        ) from e


# Fenêtre volontairement plus grande qu'un chunk de traduction
# (CHAPITRE_SOUS_CHUNK_TAILLE_MAX = 1500 car.) : un vrai changement de sujet,
# dans une transcription, s'étale souvent sur plusieurs milliers de caractères
# — une fenêtre trop petite ne verrait que des micro-variations locales.
SEGMENTATION_FENETRE_CARACTERES = 12000
SEGMENTATION_CHEVAUCHEMENT = 1200
# num_ctx dédié, plus grand que OLLAMA_NUM_CTX (4096, dimensionné pour la
# traduction par petits chunks) : une fenêtre de 12k caractères + le prompt
# d'instructions dépasse largement ce budget, et il faut aussi laisser de la
# place à la réponse JSON. Mesuré en test réel (5/12 fenêtres, réponse jamais
# tronquée) : 8192 tokens suffit.
SEGMENTATION_NUM_CTX = 8192
SEGMENTATION_NUM_PREDICT = 800
SEGMENTATION_CIBLE_MAX_CHAPITRES = 8
# Deux candidats détectés à moins de ce nombre de caractères l'un de l'autre
# sont traités comme la même transition, vue deux fois par des fenêtres
# voisines qui se chevauchent — pas deux vrais changements distincts.
SEGMENTATION_SEUIL_DOUBLON = 200

# Estimation RAPIDE (aucun appel Ollama) du nombre de chapitres à viser,
# affichée avant de lancer la vraie segmentation (qui coûte plusieurs
# dizaines de secondes). Ratio mesuré sur un vrai document — conférence
# Pinker, 60 126 caractères, 8 chapitres retenus par llama3.1 (15/9/2026,
# voir CLAUDE.md) — PAS une analyse de contenu, juste un point de départ à
# partir de la longueur du texte. L'utilisateur reste libre d'ajuster.
SEGMENTATION_CARACTERES_PAR_CHAPITRE_ESTIME = 7500
SEGMENTATION_CIBLE_ESTIMEE_MIN = 4
SEGMENTATION_CIBLE_ESTIMEE_MAX = 15

PROMPT_CANDIDATS = """Voici un extrait d'une transcription BRUTE (conférence ou entretien).
Ta tâche : repérer si UN OU PLUSIEURS changements de sujet/thème nets se produisent DANS cet extrait
(la simple continuation d'un sujet déjà en cours ne compte pas).

Pour chaque changement trouvé, donne :
- une citation EXACTE (10-15 mots, copiée mot pour mot du texte) de l'endroit où le nouveau sujet commence
- un titre de chapitre court (5-8 mots, en français)

Réponds STRICTEMENT en JSON, rien d'autre, format :
{{"changements": [{{"citation": "...", "titre": "..."}}]}}
S'il n'y a AUCUN changement net de sujet, réponds {{"changements": []}}.

EXTRAIT :
---
{extrait}
---
JSON :"""

PROMPT_CONSOLIDATION = """Voici une liste de changements de sujet candidats, détectés dans une longue
transcription, dans leur ordre chronologique. Certains titres sont vagues ou
génériques (ex. « Changement de sujet : de X à Y », « Transition »).

{liste}

Ta tâche, en deux temps :
1. Choisis au MAXIMUM {cible_max} candidats qui représentent les VRAIS grands
   changements de sujet du document entier — fusionne les quasi-doublons,
   écarte les signaux mineurs.
2. Pour chacun, donne un titre de chapitre COURT (5-8 mots, en français),
   SPÉCIFIQUE au contenu réel de sa citation — jamais une formule générique
   comme « Changement de sujet », « Transition » ou « De X à Y ». Si le titre
   existant est déjà bon, garde-le tel quel plutôt que d'en inventer un pire.

Réponds STRICTEMENT en JSON, rien d'autre, format :
{{"chapitres_retenus": [{{"index": 0, "titre": "..."}}, {{"index": 3, "titre": "..."}}]}}
(dans l'ordre croissant des index)"""

_RE_BLOC_JSON = re.compile(r"\{.*\}", re.DOTALL)


def _extraire_json(reponse: str) -> dict:
    """
    Extrait le premier bloc JSON `{...}` d'une réponse LLM qui peut contenir du
    texte libre autour (constaté en test réel : le modèle ajoute parfois une
    phrase d'introduction avant le JSON demandé). Retourne {} si rien
    d'exploitable — jamais une exception qui ferait échouer toute la fenêtre.
    """
    m = _RE_BLOC_JSON.search(reponse)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def _localiser_citation(texte: str, citation: str) -> int | None:
    """
    Position (index caractère) de la citation dans le texte source, ou None si
    introuvable — jamais une position devinée ou interpolée.
    """
    citation = citation.strip().strip('"').strip()
    if not citation:
        return None
    pos = texte.find(citation)
    if pos != -1:
        return pos
    # Repli : le modèle reformule parfois légèrement la fin de la citation.
    # Les 6 premiers mots suffisent à être distinctifs dans une longue
    # transcription tout en tolérant cette divergence.
    mots = citation.split()
    if len(mots) > 6:
        pos = texte.find(" ".join(mots[:6]))
        if pos != -1:
            return pos
    return None


def _decouper_fenetres(texte: str, taille: int, chevauchement: int) -> list[str]:
    fenetres = []
    i = 0
    while i < len(texte):
        fenetres.append(texte[i:i + taille])
        if i + taille >= len(texte):
            break
        i += taille - chevauchement
    return fenetres


def _dedupliquer_par_position(candidats: list[dict], seuil: int = SEGMENTATION_SEUIL_DOUBLON) -> list[dict]:
    """Fusionne les quasi-doublons issus du chevauchement entre fenêtres —
    la MÊME transition, vue par deux fenêtres voisines. Candidats déjà triés
    par position ; garde la première occurrence de chaque groupe."""
    if not candidats:
        return []
    dedup = [candidats[0]]
    for c in candidats[1:]:
        if c["position"] - dedup[-1]["position"] > seuil:
            dedup.append(c)
    return dedup


def _lire_texte_source(chemin: str, extracteur: str) -> str:
    """Lit le texte brut d'un .md directement, ou l'extrait d'un .pdf."""
    if chemin.lower().endswith(".md"):
        with open(chemin, "r", encoding="utf-8") as f:
            return f.read()
    return extraire_texte(chemin, extracteur)


def estimer_cible_chapitres(nb_caracteres: int) -> int:
    """Estimation déterministe, à partir de la seule longueur — voir la note
    sur SEGMENTATION_CARACTERES_PAR_CHAPITRE_ESTIME plus haut."""
    brut = round(nb_caracteres / SEGMENTATION_CARACTERES_PAR_CHAPITRE_ESTIME)
    return max(SEGMENTATION_CIBLE_ESTIMEE_MIN, min(SEGMENTATION_CIBLE_ESTIMEE_MAX, brut))


def estimer_segmentation(chemin: str, extracteur: str) -> dict:
    """
    Lecture seule et rapide (aucun appel Ollama) : longueur du document +
    nombre de chapitres suggéré. Destinée à préremplir « Chapitres visés »
    avant de lancer la vraie segmentation, plutôt que de faire deviner un
    nombre à l'utilisateur.
    """
    texte = _lire_texte_source(chemin, extracteur)
    return {
        "nb_caracteres": len(texte),
        "cible_suggeree": estimer_cible_chapitres(len(texte)),
    }


def detecter_candidats(texte: str, modele: str) -> list[dict]:
    """
    Passe 1 — fenêtres glissantes, chaque fenêtre propose ses changements de
    sujet. Retourne les candidats triés par position, dédupliqués (chevauchement
    entre fenêtres), chacun {position, titre, citation}.
    """
    fenetres = _decouper_fenetres(texte, SEGMENTATION_FENETRE_CARACTERES, SEGMENTATION_CHEVAUCHEMENT)
    candidats: list[dict] = []
    for fenetre in fenetres:
        payload = {
            "model": modele,
            "prompt": PROMPT_CANDIDATS.format(extrait=fenetre),
            "stream": False,
            "options": {
                "temperature": 0.1,
                "num_predict": SEGMENTATION_NUM_PREDICT,
                "num_ctx": SEGMENTATION_NUM_CTX,
            },
        }
        data = _appeler_ollama_labo(payload)
        parsed = _extraire_json(data.get("response", ""))
        for c in parsed.get("changements", []) or []:
            if not isinstance(c, dict):
                continue
            citation = str(c.get("citation", "")).strip()
            titre = str(c.get("titre", "")).strip()
            if not citation or not titre:
                continue
            position = _localiser_citation(texte, citation)
            if position is None:
                continue  # citation introuvable dans la source -> jamais devinee
            candidats.append({"position": position, "titre": titre, "citation": citation})

    candidats.sort(key=lambda c: c["position"])
    return _dedupliquer_par_position(candidats)


def consolider_candidats(
    candidats: list[dict], modele: str, cible_max: int = SEGMENTATION_CIBLE_MAX_CHAPITRES
) -> tuple[list[dict], bool]:
    """
    Passe 2 — réduit les candidats bruts aux `cible_max` VRAIS changements
    majeurs ET reformule leurs titres génériques (ex. « Changement de sujet :
    de X à Y ») en titres spécifiques au contenu réel de la citation.

    Toujours appelée dès qu'il y a au moins un candidat — même sous
    `cible_max` (rien à trier), un titre issu de la passe 1 peut rester
    générique, faute d'avoir vu le document entier au moment où il a été
    proposé. Retourne (chapitres, repli) : `repli` est True si le modèle n'a
    rien renvoyé d'exploitable et qu'on est retombé sur un simple troncage
    chronologique AVEC LES TITRES D'ORIGINE (jamais un plantage, mais jamais
    fait passer ce repli pour une vraie consolidation — l'appelant doit
    pouvoir le signaler).
    """
    if not candidats:
        return [], False

    lignes = [
        f'{i}. "{c["titre"]}" (extrait : "{c["citation"][:100]}")'
        for i, c in enumerate(candidats)
    ]
    payload = {
        "model": modele,
        "prompt": PROMPT_CONSOLIDATION.format(liste="\n".join(lignes), cible_max=cible_max),
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 600, "num_ctx": SEGMENTATION_NUM_CTX},
    }
    data = _appeler_ollama_labo(payload)
    parsed = _extraire_json(data.get("response", ""))
    entrees = parsed.get("chapitres_retenus")
    if not isinstance(entrees, list) or not entrees:
        return candidats[:cible_max], True

    retenus = []
    for entree in entrees:
        if not isinstance(entree, dict):
            continue
        idx = entree.get("index")
        if not isinstance(idx, int) or not (0 <= idx < len(candidats)):
            continue
        candidat = candidats[idx]
        titre = str(entree.get("titre") or "").strip()
        retenus.append({
            "position": candidat["position"],
            # Jamais un titre vide : repli sur l'original si le modèle a omis
            # ou vidé le champ pour cette entrée.
            "titre": titre or candidat["titre"],
            "citation": candidat["citation"],
        })
    if not retenus:
        return candidats[:cible_max], True

    retenus = retenus[:cible_max]  # garde-fou si le modèle ignore le « au maximum »
    retenus.sort(key=lambda c: c["position"])
    return retenus, False


def segmenter_document(
    chemin: str,
    modele: str,
    extracteur: str,
    cible_max: int = SEGMENTATION_CIBLE_MAX_CHAPITRES,
) -> dict:
    """Orchestration complète : lecture -> candidats -> consolidation. Ne touche
    à aucun fichier ; retourne tout pour affichage/inspection dans le Laboratoire."""
    texte = _lire_texte_source(chemin, extracteur)
    nb_fenetres = len(_decouper_fenetres(texte, SEGMENTATION_FENETRE_CARACTERES, SEGMENTATION_CHEVAUCHEMENT))
    candidats = detecter_candidats(texte, modele)
    chapitres, consolidation_repli = consolider_candidats(candidats, modele, cible_max)
    return {
        "nb_caracteres": len(texte),
        "nb_fenetres": nb_fenetres,
        "nb_candidats_bruts": len(candidats),
        "candidats_bruts": candidats,
        "chapitres": chapitres,
        "consolidation_repli": consolidation_repli,
    }


def inserer_chapitres(texte: str, chapitres: list[dict]) -> str:
    """
    Insère un titre Markdown `# {titre}` à la position de chaque chapitre —
    transforme le document en une source ordinaire, exploitable telle quelle
    par `identifier_chapitres()`/`_extraire_chapitres()`, sans mécanisme
    séparé à maintenir.

    Insertion de la DERNIÈRE position vers la première : insérer en position N
    décale tous les caractères après N, ce qui invaliderait les positions
    suivantes si on procédait dans l'ordre chronologique.

    Si le premier chapitre ne commence pas à la position 0 ET que le texte ne
    porte déjà aucun titre à son tout début, le texte d'avant resterait
    orphelin — invisible pour `_extraire_chapitres()`, qui ignore tout ce qui
    précède le premier titre. Un chapitre de tête générique (« Introduction »)
    couvre ce cas plutôt que de perdre silencieusement le début du document.

    Mais un document extrait par pymupdf4llm porte quasi toujours DÉJÀ un
    titre auto-généré (`# {nom du fichier}`) en tout début de texte — même un
    document par ailleurs « sans structure » (voir le constat de départ de ce
    prototype). Ajouter « Introduction » par-dessus créerait un chapitre vide
    redondant juste avant ce titre existant, qui couvre déjà la position 0.
    """
    valides = [
        c for c in chapitres
        if isinstance(c, dict) and isinstance(c.get("position"), int) and 0 <= c["position"] <= len(texte)
    ]
    if not valides:
        return texte

    a_inserer = sorted(valides, key=lambda c: c["position"])
    debut_a_deja_un_titre = bool(re.match(r"^\s*(<!--.*?-->\s*)?#{1,6}\s", texte, re.DOTALL))
    if a_inserer[0]["position"] > 0 and not debut_a_deja_un_titre:
        a_inserer = [{"position": 0, "titre": "Introduction"}] + a_inserer

    resultat = texte
    for c in sorted(a_inserer, key=lambda c: c["position"], reverse=True):
        pos = c["position"]
        titre = str(c.get("titre") or "").strip() or "Sans titre"
        resultat = resultat[:pos] + f"\n\n# {titre}\n\n" + resultat[pos:]
    return resultat.strip()


def generer_fichier_segmente(chemin: str, extracteur: str, chapitres: list[dict]) -> dict:
    """
    Écrit un `.md` à côté du fichier source, avec les chapitres consolidés
    insérés en titres Markdown — prend un résultat DÉJÀ calculé par
    `segmenter_document()` (pas de nouvel appel Ollama, généré à partir de ce
    que l'utilisateur a déjà sous les yeux dans le Laboratoire).
    """
    texte = _lire_texte_source(chemin, extracteur)
    segmente = inserer_chapitres(texte, chapitres)
    base, _ = os.path.splitext(chemin)
    chemin_sortie = f"{base}_segmente.md"
    with open(chemin_sortie, "w", encoding="utf-8") as f:
        f.write(segmente)
    return {"chemin_sortie": chemin_sortie, "nb_caracteres": len(segmente)}
