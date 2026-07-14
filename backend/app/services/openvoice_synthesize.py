#!/usr/bin/env python
"""
Script CLI exécuté DANS le venv Python 3.10 dédié (venv_openvoice/, voir
tts_modeles/openvoice/README.md) — jamais importé directement par le backend
FastAPI principal.

Synthétise un texte en français avec MeloTTS (voix de base) puis applique le
timbre du locuteur cloné (embedding produit par openvoice_extract.py) via le
convertisseur de timbre ("tone color") d'OpenVoice V2. Écrit le résultat en WAV.

Usage :
    python openvoice_synthesize.py --texte "Bonjour" --embedding embedding.pth \
        --checkpoints tts_modeles/openvoice/checkpoints --sortie extrait.wav
"""

import argparse
import os
import sys
import tempfile


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--texte", required=True)
    parser.add_argument("--embedding", required=True, help="Embedding de locuteur (.pth)")
    parser.add_argument("--checkpoints", required=True, help="Dossier des poids OpenVoice V2")
    parser.add_argument("--sortie", required=True, help="Fichier WAV produit")
    args = parser.parse_args()

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

    # MeloTTS FR : voix de base multilocuteur, source_se = embedding par défaut du speaker FR
    base = MeloTTS(language="FR", device=device)
    speaker_id = base.hps.data.spk2id["FR"]
    chemin_source_se = os.path.join(args.checkpoints, "base_speakers", "fr_se.pth")
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
