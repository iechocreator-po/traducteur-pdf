"""
Réception des documents uploadés depuis le navigateur (bouton « Parcourir » et
glisser-déposer). Un navigateur ne révèle JAMAIS le chemin disque d'un fichier
choisi : il faut donc en recevoir les octets et les écrire dans un dossier de
travail serveur, puis réinjecter ce chemin dans le flux existant (analyse,
traduction), qui reste entièrement basé sur des chemins absolus.

Philosophie de stockage calquée sur voix_clonees.py : un sous-dossier par
document, sous backend/uploads/. L'identifiant du dossier est le HASH DU CONTENU
(pas un UUID) : ré-uploader le même livre retombe sur le même dossier, donc le
cache de traduction et l'état de reprise sont réutilisés au lieu d'être perdus.
"""

import hashlib
import os
import re
import shutil
import time
import unicodedata

from app.config.settings import TAILLE_MAX_UPLOAD_OCTETS, UPLOADS_RETENTION_JOURS

DOSSIER_UPLOADS = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "uploads")
)
DOSSIER_TMP = os.path.join(DOSSIER_UPLOADS, ".tmp")

# Longueur max du *stem* (nom sans extension), en octets — APFS limite un nom de
# fichier à 255 octets, et un accent UTF-8 en pèse 2.
STEM_MAX_OCTETS = 200


class UploadInvalide(Exception):
    """Le contenu reçu n'est ni un PDF ni un Markdown UTF-8 valide (ou est vide/trop gros)."""


class UploadTropVolumineux(UploadInvalide):
    """Le fichier dépasse TAILLE_MAX_UPLOAD_OCTETS."""


def assainir_nom(filename: str | None) -> str:
    """
    Réduit un nom de fichier client à un nom sûr ET lisible, à poser dans un
    dossier serveur. « Mon Résumé Final.pdf » reste « Mon Résumé Final.pdf » ;
    « ../../../etc/passwd » devient « passwd ». L'extension finale est décidée
    ailleurs (par le contenu détecté), pas ici.
    """
    nom = filename or ""
    # basename POSIX puis re-split sur « \ » : un client Windows envoie « ..\x.pdf »
    # que basename ne découpe pas.
    nom = os.path.basename(nom).replace("\\", "/")
    nom = nom.rsplit("/", 1)[-1]
    # Retire NUL et caractères de contrôle (open() refuse un NUL ; \n est toxique).
    nom = "".join(c for c in nom if unicodedata.category(c)[0] != "C")
    # Whitelist Unicode : \w garde les lettres accentuées, on autorise espace - . ( ).
    nom = re.sub(r"[^\w \-.()]", "_", nom)
    # Points répétés et points de tête (« .. », « .hidden ») neutralisés.
    nom = re.sub(r"\.{2,}", ".", nom).lstrip(".")
    nom = re.sub(r"\s+", " ", nom).strip()

    stem, ext = os.path.splitext(nom)
    stem = stem.strip() or "document"
    # Troncature en OCTETS (pas en caractères) pour respecter la limite APFS.
    stem_octets = stem.encode("utf-8")[:STEM_MAX_OCTETS]
    stem = stem_octets.decode("utf-8", "ignore").strip() or "document"
    return stem + ext


def detecter_type(debut: bytes, contenu_complet: bytes) -> str | None:
    """
    Détermine le type par le CONTENU, jamais par l'extension client.
    Retourne « pdf », « md », ou None si ce n'est ni l'un ni l'autre.
    """
    if debut[:5] == b"%PDF-":
        return "pdf"
    try:
        contenu_complet.decode("utf-8")
        return "md"
    except UnicodeDecodeError:
        return None


def _dossier_pour_hash(sha: str) -> str:
    return os.path.join(DOSSIER_UPLOADS, sha[:16])


def enregistrer_flux(lire_morceau, nom_client: str | None) -> dict:
    """
    Écrit un flux uploadé morceau par morceau (jamais tout en RAM), en calculant
    le hash de contenu au passage, puis le range dans uploads/<hash>/<nom>.

    `lire_morceau` : callable async-agnostic déjà résolu côté route en un
    itérateur de bytes (voir routes.py). Ici on reçoit une fonction qui rend le
    prochain bloc ou b"" à la fin.

    Retourne {chemin, nom, type, taille_octets, deja_present}.
    Lève UploadInvalide / UploadTropVolumineux, en nettoyant toujours le .part.
    """
    os.makedirs(DOSSIER_TMP, exist_ok=True)
    chemin_part = os.path.join(DOSSIER_TMP, f"{os.urandom(8).hex()}.part")

    sha = hashlib.sha256()
    taille = 0
    debut = b""

    try:
        with open(chemin_part, "wb") as f:
            while True:
                bloc = lire_morceau()
                if not bloc:
                    break
                taille += len(bloc)
                if taille > TAILLE_MAX_UPLOAD_OCTETS:
                    raise UploadTropVolumineux(
                        f"Fichier trop volumineux (max {TAILLE_MAX_UPLOAD_OCTETS // (1024 * 1024)} Mo)."
                    )
                if not debut:
                    debut = bloc[:5]
                sha.update(bloc)
                f.write(bloc)

        if taille == 0:
            raise UploadInvalide("Fichier vide.")

        # Validation par contenu. Pour un MD on relit le fichier écrit (l'UTF-8
        # peut être invalide n'importe où, pas seulement au début).
        with open(chemin_part, "rb") as f:
            contenu = f.read()
        type_fichier = detecter_type(debut, contenu)
        if type_fichier is None:
            raise UploadInvalide("Seuls les fichiers .pdf et .md (texte UTF-8) sont acceptés.")

        nom = assainir_nom(nom_client)
        # L'extension suit le contenu détecté (un .md contenant un PDF → .pdf).
        stem = os.path.splitext(nom)[0]
        nom = f"{stem}.{'pdf' if type_fichier == 'pdf' else 'md'}"

        dossier = _dossier_pour_hash(sha.hexdigest())
        chemin_final = os.path.join(dossier, nom)
        deja_present = os.path.exists(chemin_final)
        os.makedirs(dossier, exist_ok=True)

        # os.replace atomique (même système de fichiers que .tmp).
        os.replace(chemin_part, chemin_final)

        # Filet anti-évasion : le chemin final DOIT être un descendant de uploads/.
        # Après assainir_nom c'est garanti ; ce test attrape un bug de régression.
        if os.path.commonpath([os.path.realpath(chemin_final), os.path.realpath(DOSSIER_UPLOADS)]) != \
                os.path.realpath(DOSSIER_UPLOADS):
            os.remove(chemin_final)
            raise UploadInvalide("Nom de fichier invalide.")

        # Validation PDF forte : l'en-tête %PDF- se falsifie en 5 octets, pas un
        # fichier que l'extracteur sait réellement ouvrir.
        if type_fichier == "pdf":
            try:
                from app.services.pdf_extractor import compter_pages
                compter_pages(chemin_final)
            except Exception:
                shutil.rmtree(dossier, ignore_errors=True)
                raise UploadInvalide("PDF illisible ou corrompu.")

        return {
            "chemin": chemin_final,
            "nom": nom,
            "type": "PDF" if type_fichier == "pdf" else "MD",
            "taille_octets": taille,
            "deja_present": deja_present,
        }
    finally:
        if os.path.exists(chemin_part):
            os.remove(chemin_part)


def _dossier_a_du_travail(dossier: str) -> bool:
    """Vrai si le dossier contient une traduction/conversion (donc à conserver)."""
    for nom in os.listdir(dossier):
        if re.search(r"_traduit.*\.md$", nom) or nom.endswith(".state.json") \
                or re.search(r"_converti.*\.md$", nom):
            return True
    return False


def purger_uploads_anciens(references: set[str] | None = None) -> int:
    """
    Purge conservatrice, appelée au démarrage. Supprime un dossier d'upload
    seulement s'il est ABANDONNÉ, c.-à-d. les trois conditions réunies :
      1. son fichier le plus récent est plus vieux que UPLOADS_RETENTION_JOURS ;
      2. il ne contient aucune traduction/conversion produite ;
      3. sa source n'est référencée par aucune entrée du registre Bibliothèque.
    Les résidus .tmp/*.part (crash en cours d'upload) sont supprimés sans condition.
    Retourne le nombre de dossiers purgés.
    """
    if not os.path.isdir(DOSSIER_UPLOADS):
        return 0

    # 1. Résidus temporaires, toujours supprimés.
    if os.path.isdir(DOSSIER_TMP):
        for nom in os.listdir(DOSSIER_TMP):
            try:
                os.remove(os.path.join(DOSSIER_TMP, nom))
            except OSError:
                pass

    if references is None:
        try:
            from app.services.bibliotheque import lister_documents
            references = {d["chemin_source"] for d in lister_documents()}
        except Exception:
            references = set()

    seuil = time.time() - UPLOADS_RETENTION_JOURS * 86400
    purges = 0
    for nom in os.listdir(DOSSIER_UPLOADS):
        dossier = os.path.join(DOSSIER_UPLOADS, nom)
        if nom == ".tmp" or not os.path.isdir(dossier):
            continue
        try:
            fichiers = [os.path.join(dossier, f) for f in os.listdir(dossier)]
            if not fichiers:
                mtime_max = os.path.getmtime(dossier)
            else:
                mtime_max = max(os.path.getmtime(f) for f in fichiers)
            reference = any(os.path.dirname(r) == os.path.realpath(dossier)
                            or os.path.dirname(os.path.realpath(r)) == os.path.realpath(dossier)
                            for r in references)
            if mtime_max < seuil and not _dossier_a_du_travail(dossier) and not reference:
                shutil.rmtree(dossier, ignore_errors=True)
                purges += 1
        except OSError:
            continue
    return purges
