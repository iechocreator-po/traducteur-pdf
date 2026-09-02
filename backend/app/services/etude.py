"""
Service de génération de fiche d'étude via l'API Ollama locale.
Logique pure d'appel au modèle — pas de gestion d'état ni d'UI ici.

Chaque fonction demande une sortie JSON (format=json d'Ollama), la valide
avec Pydantic et retente une fois si le modèle a produit un JSON invalide
ou incomplet (fréquent avec les petits modèles locaux).
"""

import json

import requests
from pydantic import BaseModel, Field, ValidationError

from app.config.settings import OLLAMA_TIMEOUT
from app.models.schemas import QuestionEtude
from app.services.translator import OLLAMA_URL


class ReponseJsonInvalide(Exception):
    """Levée quand le modèle n'a pas produit le JSON attendu après relance."""


class _ReponsePoints(BaseModel):
    points: list[str] = Field(min_length=1)


class _ReponseQuestions(BaseModel):
    questions: list[QuestionEtude] = Field(min_length=1)


def _appeler_ollama_json(modele: str, system: str, prompt: str) -> str:
    reponse = requests.post(
        OLLAMA_URL,
        json={
            "model": modele,
            "system": system,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.3},
        },
        timeout=OLLAMA_TIMEOUT,
    )
    reponse.raise_for_status()
    return reponse.json().get("response", "").strip()


def _generer_valide(modele: str, system: str, prompt: str, schema: type[BaseModel]) -> BaseModel:
    """Appelle Ollama et valide la réponse ; une seconde tentative si JSON invalide."""
    derniere_erreur: Exception | None = None
    for _ in range(2):
        brut = _appeler_ollama_json(modele, system, prompt)
        try:
            return schema.model_validate(json.loads(brut))
        except (json.JSONDecodeError, ValidationError) as e:
            derniere_erreur = e
    raise ReponseJsonInvalide(f"Réponse JSON invalide du modèle après 2 tentatives : {derniere_erreur}")


def calculer_nb_points(longueur: int) -> int:
    """
    Nombre de points à retenir, proportionné à la longueur du chapitre.

    Le produit demandait 5 points quelle que soit la taille. Cinq points pour un
    chapitre de livre de 50 000 caractères ne peuvent qu'être vagues — c'est la
    première cause du « trop simpliste » signalé le 18/8, avant même la
    formulation des consignes.
    """
    if longueur < 4_000:
        return 3
    if longueur < 12_000:
        return 5
    if longueur < 30_000:
        return 8
    return 12


def calculer_nb_questions(longueur: int) -> int:
    """
    Moins de questions que de points : une question utile coûte plus cher à
    lire qu'une puce, et au-delà de 5 ou 6 on retombe dans le remplissage.
    """
    if longueur < 4_000:
        return 2
    if longueur < 12_000:
        return 3
    if longueur < 30_000:
        return 4
    return 6


def generer_points(texte: str, modele: str, langue: str, nb_points: int) -> list[str]:
    """
    Génère les points à retenir d'un chapitre (liste de phrases complètes).

    Les consignes ont été réécrites le 18/8 après comparaison mesurée sur un même
    chapitre. Trois défauts récurrents de l'ancienne formulation :
    points circulaires (« la structure du cerveau est essentielle pour comprendre
    le fonctionnement du cerveau »), deux points sur le même fait, et absence
    d'ancrage concret.

    ⚠️ La règle 2 dit « une PHRASE COMPLÈTE qui s'appuie sur un élément concret »
    et non « doit CONTENIR un élément concret ». Testé : avec la seconde
    formulation, llama3.1 rend des mots-clés nus — « Santiago Ramón y Cajal »,
    « 1931 », « dendrites » — au lieu de phrases. qwen2.5 comprenait l'intention,
    pas llama3.1. Une consigne ambiguë coûte plus cher qu'un modèle plus faible.
    """
    system = (
        f"Tu es un pédagogue qui prépare une fiche de révision. Tu réponds "
        f"UNIQUEMENT en JSON valide, en {langue}, sans texte hors du JSON.\n"
        f'Format exact attendu : {{"points": ["…", "…"]}}\n'
        f"RÈGLES :\n"
        f"1. Exactement {nb_points} points, du plus important au moins important.\n"
        f"2. Chaque point est une PHRASE COMPLÈTE (sujet, verbe, complément) qui "
        f"s'appuie sur un élément concret du texte : un nom propre, une date, un "
        f"chiffre, un terme technique défini ou un mécanisme précis. Jamais un mot "
        f"seul ni une simple étiquette.\n"
        f"3. INTERDIT : les phrases circulaires ou vides du type « X est important "
        f"pour comprendre X », « le sujet est complexe », « cela aide à mieux "
        f"comprendre ».\n"
        f"4. Deux points ne portent JAMAIS sur le même fait.\n"
        f"5. Varie la nature des points : un fait historique, une définition, un "
        f"mécanisme, une conséquence, une limite ou une critique.\n"
        f"6. N'invente rien : tout doit venir du texte fourni."
    )
    resultat = _generer_valide(modele, system, texte, _ReponsePoints)
    return resultat.points[:nb_points]


def generer_questions(
    texte: str, modele: str, langue: str, nb_questions: int,
    points: list[str] | None = None,
    depuis_les_points: bool = False,
) -> list[QuestionEtude]:
    """
    Génère des questions de compréhension avec leur réponse attendue (corrigé).

    `points` : les points déjà retenus. Les transmettre évite que les questions
    ne les reformulent, et sert de contexte anti-redondance — mesuré le 18/8,
    qwen2.5 produisait sinon deux questions sur le MÊME fait (les ponts de
    Königsberg posés deux fois de suite).

    `depuis_les_points` : le matériau EST la liste des points, pas un texte.
    Utilisé par la stratégie « sections », dont les points sont ancrés dans le
    texte réel du chapitre. Mesuré le 18/8 sur Chapter 9 : les deux stratégies
    produisaient des questions presque identiques — et pour cause, elles
    lisaient toutes deux le MÊME texte condensé. Le gain de « sections » ne les
    atteignait pas. En partant des points, on supprime la condensation du chemin
    de cette stratégie : elle devient à la fois meilleure et plus rapide (elle
    était 13 % plus lente, 454 s contre 400 s, uniquement à cause de cette
    condensation dont elle n'avait besoin que pour les questions).

    Les types sont IMPOSÉS et ordonnés. Sans ça, les trois questions étaient du
    rappel déguisé, toutes introduites par « selon le texte » — alors même que
    l'ancienne consigne demandait « de comprendre, pas seulement de mémoriser ».
    Nommer les types obtient ce que la formulation générale n'obtenait pas.
    """
    types = [
        "MÉCANISME (« comment » / « par quel moyen »)",
        "RAISON ou ENJEU (« pourquoi » / « en quoi c'est important »)",
        "TRANSFERT (appliquer l'idée à un cas non traité dans le texte)",
        "COMPARAISON (opposer deux notions réellement présentes dans le texte)",
        "LIMITE ou CRITIQUE (ce que le texte ne tranche pas, ou nuance)",
        "SYNTHÈSE (relier deux idées éloignées du texte)",
    ]
    consignes_types = "\n".join(
        f"   Q{i + 1} = {types[i % len(types)]}" for i in range(nb_questions)
    )
    if depuis_les_points:
        # Le matériau EST la liste des points. Dire « ne reformule pas les
        # points » ici serait contradictoire : on demande au contraire de les
        # EXPLOITER, en développant ce qu'ils énoncent plutôt qu'en les répétant.
        materiau = (
            "Voici les points clés d'un chapitre, chacun tiré directement de son "
            "texte. Ils constituent la matière de ton quiz.\n\n"
            + "\n".join(f"- {p}" for p in (points or []))
        )
        regle_reponse = (
            "5. La réponse DÉVELOPPE le point concerné en 2 à 4 phrases — elle "
            "l'explique ou en tire les conséquences, elle ne le répète pas mot "
            "pour mot. N'ajoute aucun fait absent des points fournis."
        )
        contexte_points = ""
    else:
        materiau = texte
        regle_reponse = (
            "5. La réponse est complète et se suffit à elle-même, en 2 à 4 "
            "phrases, appuyée uniquement sur le texte fourni."
        )
        contexte_points = (
            "\nLes points déjà retenus (à NE PAS simplement reformuler) :\n"
            + "\n".join(f"- {p}" for p in points)
            if points else ""
        )

    system = (
        f"Tu es un pédagogue qui prépare un quiz de révision. Tu réponds "
        f"UNIQUEMENT en JSON valide, en {langue}, sans texte hors du JSON.\n"
        f'Format exact attendu : {{"questions": [{{"question": "…", "reponse": "…"}}]}}\n'
        f"RÈGLES :\n"
        f"1. Exactement {nb_questions} questions, chacune sur un ASPECT DIFFÉRENT.\n"
        f"2. Types imposés, un par question et dans cet ordre :\n{consignes_types}\n"
        f"3. INTERDIT de commencer une question par « Selon le texte » ou "
        f"« D'après le texte ».\n"
        f"4. INTERDIT : deux questions portant sur le même fait ou le même exemple.\n"
        f"{regle_reponse}"
        f"{contexte_points}"
    )
    resultat = _generer_valide(modele, system, materiau, _ReponseQuestions)
    return resultat.questions[:nb_questions]


def condenser_texte(texte: str, modele: str, langue: str) -> str:
    """
    Condense un morceau de chapitre trop long en notes détaillées.
    Utilisé avant generer_points/generer_questions quand le chapitre dépasse
    le contexte raisonnable du modèle. Sortie en texte libre (pas de JSON).
    """
    system = (
        f"Tu es un assistant d'étude. Rédige en {langue} des notes détaillées et fidèles "
        f"du texte fourni : idées principales, faits, définitions, exemples marquants. "
        f"N'invente rien, n'ajoute aucun commentaire. Réponds directement par les notes."
    )
    reponse = requests.post(
        OLLAMA_URL,
        json={
            "model": modele,
            "system": system,
            "prompt": texte,
            "stream": False,
            "options": {"temperature": 0.3},
        },
        timeout=OLLAMA_TIMEOUT,
    )
    reponse.raise_for_status()
    return reponse.json().get("response", "").strip()

def consolider_points(
    points_par_section: list[list[str]], modele: str, langue: str, nb_points: int
) -> list[str]:
    """
    Fusionne les points de plusieurs sections en une liste finale.

    C'est la seconde moitié de la stratégie « sections ». La première génère les
    points de CHAQUE section depuis le texte réel ; celle-ci les met en commun :
    déduplique, classe, et garde les meilleurs.

    Pourquoi ne pas simplement concaténer et tronquer : deux sections voisines
    produisent souvent le même point sous deux formulations, et l'ordre de
    lecture n'est pas l'ordre d'importance. Un tri naïf donnerait une liste
    redondante commençant par des détails.

    ⚠️ Cette étape voit des POINTS, pas le texte — c'est le seul endroit de la
    stratégie où le modèle travaille sur une paraphrase. La différence avec la
    condensation est que la matière première, elle, vient du texte : ici on
    choisit parmi des points ancrés, là on résumait un résumé.
    """
    tous = [p for section in points_par_section for p in section]
    if not tous:
        return []
    if len(tous) <= nb_points:
        return tous

    system = (
        f"Tu es un pédagogue qui finalise une fiche de révision. Tu réponds "
        f"UNIQUEMENT en JSON valide, en {langue}, sans texte hors du JSON.\n"
        f'Format exact attendu : {{"points": ["…", "…"]}}\n'
        f"On te donne les points relevés section par section dans un même "
        f"chapitre. RÈGLES :\n"
        f"1. Retiens exactement {nb_points} points, du plus important au moins "
        f"important.\n"
        f"2. FUSIONNE les points qui disent la même chose sous deux formulations, "
        f"en gardant la version la plus précise (celle qui porte un nom, une date "
        f"ou un chiffre).\n"
        f"3. REPRENDS les points tels quels ou fusionnés — n'en invente aucun et "
        f"n'ajoute aucune information absente de la liste.\n"
        f"4. Conserve les éléments concrets : noms propres, dates, chiffres, "
        f"termes techniques."
    )
    prompt = "\n".join(f"- {p}" for p in tous)
    resultat = _generer_valide(modele, system, prompt, _ReponsePoints)
    return resultat.points[:nb_points]
