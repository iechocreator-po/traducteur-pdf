"""
Tests du service de traduction.
On simule (mock) les appels réseau à Ollama pour que les tests soient
rapides et ne dépendent pas d'un serveur Ollama réellement lancé.
"""

import pytest
import requests
from unittest.mock import MagicMock, patch

from app.services import translator
from app.services.translator import (
    AppelInterrompu,
    OllamaErreurApplicative,
    OllamaIndisponible,
    appeler_ollama,
    lister_modeles_disponibles,
    traduire_texte,
    verifier_ollama_pret,
)


@pytest.fixture
def horloge(monkeypatch):
    """
    Horloge factice : time.sleep n'attend pas, il avance le temps. Sans ça, un
    test du budget de 30 min durerait 30 min — ou serait non déterministe.
    Retourne la liste des délais demandés, pour asserter le backoff.
    """
    faux_temps = [0.0]
    delais = []

    def faux_sleep(d):
        faux_temps[0] += d

    def faux_monotonic():
        return faux_temps[0]

    monkeypatch.setattr(translator.time, "sleep", faux_sleep)
    monkeypatch.setattr(translator.time, "monotonic", faux_monotonic)

    vrai_attendre = translator._attendre

    def espion_attendre(delai, interruption):
        delais.append(delai)
        return vrai_attendre(delai, interruption)

    monkeypatch.setattr(translator, "_attendre", espion_attendre)
    return delais


def _reponse(payload: dict) -> MagicMock:
    rep = MagicMock()
    rep.json.return_value = payload
    return rep


def _erreur_http(code: int) -> requests.HTTPError:
    rep = MagicMock()
    rep.status_code = code
    return requests.HTTPError(f"HTTP {code}", response=rep)


def test_retry_reseau_puis_succes(horloge):
    """Deux pannes réseau transitoires, puis Ollama revient : la section passe."""
    with patch("app.services.translator.requests.post") as post:
        post.side_effect = [
            requests.ConnectionError("connection refused"),
            requests.ConnectionError("connection refused"),
            _reponse({"response": "ok"}),
        ]
        assert appeler_ollama({"model": "llama3.1"}) == {"response": "ok"}
        assert post.call_count == 3
    # Backoff exponentiel : ~2 s puis ~4 s (jitter ±20 %).
    assert len(horloge) == 2
    assert 1.6 <= horloge[0] <= 2.4
    assert 3.2 <= horloge[1] <= 4.8


def test_panne_permanente_epuise_le_budget_et_abandonne(horloge, monkeypatch):
    """Budget épuisé → OllamaIndisponible, qui est fatale pour le job."""
    monkeypatch.setattr(translator, "OLLAMA_RETRY_BUDGET_SECONDES", 30)
    with patch("app.services.translator.requests.post") as post:
        post.side_effect = requests.ConnectionError("connection refused")
        with pytest.raises(OllamaIndisponible):
            appeler_ollama({"model": "llama3.1"})
    # Le budget borne les tentatives, et il est respecté.
    assert sum(horloge) <= 30


def test_erreur_4xx_abandonne_immediatement_sans_attendre(horloge):
    """Un modèle inconnu ne se répare pas en attendant : 0 retry, 0 sleep."""
    with patch("app.services.translator.requests.post") as post:
        rep = MagicMock()
        rep.raise_for_status.side_effect = _erreur_http(404)
        post.return_value = rep
        with pytest.raises(OllamaErreurApplicative):
            appeler_ollama({"model": "inexistant"})
        assert post.call_count == 1
    assert horloge == []


def test_erreur_5xx_est_reessayee(horloge):
    """Ollama vivant mais en vrac (chargement de modèle, OOM) → transitoire."""
    with patch("app.services.translator.requests.post") as post:
        rep_ko = MagicMock()
        rep_ko.raise_for_status.side_effect = _erreur_http(503)
        post.side_effect = [rep_ko, _reponse({"response": "ok"})]
        assert appeler_ollama({"model": "llama3.1"}) == {"response": "ok"}
        assert post.call_count == 2


def test_backoff_plafonne(horloge, monkeypatch):
    """Le délai ne croît pas indéfiniment : plafond à OLLAMA_RETRY_DELAI_MAX."""
    monkeypatch.setattr(translator, "OLLAMA_RETRY_BUDGET_SECONDES", 600)
    with patch("app.services.translator.requests.post") as post:
        post.side_effect = requests.ConnectionError("connection refused")
        with pytest.raises(OllamaIndisponible):
            appeler_ollama({"model": "llama3.1"})
    plafond = translator.OLLAMA_RETRY_DELAI_MAX * (1 + translator.OLLAMA_RETRY_JITTER)
    assert max(horloge) <= plafond


def test_interruption_sort_du_backoff_sans_epuiser_le_budget(horloge):
    """Pause/Annuler doivent rester vivants pendant l'attente entre 2 tentatives."""
    with patch("app.services.translator.requests.post") as post:
        post.side_effect = requests.ConnectionError("connection refused")
        with pytest.raises(AppelInterrompu):
            appeler_ollama({"model": "llama3.1"}, interruption=lambda: True)
        # L'interruption est vue avant même la première tentative.
        assert post.call_count == 0


@patch("app.services.translator.requests.post")
def test_traduire_texte_retourne_la_reponse_du_modele(mock_post):
    mock_reponse = MagicMock()
    mock_reponse.json.return_value = {"response": "Bonjour le monde"}
    mock_post.return_value = mock_reponse

    resultat = traduire_texte("Hello world", modele="llama3.1", langue_source="anglais", langue_cible="français")

    assert resultat == "Bonjour le monde"
    mock_post.assert_called_once()


@patch("app.services.translator.requests.get")
def test_lister_modeles_disponibles_retourne_les_noms(mock_get):
    mock_reponse = MagicMock()
    mock_reponse.json.return_value = {"models": [{"name": "llama3.1"}, {"name": "mistral"}]}
    mock_get.return_value = mock_reponse

    modeles = lister_modeles_disponibles()

    assert modeles == ["llama3.1", "mistral"]


@patch("app.services.translator.requests.get", side_effect=Exception("Connexion refusée"))
def test_lister_modeles_disponibles_retourne_liste_vide_si_ollama_inaccessible(mock_get):
    modeles = lister_modeles_disponibles()
    assert modeles == []


# ── Preflight verifier_ollama_pret ────────────────────────────────────────────


@patch("app.services.translator.requests.post")
def test_verifier_ollama_pret_ok_quand_traduction_reelle_repond(mock_post):
    mock_reponse = MagicMock()
    mock_reponse.json.return_value = {"response": "Le chat dort."}
    mock_post.return_value = mock_reponse

    pret, message = verifier_ollama_pret("llama3.1")

    assert pret is True
    assert message == "ok"
    # Le preflight envoie bien num_ctx (le garde-fou mémoire), pas un ping nu.
    _, kwargs = mock_post.call_args
    assert kwargs["json"]["options"]["num_ctx"] > 0


@patch("app.services.translator.requests.post", side_effect=requests.Timeout)
def test_verifier_ollama_pret_detecte_llama_server_fige(mock_post):
    """Un llama-server figé ne répond pas au test → not pret + consigne de redémarrage."""
    pret, message = verifier_ollama_pret("llama3.1", timeout=5)
    assert pret is False
    assert "figé" in message and "Redémarrer" in message


@patch("app.services.translator.requests.post", side_effect=requests.ConnectionError("refusée"))
def test_verifier_ollama_pret_detecte_ollama_injoignable(mock_post):
    pret, message = verifier_ollama_pret("llama3.1")
    assert pret is False
    assert "injoignable" in message.lower()


@patch("app.services.translator.requests.post")
def test_verifier_ollama_pret_reponse_vide_est_un_echec(mock_post):
    mock_reponse = MagicMock()
    mock_reponse.json.return_value = {"response": "   "}
    mock_post.return_value = mock_reponse

    pret, message = verifier_ollama_pret("llama3.1")

    assert pret is False
    assert "vide" in message.lower()


# ── Masquage des tags images (jamais envoyés à Ollama) ────────────────────────
@patch("app.services.translator.appeler_ollama")
def test_traduire_texte_ne_envoie_jamais_le_chemin_image_a_ollama(mock_appel):
    # Le modèle voit une sentinelle, pas le chemin ; il la recopie et traduit autour.
    mock_appel.return_value = {"response": "Voici la figure ⟦IMG0⟧ ci-dessus."}

    texte = "Here is the figure ![schéma](chapitre_images/img-0.png) above."
    resultat = traduire_texte(texte, modele="llama3.1", langue_source="anglais", langue_cible="français")

    # Ce qui a été ENVOYÉ à Ollama ne contient NI le chemin NI le tag image.
    payload = mock_appel.call_args[0][0]
    envoye = payload["prompt"]
    assert "img-0.png" not in envoye
    assert "![" not in envoye
    assert "⟦IMG0⟧" in envoye
    # Le résultat restaure le tag d'origine à l'identique.
    assert "![schéma](chapitre_images/img-0.png)" in resultat
    assert "⟦IMG0⟧" not in resultat


@patch("app.services.translator.appeler_ollama")
def test_traduire_texte_sans_image_inchange(mock_appel):
    """Aucune image → aucun masquage, aucune règle en plus (pas de régression)."""
    mock_appel.return_value = {"response": "Bonjour le monde"}
    traduire_texte("Hello world", modele="llama3.1", langue_source="anglais", langue_cible="français")
    payload = mock_appel.call_args[0][0]
    assert payload["prompt"] == "<texte>\nHello world\n</texte>"     # seul le balisage du texte s'ajoute
    assert "MARQUEURS IMAGES" not in payload["system"]


@patch("app.services.translator.appeler_ollama")
def test_balises_recopiees_par_le_modele_retirees(mock_appel):
    mock_appel.return_value = {"response": "<texte>\nBonjour le monde\n</texte>"}
    assert traduire_texte("Hello world", "m", "anglais", "français") == "Bonjour le monde"
    assert "UNIQUEMENT en français" in mock_appel.call_args[0][0]["system"]


@patch("app.services.translator.appeler_ollama")
def test_traduire_texte_filet_si_le_modele_perd_la_sentinelle(mock_appel):
    """Si le modèle oublie la sentinelle, l'image n'est jamais perdue."""
    mock_appel.return_value = {"response": "Traduction sans le marqueur."}
    texte = "Texte ![x](img/a.png) suite."
    resultat = traduire_texte(texte, modele="llama3.1", langue_source="anglais", langue_cible="français")
    assert "![x](img/a.png)" in resultat


@patch("app.services.translator.requests.post")
def test_pictogrammes_situes_masques_puis_restaures(mock_post):
    """Le modèle ne voit jamais « ⟦icône:2.7⟧ » (il pourrait le renuméroter) ; un marqueur perdu revient
    dans le paragraphe, pas en paragraphe isolé."""
    mock_post.return_value = _reponse({"response": "Le symbole ⟦IMG0⟧ est restreint."})
    out = translator.traduire_texte("The ⟦icône:2.7⟧ symbol is restricted. Gain ⟦icône:2.8⟧.", "m",
                                    "anglais", "français")
    envoye = mock_post.call_args.kwargs["json"]["prompt"]
    assert "icône" not in envoye and "⟦IMG0⟧" in envoye and "⟦IMG1⟧" in envoye
    assert out == "Le symbole ⟦icône:2.7⟧ est restreint. ⟦icône:2.8⟧"


def test_reponse_vide_sans_done_est_reessayee(horloge):
    """Un 200 sans texte ni « done » (runner en vrac) est une panne transitoire, pas une traduction vide."""
    with patch("app.services.translator.requests.post") as mock_post:
        mock_post.side_effect = [_reponse({}), _reponse({"response": "Bonjour", "done": True})]
        assert translator.appeler_ollama({"prompt": "x"})["response"] == "Bonjour"
        assert mock_post.call_count == 2


def test_reponse_vide_done_false_est_reessayee(horloge):
    with patch("app.services.translator.requests.post") as mock_post:
        mock_post.side_effect = [_reponse({"response": "", "done": False}), _reponse({"response": "Oui", "done": True})]
        assert translator.appeler_ollama({"prompt": "x"})["response"] == "Oui"


@patch("app.services.translator.requests.post")
def test_numero_d_entree_masque_et_restaure(mock_post):
    """Le modèle ne voit pas « **0029** » (il lui retirait ses étoiles ou le supprimait)."""
    mock_post.return_value = _reponse({"response": "Vous n'avez pas oublié.", "done": True})
    out = translator.traduire_texte("**0029**\n\nYou have not forgotten.", "m", "anglais", "français")
    assert "0029" not in mock_post.call_args.kwargs["json"]["prompt"]
    assert out == "**0029**\n\nVous n'avez pas oublié."


def test_marqueurs_perdus():
    src = "**0009**\n\nHere ⟦icône:3.1⟧ and ⟦icône:3.1⟧."
    assert translator.marqueurs_perdus(src, "**0009**\n\nIci ⟦icône:3.1⟧ et ⟦icône:3.1⟧.") == []
    assert translator.marqueurs_perdus(src, "0009 Ici ⟦icône:3.1⟧.") == ["**0009**", "⟦icône:3.1⟧"]


@patch("app.services.translator.requests.post")
def test_numero_d_entree_remis_seul_sur_sa_ligne(mock_post):
    mock_post.side_effect = [_reponse({"response": "Reviens à la phase du Voyage.", "done": True}),
                             _reponse({"response": "Après le combat.", "done": True})]
    out = translator.traduire_texte("Return to the Voyage Phase.\n\n**0052**\n\nAfter the battle.", "m", "anglais", "français")
    assert out == "Reviens à la phase du Voyage.\n\n**0052**\n\nAprès le combat."


def test_numero_colle_a_une_phrase_compte_comme_perdu():
    assert translator.marqueurs_perdus("x\n\n**0052**\n\ny", "Voyage. **0052**\n\nAprès.") == ["**0052**"]


def test_restaurer_structure_encadre_et_intertitre():
    src = "> If Labyrinthians: Unfriendly, see 0015,\n\n**Fear and Dread**\n\nPlain text."
    tr = "Si Labyrinthiens : Hostiles, voir 0015,\n\nPeur et Effroi\n\nTexte simple."
    assert translator.restaurer_structure(src, tr) == ("> Si Labyrinthiens : Hostiles, voir 0015,\n\n"
                                                       "**Peur et Effroi**\n\nTexte simple.")
    # nombre de paragraphes différent : on ne devine pas l'alignement
    assert translator.restaurer_structure(src, "Un seul paragraphe.") == "Un seul paragraphe."


@patch("app.services.translator.requests.post")
def test_debut_d_encadre_masque_et_restaure(mock_post):
    mock_post.return_value = _reponse({"response": "Texte.\n\n⟦IMG0⟧Si Labyrinthiens : voir 0015.", "done": True})
    out = translator.traduire_texte("Text.\n\n> If Labyrinthians: see 0015.", "m", "anglais", "français")
    assert "> " not in mock_post.call_args.kwargs["json"]["prompt"].replace("<texte>", "")
    assert out == "Texte.\n\n> Si Labyrinthiens : voir 0015."


def test_numero_suivi_d_une_icone_remis_seul():
    assert translator.restaurer_structure("**0007**\n\n⟦icône:3.3⟧ x", "**0007** ⟦icône:3.3⟧ y") == "**0007**\n\n⟦icône:3.3⟧ y"


def test_gras_jamais_sur_un_paragraphe_entier_ni_numero():
    src = "**0037**\n\nEven in a foreign place."
    tr = "Même dans une ville étrangère et hostile, vous trouvez vite vos repères et votre chef parle bien.\n\nSuite."
    assert translator.restaurer_structure(src, tr) == tr
    assert translator.restaurer_structure("x\n\n**0037**", "y\n\n> **0037**") == "y\n\n**0037**"


def test_entree_eloignee_de_son_texte():
    src = "> Return.\n\n**0037**\n\nEven in a foreign place.\n\n**0038**"
    assert translator.entrees_deplacees(src, "> Retour.\n\n**0037**\n\nMême ailleurs.\n\n**0038**") is None
    assert "0037" in translator.entrees_deplacees(src, "Même ailleurs.\n\n> Retour.\n\n**0037**\n\n**0038**")
    assert translator.restaurer_structure("x\n\n**0037**", "y\n\n> \n\n**0037**") == "y\n\n**0037**"


def test_gras_en_trop():
    assert translator.gras_en_trop("**0037**\n\nText.", "**0037**\n\n**Texte entier en gras.**")
    assert not translator.gras_en_trop("**0037**\n\nText.", "**0037**\n\nTexte.")


@patch("app.services.translator.appeler_ollama")
def test_morceau_coupe_aux_numeros_d_entree(mock_appel):
    """Chaque partie entre deux numéros est traduite à part : un numéro ne peut plus être déplacé."""
    mock_appel.side_effect = lambda p, **k: {"response": p["prompt"].replace("<texte>\n", "").replace("\n</texte>", "")
                                             .replace("Intro", "Introduction").replace("Body", "Corps"), "done": True}
    out = translator.traduire_texte("Intro.\n\n**0001**\n\nBody one.\n\n**0002**\n\nBody two.", "m", "anglais", "français")
    assert out == "Introduction.\n\n**0001**\n\nCorps one.\n\n**0002**\n\nCorps two."
    assert mock_appel.call_count == 3 and all("0001" not in c[0][0]["prompt"] for c in mock_appel.call_args_list)


def test_entree_en_double_ou_deplacee():
    src = "Intro.\n\n**0001**\n\nBody."
    assert translator.entrees_deplacees(src, "**0001**\n\nIntro.\n\n**0001**\n\nCorps.")


def test_numero_invente_sous_un_intertitre():
    assert translator.entrees_deplacees("**Starting a Campaign**\n\nPrepare.", "**Début d'une Campagne**\n**0001**\n\nPréparez.")


def test_gras_ajoute_sous_un_intertitre():
    assert translator.gras_en_trop("**Starting a Campaign**\n\nPrepare.", "**Début d'une Campagne**\n**END**\n\nPréparez.")
