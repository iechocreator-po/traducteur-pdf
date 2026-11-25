"""
Contrôle de la LANGUE d'une traduction : le modèle a-t-il bien écrit dans la langue cible ?

Mesuré sur le Cycle I (qwen2.5, 17/11/2026) : sur 126 morceaux, 6 contenaient du chinois au milieu du français
(« Êtes-vous de ceux qui… rester de旁观者并… ») et un paragraphe entier était en espagnol (« Tú logras persuadir
al hombre »). Le ratio de longueur ne voit rien : la longueur est normale. Deux signaux, sans dépendance :
  1. une écriture absente de la source et étrangère aux langues gérées (idéogrammes, cyrillique, arabe…) ;
  2. un paragraphe d'au moins MOTS_MIN_PARAGRAPHE mots où les mots-outils d'une AUTRE langue gérée dominent
     nettement ceux de la langue cible (un paragraphe resté en anglais compte aussi).
"""

import re

# Paragraphe resté dans sa langue d'origine parce que toutes les traductions dérivaient (chinois…) : commentaire
# Markdown placé sur la ligne qui le précède. Invisible pour l'audio (tts retire les commentaires) ; rendu en couleur
# par la mise en page (reconstruction) et le lecteur de la Bibliothèque.
MARQUE_NON_TRADUIT = "<!-- non-traduit -->"

MOTS_MIN_PARAGRAPHE = 8
DOMINATION_MIN = 3          # mots-outils de l'autre langue, au minimum
FACTEUR_DOMINATION = 2      # … et au moins deux fois plus que ceux de la langue cible

# Écritures qu'aucune des langues gérées (anglais, français, espagnol) n'utilise.
_RE_ECRITURE_ETRANGERE = re.compile(
    "[Ѐ-ӿ֐-׿؀-ۿ฀-๿぀-ヿ㐀-䶿一-鿿가-힯]")

# Mots-outils propres à chaque langue (les mots communs à deux langues, « a », « de », « la », « en », sont exclus).
MOTS_OUTILS = {
    "français": {"le", "les", "des", "du", "et", "est", "vous", "votre", "vos", "nous", "une", "dans", "pour", "pas",
                 "qui", "que", "sur", "avec", "au", "aux", "ce", "cette", "il", "elle", "ils", "sont", "mais", "ou"},
    "espagnol": {"el", "los", "las", "del", "y", "es", "usted", "tú", "su", "sus", "una", "para", "no", "que", "con",
                 "al", "este", "esta", "él", "ella", "ellos", "son", "pero", "o", "por", "lo", "se", "muy"},
    "anglais": {"the", "and", "of", "to", "is", "you", "your", "with", "for", "not", "this", "that", "are", "they",
                "he", "she", "it", "but", "or", "from", "have", "has", "was", "were", "will", "which"},
}
# « que » est commun au français et à l'espagnol : il ne départage rien.
for _l in ("français", "espagnol"):
    MOTS_OUTILS[_l] = MOTS_OUTILS[_l] - {"que"}


# Texte gardé tel quel par conception : zone « conserver » (scan_colonnes) et bloc de code (règle du prompt). Il est
# dans la langue d'origine sans être une dérive ; jugé, il faisait retraduire le morceau cinq fois et le marquait
# « non traduit » à chaque rejeu (jamais mis en cache).
_RE_GARDE_TEL_QUEL = re.compile(r"<!-- conserver -->\n.*?\n<!-- /conserver -->|^```.*?^```", re.S | re.M)


def langue_inattendue(source: str, traduit: str, langue_cible: str) -> str | None:
    """Description du défaut (« écriture étrangère : 旁观者… », « paragraphe en espagnol : … »), ou None.
    Le texte gardé tel quel (zone « conserver », bloc de code) n'est pas jugé ; un paragraphe ordinaire recopié tel
    quel de la source, lui, est bien une dérive (le modèle n'a pas traduit)."""
    source = _RE_GARDE_TEL_QUEL.sub("", source)
    traduit = _RE_GARDE_TEL_QUEL.sub("", traduit)
    etrangers = _RE_ECRITURE_ETRANGERE.findall(traduit)
    if etrangers and not _RE_ECRITURE_ETRANGERE.search(source):
        return f"écriture étrangère dans la traduction : « {''.join(etrangers[:12])} »"
    cible = MOTS_OUTILS.get(langue_cible)
    if not cible:
        return None
    for paragraphe in re.split(r"\n\s*\n", traduit):
        mots = re.findall(r"[a-záéíóúñüàâçèêëîïôûœ’']+", paragraphe.lower())
        if len(mots) < MOTS_MIN_PARAGRAPHE:
            continue
        n_cible = sum(m in cible for m in mots)
        for langue, outils in MOTS_OUTILS.items():
            if langue == langue_cible:
                continue
            n = sum(m in outils for m in mots)
            if n >= DOMINATION_MIN and n >= FACTEUR_DOMINATION * max(n_cible, 1):
                return f"paragraphe en {langue} : « {paragraphe.strip()[:60]} »"
    return None
