"""Titre de la fenêtre de terminal : écrit seulement sur un vrai terminal."""

import io

from app.services.titre_terminal import definir_titre, port_depuis_arguments


class FauxTerminal(io.StringIO):
    def isatty(self):
        return True


def test_ecrit_la_sequence_osc_sur_un_terminal():
    flux = FauxTerminal()
    assert definir_titre("toledo · backend (port 8000)", flux) is True
    assert flux.getvalue() == "\033]0;toledo · backend (port 8000)\007"


def test_n_ecrit_rien_hors_terminal():
    flux = io.StringIO()   # fichier de log, tube, tests : isatty() est faux
    assert definir_titre("toledo", flux) is False
    assert flux.getvalue() == ""


def test_aucun_caractere_de_controle_injecte_dans_le_titre():
    flux = FauxTerminal()
    definir_titre("a\033]0;piege\007b", flux)
    assert flux.getvalue().count("\033") == 1 and flux.getvalue().count("\007") == 1


def test_port_depuis_les_arguments():
    assert port_depuis_arguments(["uvicorn", "app.main:app", "--port", "8001"]) == 8001
    assert port_depuis_arguments(["uvicorn", "--port=8123"]) == 8123
    assert port_depuis_arguments(["pytest"]) == 8000
    assert port_depuis_arguments(["x", "--port", "abc"]) == 8000
