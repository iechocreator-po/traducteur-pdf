#!/usr/bin/env python
"""
Script CLI exécuté DANS le venv Python 3.10 dédié (venv_openvoice/, voir
tts_modeles/openvoice/README.md) — jamais importé directement par le backend
FastAPI principal.

Synthétise un texte avec MeloTTS (voix de base, dans la langue demandée) puis
applique le timbre du locuteur cloné (embedding produit par openvoice_extract.py)
via le convertisseur de timbre ("tone color") d'OpenVoice V2. Écrit le WAV.

La langue de synthèse doit suivre la langue du texte lu — le timbre cloné, lui,
est indépendant de la langue (OpenVoice V2 est cross-lingual).

Usage :
    python openvoice_synthesize.py --texte "Bonjour" --embedding embedding.pth \
        --checkpoints tts_modeles/openvoice/checkpoints --sortie extrait.wav \
        --langue français
"""

import argparse
import os
import sys
import tempfile

# Langue applicative → (langue MeloTTS, clé locuteur MeloTTS, fichier source SE)
LANGUES = {
    "français": ("FR", "FR", "fr.pth"),
    "anglais": ("EN", "EN-US", "en-us.pth"),
    "espagnol": ("ES", "ES", "es.pth"),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--texte", required=True)
    parser.add_argument("--embedding", required=True, help="Embedding de locuteur (.pth)")
    parser.add_argument("--checkpoints", required=True, help="Dossier des poids OpenVoice V2")
    parser.add_argument("--sortie", required=True, help="Fichier WAV produit")
    parser.add_argument("--langue", default="français", help="français | anglais | espagnol")
    args = parser.parse_args()

    if args.langue not in LANGUES:
        print(f"Langue non prise en charge : {args.langue}", file=sys.stderr)
        return 1

    if not os.path.exists(args.embedding):
        print(f"Embedding introuvable : {args.embedding}", file=sys.stderr)
        return 1

    try:
        import torch
        from melo.api import TTS as MeloTTS
        from openvoice.api import ToneColorConverter
    except ImportError as e:
        print(f"Dépendance OpenVoice/MeloTTS manquante dans venv_openvoice : {e}", file=sys.stderr)
        return 1

    device = "cuda" if torch.cuda.is_available() else "cpu"

    chemin_converter = os.path.join(args.checkpoints, "converter")
    converter = ToneColorConverter(os.path.join(chemin_converter, "config.json"), device=device)
    converter.load_ckpt(os.path.join(chemin_converter, "checkpoint.pth"))
    voix_cible = torch.load(args.embedding, map_location=device)

    # Voix de base MeloTTS dans la langue du texte ; source_se = embedding du
    # locuteur de base correspondant, fourni par OpenVoice V2.
    langue_melo, cle_locuteur, fichier_se = LANGUES[args.langue]
    base = MeloTTS(language=langue_melo, device=device)
    speaker_id = base.hps.data.spk2id[cle_locuteur]
    chemin_source_se = os.path.join(args.checkpoints, "base_speakers", "ses", fichier_se)
    voix_source = torch.load(chemin_source_se, map_location=device)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        chemin_base = tmp.name
    try:
        base.tts_to_file(args.texte, speaker_id, chemin_base, speed=1.0)
        converter.convert(
            audio_src_path=chemin_base,
            src_se=voix_source,
            tgt_se=voix_cible,
            output_path=args.sortie,
        )
    finally:
        if os.path.exists(chemin_base):
            os.remove(chemin_base)

    return 0


if __name__ == "__main__":
    sys.exit(main())
