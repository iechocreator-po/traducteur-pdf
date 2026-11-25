"""
Service de traduction via l'API Ollama locale.
Logique pure d'appel au modèle — pas de gestion d'état ni d'UI ici.
"""

import random
import re
import time
from typing import Callable

import requests

from app.services import metriques
from app.config.settings import (
    OLLAMA_TIMEOUT,
    OLLAMA_NUM_CTX,
    OLLAMA_NUM_PREDICT_MAX,
    OLLAMA_RETRY_DELAI_INITIAL,
    OLLAMA_RETRY_FACTEUR,
    OLLAMA_RETRY_DELAI_MAX,
    OLLAMA_RETRY_BUDGET_SECONDES,
    OLLAMA_RETRY_JITTER,
)

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"


class OllamaIndisponible(Exception):
    """
    Ollama est injoignable et le budget de retry est épuisé.
    FATALE pour le job : l'appelant doit arrêter proprement et laisser la reprise
    repartir de la section perdue, surtout pas continuer avec des placeholders.
    """


class OllamaErreurApplicative(Exception):
    """
    Ollama répond mais refuse la requête (4xx : modèle inconnu, requête invalide).
    Attendre n'y changera rien → aucun retry, et l'erreur reste locale à la section.
    """


class AppelInterrompu(Exception):
    """L'appelant a demandé pause/annulation pendant l'attente entre deux tentatives."""


def _attendre(delai: float, interruption: Callable[[], bool] | None) -> None:
    """
    Dort `delai` secondes en surveillant l'interruption. Le sommeil est découpé
    car pause et annulation ne sont testées qu'entre les chunks par l'appelant :
    sans ça, un clic sur Pause resterait invisible pendant tout le backoff.
    """
    echeance = time.monotonic() + delai
    while time.monotonic() < echeance:
        if interruption and interruption():
            raise AppelInterrompu
        time.sleep(min(0.5, echeance - time.monotonic()))


def appeler_ollama(
    payload: dict,
    timeout: int = OLLAMA_TIMEOUT,
    interruption: Callable[[], bool] | None = None,
) -> dict:
    """
    POST vers Ollama avec retry exponentiel sur les pannes réseau.

    Distingue deux familles d'erreurs :
    - réseau / 5xx (Ollama absent, tué en plein vol, en train de charger un
      modèle) → réessayable : c'est transitoire par nature ;
    - 4xx → définitive : la requête elle-même est invalide.
    """
    delai = OLLAMA_RETRY_DELAI_INITIAL
    debut = time.monotonic()
    derniere: Exception | None = None

    while True:
        if interruption and interruption():
            raise AppelInterrompu
        try:
            reponse = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
            reponse.raise_for_status()
            data = reponse.json()
            if not data.get("response") and data.get("done") is not True:
                # HTTP 200 vide, sans « done » ou avec « done: false » : runner en vrac (mesuré le 17/11/2026, machine chargée ; une
                # phrase triviale rechargeait ensuite le modèle). Pris pour une traduction, ce vide passait pour un
                # morceau « possiblement résumé » : 70 morceaux sur 126 écrits presque vides, job « termine ».
                raise ValueError("réponse Ollama vide et incomplète (ni texte ni « done: true »)")
            metriques.enregistrer_reponse(data)   # jetons et durées réels, pour l'historique (stats.py)
            return data
        except requests.HTTPError as e:
            code = getattr(e.response, "status_code", None)
            if code is not None and 400 <= code < 500:
                raise OllamaErreurApplicative(
                    f"Ollama a refusé la requête (HTTP {code})"
                ) from e
            derniere = e  # 5xx : Ollama est vivant mais en vrac → réessayable
        except (requests.RequestException, ValueError) as e:
            # ValueError couvre le JSONDecodeError d'une réponse tronquée.
            derniere = e

        ecoule = time.monotonic() - debut
        attente = delai * (1 + random.uniform(-OLLAMA_RETRY_JITTER, OLLAMA_RETRY_JITTER))
        # Budget vérifié AVANT de dormir, contre l'attente RÉELLE (jitter inclus) :
        # ainsi le temps total passé en attente ne dépasse jamais le budget.
        if ecoule + attente > OLLAMA_RETRY_BUDGET_SECONDES:
            raise OllamaIndisponible(
                f"Ollama injoignable depuis {ecoule:.0f}s (budget "
                f"{OLLAMA_RETRY_BUDGET_SECONDES}s épuisé) — dernière erreur : {derniere}"
            )
        _attendre(attente, interruption)
        delai = min(delai * OLLAMA_RETRY_FACTEUR, OLLAMA_RETRY_DELAI_MAX)


# Un tag image Markdown : ![alt](chemin). Le chemin est un nom de fichier qu'on
# ne veut SURTOUT pas envoyer au modèle (gaspillage de tokens + risque qu'il
# traduise/altère le chemin). On le masque par une sentinelle avant l'appel.
_RE_TAG_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
# Texte marqué « à ne pas traduire » (Documents complexes, zone dessinée à la main ; voir
# scan_colonnes.MARQUE_CONSERVER) : masqué de la même façon, donc recopié à l'identique, marques comprises.
# Pictogramme du scan (« ⟦icône:page.k⟧ ») : son numéro est l'adresse de l'image découpée ; laissé au modèle,
# il pouvait être traduit, renuméroté ou supprimé, et la reconstruction n'aurait plus su quelle image recoller.
# Numéro d'entrée seul sur sa ligne (« **0001** », scan_colonnes) : laissé au modèle, il perdait ses étoiles ou
# disparaissait (9 sur 106 sur le Cycle I, 17/11/2026) ; masqué, il revient intact, à sa place.
# Début d'encadré (« > ») : laissé au modèle, il le retirait (19 encadrés sur 330, Cycle I) ; masqué, il revient en
# tête de son paragraphe.
# Rangée de tableau de tirage (scan_colonnes._texte_tirage) : « | 1–2 | Mazed Forest | ☐ | ». Seul le libellé est
# traduit ; la clé et la case sont des sentinelles, comme le « > » d'un encadré.
_TIRAGE = r"(?m:^\| [^|\n]{1,5} \| )|(?m: \|(?: ☐ \|)?$)"
_RE_PROTEGE = re.compile(
    r"!\[[^\]]*\]\([^)]*\)|<!-- conserver -->\n.*?\n<!-- /conserver -->|⟦icône(?::\d+\.\d+)?⟧"
    r"|(?m:^\*\*[\d?]{4}\*\*$)|(?m:^> )|" + _TIRAGE, re.S)
_RE_ENTREE_SEULE = re.compile(r"(?m)^(\*\*[\d?]{4}\*\*)[ \t]*$")
# Ce qui doit se retrouver tel quel dans toute traduction (voir marqueurs_perdus).
_RE_MARQUEUR_OBLIGATOIRE = re.compile(r"⟦icône(?::\d+\.\d+)?⟧|(?m:^\*\*[\d?]{4}\*\*$)|(?m:^> )|" + _TIRAGE)


_RE_GRAS_SEUL = re.compile(r"^\*\*(.+)\*\*$", re.S)


def restaurer_structure(source: str, traduit: str) -> str:
    """
    Remet la structure Markdown que le modèle a perdue, paragraphe par paragraphe, quand source et traduction ont le
    même nombre de paragraphes : « > » d'un encadré (« If Labyrinthians… see 0015 » rendu en texte courant, Cycle I
    p. 3, 17/11/2026) et gras d'un intertitre. Nombre différent : rien n'est touché (on ne devine pas l'alignement).
    """
    # un numéro d'entrée suivi d'autre chose sur sa ligne (« **0007** ⟦icône:3.3⟧ ») : remis seul sur sa ligne
    traduit = re.sub(r"(?m)^(\*\*[\d?]{4}\*\*)[ \t]+(?=\S)", r"\1\n\n", traduit)
    # un numéro d'entrée n'est jamais dans un encadré (le « > » masqué s'y recolle quand le modèle les rapproche)
    traduit = re.sub(r"(?m)^>[ \t]*(?:\n\s*)?(\*\*[\d?]{4}\*\*)$", r"\1", traduit)
    p_src = [p for p in re.split(r"\n\s*\n", source.strip()) if p.strip()]
    p_tr = [p for p in re.split(r"\n\s*\n", traduit.strip()) if p.strip()]
    if len(p_src) != len(p_tr):
        return traduit
    sortie, change = [], False
    for ps, pt in zip(p_src, p_tr):
        ps, pt = ps.strip(), pt.strip()
        if ps.startswith(">") and not pt.startswith(">"):
            pt = "\n".join("> " + ligne for ligne in pt.splitlines())
            change = True
        elif (_RE_GRAS_SEUL.match(ps) and not _RE_GRAS_SEUL.match(pt)
              and not re.fullmatch(r"\*\*[\d?]{4}\*\*", ps) and len(pt) <= 2 * len(ps) + 20):
            # intertitre seulement, et de longueur comparable : un paragraphe entier mis en gras par un alignement
            # décalé d'un cran (texte de l'entrée 0037, Cycle I) est pire que pas de gras
            pt = f"**{pt.strip('* ')}**"
            change = True
        sortie.append(pt)
    return "\n\n".join(sortie) if change else traduit


def _suites_des_entrees(texte: str) -> list[tuple[str, bool]]:
    """[(numéro, suivi de texte ?)] dans l'ordre : un numéro d'entrée est-il suivi d'un paragraphe qui n'est pas un
    autre numéro ?"""
    paras = [p.strip() for p in re.split(r"\n\s*\n", texte.strip()) if p.strip()]
    est_num = [bool(re.fullmatch(r"\*\*[\d?]{4}\*\*", p)) for p in paras]
    return [(p, i + 1 < len(paras) and not est_num[i + 1]) for i, p in enumerate(paras) if est_num[i]]


def entrees_deplacees(source: str, traduit: str) -> str | None:
    """
    Numéro d'entrée que le modèle a éloigné de son texte (Cycle I, 17/11/2026 : la sentinelle de « 0037 » posée en fin
    de morceau, juste avant « 0038 » ; le texte de l'entrée restait sous la précédente). Retourne la description du
    défaut, ou None.
    """
    attendu = _suites_des_entrees(source)
    # comptés ligne par ligne : un numéro collé sous une autre ligne du même paragraphe compte aussi
    if _RE_ENTREE_SEULE.findall(source) != _RE_ENTREE_SEULE.findall(traduit):
        return "numéros d'entrée dans un autre ordre (ou en double) que dans la source"
    obtenu = dict(_suites_des_entrees(traduit))
    for num, suivi in attendu:
        if suivi and not obtenu.get(num, False):
            return f"numéro d'entrée {num.strip('*')} séparé de son texte"
    return None


def gras_en_trop(source: str, traduit: str) -> bool:
    """Plus de paragraphes tout en gras dans la traduction que dans la source : un texte courant mis en gras (ancienne
    restauration de structure sur un alignement décalé, gardée en cache)."""
    def nb(t: str) -> int:      # ligne par ligne : « **END** » ajouté sous un intertitre, sans ligne vide (Cycle I)
        return len(re.findall(r"(?m)^\*\*[^*\n]+\*\*[ \t]*$", t))
    return nb(traduit) > nb(source)


def marqueurs_perdus(source: str, traduit: str) -> list[str]:
    """Pictogrammes et numéros d'entrée de la source absents de la traduction (une traduction en cache faite avant
    leur masquage peut les avoir perdus)."""
    restant = traduit
    perdus = []
    for m in _RE_MARQUEUR_OBLIGATOIRE.findall(source):
        # un numéro d'entrée doit rester seul sur sa ligne (collé à une phrase, il n'est plus un titre)
        motif = (rf"(?m)^{re.escape(m)}$" if m.startswith("**") else r"(?m)^> " if m == "> " else re.escape(m))
        if re.search(motif, restant):
            restant = re.sub(motif, "", restant, count=1)
        else:
            perdus.append(m)
    return perdus


def _masquer_images(texte: str) -> tuple[str, list[str]]:
    """
    Remplace chaque tag image (et chaque texte « à ne pas traduire ») par une sentinelle neutre
    `⟦IMGk⟧`. Ainsi Ollama ne reçoit jamais le chemin de l'image (ni l'alt) : il ne peut ni le
    traduire ni l'altérer. Retourne (texte_masqué, liste des tags dans l'ordre d'apparition).
    """
    tags: list[str] = []

    def _sub(m: re.Match) -> str:
        tags.append(m.group(0))
        return f"⟦IMG{len(tags) - 1}⟧"

    return _RE_PROTEGE.sub(_sub, texte), tags


def _restaurer_images(traduit: str, tags: list[str]) -> str:
    """
    Remet chaque tag image d'origine à la place de sa sentinelle. Filet de
    sécurité : si le modèle a malgré tout perdu/altéré une sentinelle, le tag
    correspondant est réinséré en fin de texte — on ne perd JAMAIS une image.
    """
    for k, tag in enumerate(tags):
        sentinelle = f"⟦IMG{k}⟧"
        if tag == "> ":     # début d'encadré : en tête de paragraphe ; perdu, restaurer_structure tentera l'alignement
            if sentinelle in traduit:
                traduit = re.sub(rf"\s*{re.escape(sentinelle)}\s*", "\n\n> ", traduit).lstrip("\n")
            continue
        if sentinelle in traduit and tag.startswith("| "):   # début de rangée de tirage : en tête de ligne
            traduit = re.sub(rf"[ \t]*\n?[ \t]*{re.escape(sentinelle)}[ \t]*", lambda _: "\n" + tag,
                             traduit).lstrip("\n")
            continue
        if sentinelle in traduit and tag.startswith(" |"):   # fin de rangée : collée au libellé, puis fin de ligne
            traduit = re.sub(rf"[ \t]*{re.escape(sentinelle)}[ \t]*\n?", lambda _: tag + "\n", traduit).rstrip("\n")
            continue
        if sentinelle in traduit and re.fullmatch(r"\*\*[\d?]{4}\*\*", tag):
            # numéro d'entrée : toujours seul sur sa ligne, même si le modèle a collé la sentinelle à une phrase
            traduit = re.sub(rf"[ \t]*{re.escape(sentinelle)}[ \t]*", f"\n\n{tag}\n\n", traduit)
            traduit = re.sub(r"\n{3,}", "\n\n", traduit).strip("\n")
        elif sentinelle in traduit:
            traduit = traduit.replace(sentinelle, tag)
        elif tag.startswith("⟦icône"):     # un pictogramme perdu reste dans son paragraphe
            traduit = traduit.rstrip() + " " + tag
        else:
            traduit = traduit.rstrip() + "\n\n" + tag
    return traduit


_RE_TABLEAU = re.compile(r"((?:^\| [^\n]*\|[ \t]*(?:\n|$))+)", re.M)
# Une rangée de tirage exactement (scan_colonnes._texte_tirage) : clé courte, un seul libellé qui porte du texte, case
# facultative. Un tableau Markdown ordinaire n'en est pas un : sa ligne « | --- | --- | » n'a pas de libellé, et ses
# rangées de plus de deux colonnes ont un « | » dans le libellé. Traité rangée par rangée, il perdait ses colonnes.
_RE_RANGEE_TIRAGE = re.compile(r"^\| [^|\n]{1,5} \| (?=[^|\n]*\w)[^|\n]*? \|(?: ☐ \|)?[ \t]*$")


def _est_tirage(bloc: str) -> bool:
    lignes = [ligne for ligne in bloc.splitlines() if ligne.strip()]
    return bool(lignes) and all(_RE_RANGEE_TIRAGE.match(ligne) for ligne in lignes)


def _decouper_tirages(texte: str) -> list[tuple[bool, str]]:
    """[(est_un_tirage, morceau)] dans l'ordre ; un tableau qui n'est pas un tirage reste dans le texte voisin."""
    morceaux, debut = [], 0
    for m in _RE_TABLEAU.finditer(texte):
        if _est_tirage(m.group(0)):
            morceaux += [(False, texte[debut:m.start()]), (True, m.group(0))]
            debut = m.end()
    morceaux.append((False, texte[debut:]))
    return morceaux


def _traduire_tableau(tableau: str, modele: str, langue_source: str, langue_cible: str, termes_a_conserver,
                      interruption, temperature: float) -> str:
    """Rangées « | clé | libellé | ☐ | » : seul le libellé part chez le modèle, seul ; clé et case restent."""
    lignes = []
    for ligne in tableau.splitlines():
        m = re.match(r"^(\| [^|]*\| )(.*?)( \|(?: ☐ \|)?)[ \t]*$", ligne)
        if not m or not m.group(2).strip():
            lignes.append(ligne)
            continue
        libelle = traduire_texte(m.group(2).strip(), modele, langue_source, langue_cible,
                                 termes_a_conserver=termes_a_conserver, interruption=interruption,
                                 temperature=temperature)
        lignes.append(m.group(1) + " ".join(libelle.split()).replace("|", "/") + m.group(3))
    return "\n".join(lignes)


def traduire_texte(
    texte: str,
    modele: str,
    langue_source: str,
    langue_cible: str,
    termes_a_conserver: list[str] | None = None,
    interruption: Callable[[], bool] | None = None,
    temperature: float = 0.3,
) -> str:
    """
    Envoie un texte à Ollama et retourne la traduction.
    termes_a_conserver : entrées du glossaire présentes : à recopier telles quelles, ou « source => cible »
      (traduction imposée, voir glossaire.decomposer).
    interruption : callback consulté pendant le backoff (pause/annulation).

    Les tags images `![](...)` sont masqués par une sentinelle avant l'appel (le
    chemin n'est jamais envoyé au modèle) puis restaurés à l'identique.
    """
    # Tableau de tirage (« | 1–2 | Mazed Forest | ☐ | ») : traduit à part, libellé par libellé. Masqué par sentinelles
    # au milieu du texte, il revenait en désordre (qwen2.5, Cycle I section 3 p. 5, 19/11/2026 : libellés décalés d'une
    # rangée, quatre rangées rejetées plus loin sans libellé).
    blocs = _decouper_tirages(texte)
    if len(blocs) > 1:
        sortie = []
        for est_tirage, bloc in blocs:
            if est_tirage:
                sortie.append(_traduire_tableau(bloc.strip("\n"), modele, langue_source, langue_cible,
                                                termes_a_conserver, interruption, temperature))
            elif bloc.strip():
                sortie.append(traduire_texte(bloc.strip(), modele, langue_source, langue_cible,
                                             termes_a_conserver=termes_a_conserver, interruption=interruption,
                                             temperature=temperature))
        return "\n\n".join(sortie)

    # Numéros d'entrée : le morceau est coupé à chacun et chaque partie traduite à part. Masqués (sentinelles), ils
    # étaient parfois déplacés par le modèle loin de leur texte (« 0001 » remonté sous un intertitre de la page
    # précédente, Cycle I, 17/11/2026) ; coupés, ils restent exactement à leur place.
    parties = _RE_ENTREE_SEULE.split(texte)
    if len(parties) > 1:
        sortie = []
        for i, partie in enumerate(parties):
            if i % 2:                                   # un numéro capturé par le split
                sortie.append(partie)
            elif partie.strip():
                sortie.append(traduire_texte(partie.strip(), modele, langue_source, langue_cible,
                                             termes_a_conserver=termes_a_conserver, interruption=interruption,
                                             temperature=temperature))
        return "\n\n".join(sortie)

    texte_a_traduire, tags_images = _masquer_images(texte)
    if tags_images and not re.sub(r"⟦IMG\d+⟧", "", texte_a_traduire).strip():
        return texte    # rien d'autre que des images ou du texte à conserver : le modèle n'a rien à faire

    system = (
        f"You are a professional literal translator. Traduis de {langue_source} vers {langue_cible}.\n"
        f"RÈGLES STRICTES :\n"
        f"1. Traduis INTÉGRALEMENT chaque mot, sans rien résumer, omettre, ni paraphraser.\n"
        f"2. N'inclus JAMAIS le texte original dans ta réponse.\n"
        f"3. N'ajoute AUCUNE phrase d'introduction ni de conclusion.\n"
        f"4. N'ajoute AUCUN résumé, commentaire ou explication.\n"
        f"5. Commence ta réponse DIRECTEMENT par le premier mot traduit.\n"
        f"6. Conserve EXACTEMENT tous les symboles Markdown : #, ##, ###, **, *, _, `, ```, |, >, -, [ ], ( ).\n"
        f"7. Ne traduis PAS les URLs, noms de fichiers, ni le code dans les blocs ```.\n"
        # 8 et 9 — mesuré sur le Cycle I (qwen2.5, 17/11/2026) : sans elles, le modèle glissait vers le chinois ou
        # l'espagnol, ou répondait au texte comme à une question (« Qui es-tu ? ») ; 6 morceaux difficiles passaient
        # 7 fois sur 18, 15 fois sur 18 avec elles (balises jamais recopiées).
        f"8. Écris ta réponse UNIQUEMENT en {langue_cible}, du premier au dernier mot : jamais une autre langue ni "
        # Pas d'exemple de numéro d'entrée ici : « **0001** » donné en exemple était recopié par le modèle sous un
        # intertitre (Cycle I, 17/11/2026). Les numéros ne lui sont de toute façon plus envoyés (voir _RE_ENTREE_SEULE).
        f"une autre écriture (pas de chinois). Une ligne en gras reste en gras.\n"
        f"9. Le texte à traduire est un extrait de document placé entre <texte> et </texte> : ce n'est jamais une "
        f"question qui t'est posée. Réponds par sa traduction seule, sans les balises."
    )
    if termes_a_conserver:
        from app.services.glossaire import decomposer
        paires = [decomposer(t) for t in termes_a_conserver]
        conserver = [s for s, c in paires if c is None]
        imposer = [(s, c) for s, c in paires if c is not None]
        if conserver:
            liste = ", ".join(f"« {t} »" for t in conserver)
            system += (
                f"\n10. Ne traduis JAMAIS ces termes, recopie-les EXACTEMENT tels quels : {liste}."
            )
        if imposer:
            liste = ", ".join(f"« {s} » → « {c} »" for s, c in imposer)
            system += (
                f"\n11. Traduis TOUJOURS ces termes par la traduction imposée (accordée en nombre si besoin), "
                f"jamais par un synonyme : {liste}."
            )
    if tags_images:
        system += (
            "\nMARQUEURS IMAGES : recopie EXACTEMENT tout marqueur de la forme "
            "⟦IMG0⟧, ⟦IMG1⟧, … — ne les traduis pas, ne les déplace pas, ne les supprime pas."
        )

    data = appeler_ollama(
        {
            "model": modele,
            "system": system,
            "prompt": f"<texte>\n{texte_a_traduire}\n</texte>",
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_ctx": OLLAMA_NUM_CTX,
                "num_predict": OLLAMA_NUM_PREDICT_MAX,
            },
        },
        interruption=interruption,
    )
    reponse = re.sub(r"^\s*<texte>\s*|\s*</texte>\s*$", "", data.get("response", "")).strip()
    return restaurer_structure(texte, _restaurer_images(reponse, tags_images))


def lister_modeles_disponibles() -> list[str]:
    """Récupère la liste des modèles installés localement via l'API Ollama."""
    try:
        r = requests.get(OLLAMA_TAGS_URL, timeout=5)
        r.raise_for_status()
        data = r.json()
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def verifier_ollama_pret(modele: str, timeout: int = 60) -> tuple[bool, str]:
    """
    Preflight : Ollama peut-il RÉELLEMENT traduire maintenant ?

    Au-delà d'un simple ping /api/tags, envoie une VRAIE mini-traduction avec les
    params exacts d'un job (num_ctx compris). Un llama-server figé (modèle chargé
    mais bloqué, ~3 % CPU) ne répond pas à ceci alors que /api/tags répondrait
    encore. Appeler ce preflight AVANT de lancer un job évite de figer 10 min sur
    un Ollama en vrac : on échoue proprement (503) avec une consigne claire.

    Ce premier appel réchauffe aussi le modèle (chargement en mémoire) — d'où le
    timeout large qui couvre un démarrage à froid.

    Retour : (pret, message). `message` décrit la cause quand pas prêt.
    """
    payload = {
        "model": modele,
        "system": "Traduis de anglais vers français. Réponds uniquement la traduction.",
        "prompt": "The cat sleeps.",
        "stream": False,
        "options": {
            "temperature": 0.3,
            "num_ctx": OLLAMA_NUM_CTX,
            "num_predict": OLLAMA_NUM_PREDICT_MAX,
        },
    }
    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
        r.raise_for_status()
        if r.json().get("response", "").strip():
            return True, "ok"
        return False, "Ollama a répondu vide au test de traduction — état anormal."
    except requests.Timeout:
        return False, (
            f"Ollama n'a pas répondu en {timeout}s au test de traduction "
            "(llama-server probablement figé). Redémarrer Ollama, puis réessayer : "
            "killall ollama ; open -a Ollama"
        )
    except requests.HTTPError as e:
        code = getattr(e.response, "status_code", "?")
        return False, f"Ollama a refusé le test (HTTP {code}) — modèle « {modele} » installé ?"
    except requests.RequestException as e:
        return False, f"Ollama injoignable ({e}). Est-il lancé ? (open -a Ollama)"
