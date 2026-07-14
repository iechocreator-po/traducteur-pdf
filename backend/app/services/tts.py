"""
Service Text-to-Speech 100 % local, même philosophie que les extracteurs PDF :
plusieurs moteurs open source détectés dynamiquement, choix par menu déroulant.

- piper     : rapide et léger (CPU). Voix = fichiers .onnx dans tts_modeles/piper/
              (télécharger avec : python -m piper.download_voices fr_FR-siwis-medium)
- kokoro    : qualité supérieure (kokoro-onnx). Modèle + voix dans tts_modeles/kokoro/
              (kokoro-v1.0.onnx et voices-v1.0.bin, dépôt thewh1teagle/kokoro-onnx)
- openvoice : voix clonées par l'utilisateur (capture micro dans le Laboratoire).
              Génère via MeloTTS puis convertit le timbre avec OpenVoice V2,
              dans un sous-processus du venv dédié tts_modeles/openvoice/venv_openvoice/
              (incompatibilité Python 3.13 du venv backend, voir README du dossier).
"""

import glob as _glob
import os
import re
import subprocess
import tempfile
import wave

import numpy as np

from app.services import voix_clonees

DOSSIER_MODELES = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "tts_modeles")
)
DOSSIER_PIPER = os.path.join(DOSSIER_MODELES, "piper")
CHEMIN_KOKORO_MODELE = os.path.join(DOSSIER_MODELES, "kokoro", "kokoro-v1.0.onnx")
CHEMIN_KOKORO_VOIX = os.path.join(DOSSIER_MODELES, "kokoro", "voices-v1.0.bin")

# Longueur max d'un extrait pour l'écoute directe (l'UI, pas les jobs)
EXTRAIT_LONGUEUR_MAX = 1500

_PIPER_VOIX_CHARGEES: dict = {}
_KOKORO_INSTANCE = None


# ── Découverte des moteurs et des voix ───────────────────────────────────────

def _piper_importable() -> bool:
    try:
        import piper  # noqa: F401
        return True
    except ImportError:
        return False


def _kokoro_importable() -> bool:
    try:
        import kokoro_onnx  # noqa: F401
        return True
    except ImportError:
        return False


def lister_voix_piper() -> list[str]:
    """Les voix Piper sont les fichiers .onnx déposés dans tts_modeles/piper/."""
    fichiers = _glob.glob(os.path.join(DOSSIER_PIPER, "*.onnx"))
    return sorted(os.path.splitext(os.path.basename(f))[0] for f in fichiers)


def _kokoro_modele_present() -> bool:
    return os.path.exists(CHEMIN_KOKORO_MODELE) and os.path.exists(CHEMIN_KOKORO_VOIX)


def lister_voix_kokoro() -> list[str]:
    if not _kokoro_modele_present() or not _kokoro_importable():
        return []
    try:
        return sorted(_obtenir_kokoro().get_voices())
    except Exception:
        return []


def _openvoice_disponible() -> bool:
    """Le venv dédié + les checkpoints OpenVoice V2 sont installés."""
    from app.services.voix_clonage_runner import venv_openvoice_disponible
    chemin_checkpoints = os.path.join(voix_clonees.DOSSIER_OPENVOICE, "checkpoints")
    return venv_openvoice_disponible() and os.path.isdir(chemin_checkpoints) and bool(
        os.listdir(chemin_checkpoints)
    )


def lister_voix_openvoice() -> list[str]:
    """Voix clonées par l'utilisateur dont le traitement est terminé."""
    return sorted(v["nom"] for v in voix_clonees.lister_voix() if v["statut"] == "termine")


def lister_moteurs() -> list[dict]:
    """
    Liste des moteurs TTS avec leur disponibilité, leurs voix et, si le moteur
    n'est pas prêt, l'action à faire pour l'activer.
    """
    voix_piper = lister_voix_piper() if _piper_importable() else []
    voix_kokoro = lister_voix_kokoro()
    voix_openvoice = lister_voix_openvoice()
    openvoice_pret = _openvoice_disponible()

    if not openvoice_pret:
        aide_openvoice = (
            "Moteur non installé — voir backend/tts_modeles/openvoice/README.md"
        )
    elif not voix_openvoice:
        aide_openvoice = "Aucune voix clonée pour l'instant — crée-en une dans le Laboratoire."
    else:
        aide_openvoice = None

    return [
        {
            "id": "piper",
            "nom": "Piper (rapide)",
            "disponible": _piper_importable() and bool(voix_piper),
            "voix": voix_piper,
            "aide": None if voix_piper else (
                "Aucune voix installée — depuis backend/tts_modeles/piper/ : "
                "../../venv/bin/python -m piper.download_voices fr_FR-siwis-medium"
            ),
        },
        {
            "id": "kokoro",
            "nom": "Kokoro (qualité)",
            "disponible": _kokoro_importable() and _kokoro_modele_present(),
            "voix": voix_kokoro,
            "aide": None if _kokoro_modele_present() else (
                "Modèle manquant — placer kokoro-v1.0.onnx et voices-v1.0.bin "
                "dans backend/tts_modeles/kokoro/ (dépôt GitHub thewh1teagle/kokoro-onnx)"
            ),
        },
        {
            "id": "openvoice",
            "nom": "Voix clonées (OpenVoice)",
            "disponible": openvoice_pret and bool(voix_openvoice),
            "voix": voix_openvoice,
            "aide": aide_openvoice,
        },
    ]


# ── Préparation du texte ─────────────────────────────────────────────────────

def nettoyer_markdown_pour_lecture(md: str) -> str:
    """
    Convertit du Markdown en texte lisible à voix haute :
    retire les commentaires HTML, blocs de code, symboles de mise en forme,
    et garde le texte des liens sans les URLs.
    """
    texte = re.sub(r"<!--.*?-->", " ", md, flags=re.DOTALL)
    texte = re.sub(r"```.*?```", " ", texte, flags=re.DOTALL)
    texte = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", texte)          # images
    texte = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", texte)        # liens → texte
    texte = re.sub(r"<[^>]+>", " ", texte)                        # balises HTML
    texte = re.sub(r"^#{1,6}\s*", "", texte, flags=re.MULTILINE)  # titres
    texte = re.sub(r"[*_`|>]+", " ", texte)                       # emphase, tableaux
    texte = re.sub(r"^\s*[-•]\s+", "", texte, flags=re.MULTILINE)  # puces
    texte = re.sub(r"[ \t]+", " ", texte)
    texte = re.sub(r"\n{3,}", "\n\n", texte)
    return texte.strip()


# ── Synthèse ─────────────────────────────────────────────────────────────────

def _obtenir_voix_piper(voix: str):
    """Charge une voix Piper une seule fois (les .onnx sont longs à ouvrir)."""
    if voix not in _PIPER_VOIX_CHARGEES:
        from piper import PiperVoice
        chemin = os.path.join(DOSSIER_PIPER, f"{voix}.onnx")
        if not os.path.exists(chemin):
            raise ValueError(f"Voix Piper introuvable : {voix}")
        _PIPER_VOIX_CHARGEES[voix] = PiperVoice.load(chemin)
    return _PIPER_VOIX_CHARGEES[voix]


def _obtenir_kokoro():
    global _KOKORO_INSTANCE
    if _KOKORO_INSTANCE is None:
        from kokoro_onnx import Kokoro
        _KOKORO_INSTANCE = Kokoro(CHEMIN_KOKORO_MODELE, CHEMIN_KOKORO_VOIX)
    return _KOKORO_INSTANCE


def _langue_kokoro(voix: str) -> str:
    """Déduit la langue eSpeak de la convention de nommage des voix Kokoro."""
    prefixes = {
        "a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr",
        "h": "hi", "i": "it", "j": "ja", "p": "pt-br", "z": "cmn",
    }
    return prefixes.get(voix[:1], "en-us")


def _synthetiser_openvoice(texte: str, voix: str, langue: str) -> tuple[np.ndarray, int]:
    """
    Synthétise avec une voix clonée : sous-processus dans le venv dédié
    (MeloTTS pour la parole de base dans la langue du texte + conversion de
    timbre OpenVoice V2). Le timbre cloné est indépendant de la langue.
    """
    entree = next(
        (v for v in voix_clonees.lister_voix() if v["nom"] == voix and v["statut"] == "termine"),
        None,
    )
    if entree is None:
        raise ValueError(f"Voix clonée introuvable ou pas encore prête : '{voix}'")

    from app.services.voix_clonage_runner import CHEMIN_VENV_PYTHON, DOSSIER_CHECKPOINTS

    script = os.path.join(os.path.dirname(__file__), "openvoice_synthesize.py")
    chemin_tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
    try:
        resultat = subprocess.run(
            [
                CHEMIN_VENV_PYTHON, script,
                "--texte", texte,
                "--embedding", entree["chemin_embedding"],
                "--checkpoints", DOSSIER_CHECKPOINTS,
                "--sortie", chemin_tmp,
                "--langue", langue,
            ],
            capture_output=True, text=True, timeout=300,
        )
        if resultat.returncode != 0:
            raise RuntimeError(
                resultat.stderr.strip()[-2000:] or "Échec de la synthèse OpenVoice."
            )
        with wave.open(chemin_tmp, "rb") as wav:
            frequence = wav.getframerate()
            echantillons = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
        return echantillons, frequence
    finally:
        if os.path.exists(chemin_tmp):
            os.remove(chemin_tmp)


def synthetiser(texte: str, moteur: str, voix: str, langue: str = "français") -> tuple[np.ndarray, int]:
    """
    Synthétise un texte et retourne (échantillons int16, fréquence Hz).
    Le texte doit déjà être nettoyé (voir nettoyer_markdown_pour_lecture).
    `langue` n'est utilisée que par le moteur openvoice (MeloTTS) ; Piper et
    Kokoro déduisent la langue de la voix choisie.
    """
    if moteur == "openvoice":
        return _synthetiser_openvoice(texte, voix, langue)

    if moteur == "piper":
        voix_piper = _obtenir_voix_piper(voix)
        morceaux = []
        frequence = None
        for chunk in voix_piper.synthesize(texte):
            morceaux.append(np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16))
            frequence = chunk.sample_rate
        if not morceaux:
            return np.array([], dtype=np.int16), frequence or 22050
        return np.concatenate(morceaux), frequence

    if moteur == "kokoro":
        echantillons, frequence = _obtenir_kokoro().create(
            texte, voice=voix, lang=_langue_kokoro(voix)
        )
        return (np.clip(echantillons, -1.0, 1.0) * 32767).astype(np.int16), frequence

    raise ValueError(f"Moteur TTS inconnu : '{moteur}'")


def synthetiser_extrait_wav(texte: str, moteur: str, voix: str, langue: str = "français") -> bytes:
    """Synthétise un court extrait et retourne le contenu d'un fichier WAV."""
    import io
    import wave

    texte = nettoyer_markdown_pour_lecture(texte)[:EXTRAIT_LONGUEUR_MAX]
    echantillons, frequence = synthetiser(texte, moteur, voix, langue)
    tampon = io.BytesIO()
    with wave.open(tampon, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(frequence)
        wav.writeframes(echantillons.tobytes())
    return tampon.getvalue()
