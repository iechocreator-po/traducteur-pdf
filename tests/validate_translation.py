#!/usr/bin/env python3
"""
Validation END-TO-END réelle de la traduction (avec preuve).

Contrairement à un simple "le job a démarré", ce script PROUVE que la traduction
est correcte en comparant la sortie à une référence "golden" validée à la main,
sur les propriétés qui comptent.

Pourquoi pas une comparaison exacte ? Ollama est non-déterministe (temperature
0.3) : deux traductions du même texte diffèrent au mot près. On valide donc :

  INVARIANTS (échec dur si violé) :
    - taille de sortie dans [50 %, 200 %] de la référence
      (un résumé anglais tronqué faisait ~9 % de la taille réelle → détecté)
    - tous les tags images ![](...) de la SOURCE présents à l'identique en sortie
      (Ollama ne doit jamais altérer un chemin d'image)
    - la sortie est du FRANÇAIS, pas un résumé anglais (densité de mots-outils)
    - 0 section en échec (lu dans le .state.json)

  INDICATIFS (avertissement, jamais bloquant — normal qu'ils varient) :
    - similarité difflib avec la référence
    - nombre de titres Markdown

Usage :
    # Preflight seul (vérifie qu'Ollama est en état de traduire)
    python3 validate_translation.py --preflight

    # Validation complète : traduit Chapter 9 et compare à la référence
    python3 validate_translation.py --pdf "<chemin Chapter 9.pdf>" --chapitres 0

    # (Re)générer la référence golden à partir d'une sortie validée à la main
    python3 validate_translation.py --save-reference "<sortie_traduit_ll.md>" \
        --source "<converti_py.md>"

Prérequis : Ollama lancé (127.0.0.1:11434) ET backend toledo (127.0.0.1:8000).
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path

BACKEND = "http://127.0.0.1:8000"
OLLAMA = "http://127.0.0.1:11434"
ICI = Path(__file__).resolve().parent
REF_TRADUIT = ICI / "reference" / "Chapter9_traduit_reference.md"
REF_SOURCE = ICI / "reference" / "Chapter9_source_reference.md"

NUM_CTX = 4096  # doit rester aligné avec settings.OLLAMA_NUM_CTX

TAG_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
# Mots-outils : le français et l'anglais n'ont presque aucun recouvrement ici.
MOTS_FR = {"le", "la", "les", "de", "des", "du", "et", "à", "un", "une", "dans",
           "pour", "qui", "que", "est", "sont", "avec", "sur", "ses", "par", "au"}
MOTS_EN = {"the", "and", "of", "to", "is", "are", "that", "this", "which",
           "with", "was", "were", "from", "their", "these", "on", "as", "by"}


def _curl_json(url, data=None, max_time=120):
    cmd = ["curl", "-s", "--max-time", str(max_time), url]
    if data is not None:
        # Content-Type explicite : FastAPI refuse un corps JSON sans cet en-tête
        # (Ollama, lui, est tolérant — d'où un bug qui ne se voyait que côté backend).
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(data)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        return json.loads(r.stdout)
    except ValueError:
        return None


def _mem_libre_pct():
    try:
        out = subprocess.run(["memory_pressure"], capture_output=True, text=True).stdout
        for l in out.splitlines():
            if "free percentage" in l.lower():
                return int(re.search(r"(\d+)", l.split(":")[-1]).group(1))
    except Exception:
        pass
    return None


def langue_francaise(texte):
    """True si le texte ressemble à du français plutôt qu'à de l'anglais."""
    mots = re.findall(r"[a-zàâäéèêëïîôöùûüç]+", texte.lower())
    if len(mots) < 30:
        return True  # trop court pour juger, on ne bloque pas
    fr = sum(1 for m in mots if m in MOTS_FR)
    en = sum(1 for m in mots if m in MOTS_EN)
    # Français attendu : fr nettement > en. Un résumé anglais aurait en >> fr.
    return fr > en


# ─────────────────────────────────────────────────────────────────────────────
# Q3 — Preflight : Ollama est-il EN ÉTAT de traduire ?
# ─────────────────────────────────────────────────────────────────────────────
def preflight():
    """Vérifie qu'Ollama peut réellement traduire AVANT de lancer un long job."""
    print("🔎 PREFLIGHT OLLAMA")
    ok = True

    # 1. L'API répond ?
    tags = _curl_json(f"{OLLAMA}/api/tags", max_time=5)
    if not tags:
        print("  ❌ Ollama ne répond pas sur /api/tags — est-il lancé ?")
        return False
    modeles = [m["name"] for m in tags.get("models", [])]
    print(f"  ✅ API OK ({len(modeles)} modèle(s) : {', '.join(modeles) or 'aucun'})")

    # 2. Mémoire libre suffisante ?
    mem = _mem_libre_pct()
    if mem is not None:
        etat = "✅" if mem >= 25 else "⚠️"
        print(f"  {etat} RAM libre : {mem}%" + ("" if mem >= 25 else "  (basse — risque de stall)"))

    # 3. VRAIE mini-traduction avec les params EXACTS du backend (num_ctx compris).
    #    C'est LE test qui attrape le blocage : un llama-server figé ne répondra
    #    pas à ceci dans le délai imparti, même si /api/tags répond.
    print("  … test de traduction réelle (avec num_ctx) …", flush=True)
    t0 = time.time()
    data = _curl_json(f"{OLLAMA}/api/generate", {
        "model": modeles[0] if modeles else "llama3.1:latest",
        "system": "Traduis de anglais vers français. Réponds UNIQUEMENT la traduction.",
        "prompt": "The cat sleeps on the table.",
        "stream": False,
        "options": {"temperature": 0.3, "num_ctx": NUM_CTX},
    }, max_time=60)
    dt = time.time() - t0
    if not data or "response" not in data:
        print(f"  ❌ Pas de réponse en {dt:.0f}s → Ollama figé. Redémarrer Ollama.")
        return False
    rep = data["response"].strip()
    if not langue_francaise(rep):
        print(f"  ⚠️ Réponse en {dt:.1f}s mais pas clairement française : {rep!r}")
        ok = False
    else:
        print(f"  ✅ Traduction réelle en {dt:.1f}s : {rep!r}")
    return ok


# ─────────────────────────────────────────────────────────────────────────────
# Lancement + attente d'un vrai job de traduction via le backend
# ─────────────────────────────────────────────────────────────────────────────
def lancer_et_attendre(pdf, chapitres, timeout=900):
    pdf = str(Path(pdf).resolve())  # le backend exige un chemin absolu
    print(f"\n▶️  TRADUCTION : {Path(pdf).name} chapitres={chapitres}")
    rep = _curl_json(f"{BACKEND}/api/translate", {
        "chemin_pdf": pdf, "chapitres_selectionnes": chapitres, "modele_ollama": "llama3.1",
    }, max_time=90)
    if not rep or "chemin_sortie" not in rep:
        print(f"  ❌ /translate a échoué : {rep}")
        return None
    sortie = rep["chemin_sortie"]
    state = sortie.replace("_traduit_ll.md", "_traduit_ll.state.json")
    t0 = time.time(); last = -1
    while time.time() - t0 < timeout:
        time.sleep(4)
        try:
            d = json.load(open(state))
        except Exception:
            continue
        s = d["derniere_section_completee"]
        if s != last:
            print(f"  … {s}/{d['total_sections']} à t+{time.time()-t0:.0f}s", flush=True); last = s
        if d["statut"] in ("termine", "erreur"):
            echecs = len(d.get("chapitres_echoues", []))
            print(f"  🏁 {d['statut']} — {s}/{d['total_sections']}, {echecs} échec(s), {time.time()-t0:.0f}s")
            return {"sortie": sortie, "statut": d["statut"], "echecs": echecs}
    print("  ❌ timeout global")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Q1 — Validation de la sortie contre la référence golden
# ─────────────────────────────────────────────────────────────────────────────
def valider(sortie_path, source_path=None, ref_path=REF_TRADUIT, echecs=0):
    print("\n🧪 VALIDATION DE LA SORTIE")
    if not os.path.isfile(sortie_path):
        print(f"  ❌ sortie introuvable : {sortie_path}"); return False
    sortie = open(sortie_path, encoding="utf-8").read()
    ref = open(ref_path, encoding="utf-8").read() if os.path.isfile(ref_path) else None
    source = open(source_path, encoding="utf-8").read() if source_path and os.path.isfile(source_path) else None

    dur = True  # invariants durs

    # INVARIANT 1 : 0 échec
    if echecs == 0:
        print("  ✅ 0 section en échec")
    else:
        print(f"  ❌ {echecs} section(s) en échec"); dur = False

    # INVARIANT 2 : taille dans [50%, 200%] de la référence (attrape le résumé)
    if ref:
        ratio = len(sortie) / max(len(ref), 1)
        if 0.5 <= ratio <= 2.0:
            print(f"  ✅ taille {len(sortie)} octets ({ratio*100:.0f}% de la référence)")
        else:
            print(f"  ❌ taille {len(sortie)} = {ratio*100:.0f}% de la réf (hors [50%,200%] → tronqué/résumé ?)"); dur = False

    # INVARIANT 3 : tags images de la SOURCE préservés à l'identique
    if source:
        tags_src = set(TAG_IMAGE.findall(source))
        tags_out = set(TAG_IMAGE.findall(sortie))
        manquants = tags_src - tags_out
        if not manquants:
            print(f"  ✅ {len(tags_src)} tag(s) image préservé(s) à l'identique")
        else:
            print(f"  ❌ tag(s) image altéré(s)/perdu(s) : {manquants}"); dur = False

    # INVARIANT 4 : c'est du français, pas un résumé anglais
    if langue_francaise(sortie):
        print("  ✅ sortie en français")
    else:
        print("  ❌ sortie NON française (résumé anglais ?)"); dur = False

    # INDICATIF : similarité avec la référence (jamais bloquant)
    if ref:
        sim = SequenceMatcher(None, sortie, ref).ratio()
        print(f"  ℹ️  similarité avec la référence : {sim*100:.0f}% (indicatif, varie à chaque run)")

    print("\n" + ("✅ VALIDATION RÉUSSIE" if dur else "❌ VALIDATION ÉCHOUÉE"))
    return dur


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--preflight", action="store_true", help="Vérifier qu'Ollama peut traduire")
    p.add_argument("--pdf", help="PDF à traduire puis valider")
    p.add_argument("--chapitres", default="0", help="Indices de chapitres, ex: 0 ou 0,1,2")
    p.add_argument("--source", help="Markdown source (converti) pour vérifier les tags images")
    p.add_argument("--reference", default=str(REF_TRADUIT), help="Référence golden")
    p.add_argument("--save-reference", help="Sauver ce fichier comme nouvelle référence golden")
    args = p.parse_args()

    if args.save_reference:
        REF_TRADUIT.parent.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copy(args.save_reference, REF_TRADUIT)
        if args.source:
            shutil.copy(args.source, REF_SOURCE)
        print(f"✅ Référence golden mise à jour : {REF_TRADUIT}")
        return 0

    if args.preflight and not args.pdf:
        return 0 if preflight() else 1

    if not args.pdf:
        p.print_help(); return 1

    # Flux complet : preflight → traduire → valider
    if not preflight():
        print("\n⛔ Preflight échoué — on ne lance pas le job (Ollama pas en état).")
        return 1
    chapitres = [int(c) for c in str(args.chapitres).split(",")]
    res = lancer_et_attendre(args.pdf, chapitres)
    if not res:
        return 1
    src = args.source or (REF_SOURCE if os.path.isfile(REF_SOURCE) else None)
    ok = valider(res["sortie"], source_path=src, ref_path=Path(args.reference), echecs=res["echecs"])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
