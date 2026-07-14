#!/usr/bin/env python
"""
Script CLI exécuté DANS le venv Python 3.10 dédié (venv_openvoice/, voir
tts_modeles/openvoice/README.md) — jamais importé directement par le backend
FastAPI principal (Python 3.13, incompatible avec les dépendances OpenVoice).

Extrait l'embedding de locuteur ("tone color") d'un échantillon audio et
l'écrit sur disque, pour réutilisation ultérieure par openvoice_synthesize.py.

Usage :
    python openvoice_extract.py --audio echantillon.wav --sortie embedding.pth \
        --checkpoints tts_modeles/openvoice/checkpoints
"""

import argparse
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True, help="Échantillon vocal brut (wav)")
    parser.add_argument("--sortie", required=True, help="Chemin de l'embedding produit (.pth)")
    parser.add_argument("--checkpoints", required=True, help="Dossier des poids OpenVoice V2")
    args = parser.parse_args()

    if not os.path.exists(args.audio):
        print(f"Échantillon introuvable : {args.audio}", file=sys.stderr)
        return 1

    try:
        import torch
        from openvoice import se_extractor
        from openvoice.api import ToneColorConverter
    except ImportError as e:
        print(f"Dépendance OpenVoice manquante dans venv_openvoice : {e}", file=sys.stderr)
        return 1

    device = "cuda" if torch.cuda.is_available() else "cpu"
    chemin_converter = os.path.join(args.checkpoints, "converter")
    converter = ToneColorConverter(os.path.join(chemin_converter, "config.json"), device=device)
    converter.load_ckpt(os.path.join(chemin_converter, "checkpoint.pth"))

    try:
        embedding, _ = se_extractor.get_se(args.audio, converter, vad=True)
    except Exception as e:
        print(f"Extraction impossible (échantillon trop court ou inaudible ?) : {e}", file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(args.sortie), exist_ok=True)
    torch.save(embedding, args.sortie)
    return 0


if __name__ == "__main__":
    sys.exit(main())
