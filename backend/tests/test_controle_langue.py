"""Contrôle de la langue d'une traduction (mesuré sur le Cycle I, qwen2.5, 17/11/2026)."""

from app.services.controle_langue import langue_inattendue

SOURCE = "Are you one of those who weigh the evils? she asks. Or one of those who would rather stand aside?"


def test_chinois_au_milieu_du_francais():
    traduit = "« Êtes-vous de ceux qui pèsent les maux ? » demande-t-elle. « Ou de ceux qui préfèrent rester de旁观者并避开它们 ? »"
    assert "écriture étrangère" in langue_inattendue(SOURCE, traduit, "français")


def test_paragraphe_en_espagnol():
    traduit = "Tú logras persuadir al hombre de que no es el momento para pelear, y él se aleja con su espada."
    assert "espagnol" in langue_inattendue("You successfully persuade the man that it is not the time.", traduit,
                                           "français")


def test_paragraphe_reste_en_anglais():
    traduit = "You knock on the door and enter. There is a great fireplace in the back of the room and you sweat."
    assert "anglais" in langue_inattendue(traduit, traduit, "français")


def test_francais_normal_et_noms_propres_acceptes():
    traduit = ("Vous frappez à la porte et entrez. Il y a une grande cheminée au fond de la pièce, et vous "
               "commencez à transpirer. Marcin Welnicki, Mateusz Lenart, Konrad Rekieć.")
    assert langue_inattendue("You knock on the door…", traduit, "français") is None
    assert langue_inattendue("x", "Marcin Welnicki Mateusz Lenart Konrad Rekieć Iain MacAskill Sylvie Gorak", "français") is None


def test_zone_conserver_et_code_ne_sont_pas_des_derives():
    """Une zone « conserver » ou un bloc de code restent en anglais par conception : jugés, ils faisaient retraduire
    le morceau cinq fois et le marquaient « non traduit » (revue du 24/11/2026)."""
    garde = "<!-- conserver -->\nYou have to choose the path that is the best for you and your party.\n<!-- /conserver -->"
    code = "```\n# this is the loop that will run for each of the items and it will not stop\n```"
    traduit = f"Vous entrez dans la salle.\n\n{garde}\n\n{code}"
    assert langue_inattendue(f"You enter the room.\n\n{garde}\n\n{code}", traduit, "français") is None
