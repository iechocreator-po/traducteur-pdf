"""
Tests de l'assertion d'énergie (principe cible ⑩, défaut F12).
"""

import sys
from datetime import datetime

import pytest

from app.services import energie


@pytest.fixture(autouse=True)
def relacher_apres():
    yield
    energie.relacher()


class _FauxProcessus:
    def __init__(self, *a, **kw):
        self.args = a
        self.termine = False

    def poll(self):
        return 0 if self.termine else None

    def terminate(self):
        self.termine = True

    def wait(self, timeout=None):
        return 0


def test_acquerir_est_idempotent(monkeypatch):
    """
    Le worker appelle `acquerir()` à CHAQUE travail dépilé mais ne relâche qu'à
    file vide. Sans idempotence (avec un compteur de prises), trois jobs
    enchaînés laisseraient caffeinate tourner pour toujours et le Mac ne se
    rendormirait plus jamais.
    """
    lances = []
    monkeypatch.setattr(energie, "disponible", lambda: True)
    monkeypatch.setattr(
        energie.subprocess, "Popen", lambda *a, **kw: lances.append(a) or _FauxProcessus(*a)
    )

    assert energie.acquerir() is True
    assert energie.acquerir() is True
    assert energie.acquerir() is True

    assert len(lances) == 1  # un seul caffeinate, pas trois
    assert energie.actif() is True


def test_un_seul_relacher_suffit_a_tout_arreter(monkeypatch):
    monkeypatch.setattr(energie, "disponible", lambda: True)
    monkeypatch.setattr(energie.subprocess, "Popen", lambda *a, **kw: _FauxProcessus(*a))

    energie.acquerir()
    energie.acquerir()
    energie.relacher()

    assert energie.actif() is False


def test_caffeinate_meurt_avec_le_backend(monkeypatch):
    """
    `-w <pid>` : aucune assertion orpheline ne peut survivre à un crash du
    backend. Sans ça, un plantage laisserait le Mac éveillé indéfiniment.
    """
    captures = []
    monkeypatch.setattr(energie, "disponible", lambda: True)
    monkeypatch.setattr(
        energie.subprocess, "Popen", lambda *a, **kw: captures.append(a[0]) or _FauxProcessus()
    )

    energie.acquerir()

    commande = captures[0]
    assert commande[0] == "caffeinate"
    assert "-i" in commande  # veille système, pas l'écran
    assert "-w" in commande
    assert commande[commande.index("-w") + 1].isdigit()


def test_relacher_sans_acquerir_ne_leve_pas():
    energie.relacher()  # ne doit pas exploser


def test_hors_macos_degrade_proprement(monkeypatch):
    monkeypatch.setattr(energie, "disponible", lambda: False)
    assert energie.acquerir() is False
    assert energie.actif() is False


def test_caffeinate_absent_degrade_proprement(monkeypatch):
    """Un binaire manquant ne doit pas empêcher la traduction de tourner."""
    monkeypatch.setattr(energie, "disponible", lambda: True)

    def popen_qui_echoue(*a, **kw):
        raise OSError("caffeinate: command not found")

    monkeypatch.setattr(energie.subprocess, "Popen", popen_qui_echoue)
    assert energie.acquerir() is False


def test_la_commande_de_reveil_est_rendue_mais_jamais_executee():
    """
    ⚠️ La moitié manquante du principe ⑩ : `pmset schedule wake` exige les droits
    admin. Le backend ne l'arme pas — il rend la commande, et c'est délibéré.
    """
    commande = energie.commande_reveil_programme(datetime(2026, 7, 30, 23, 0, 0))
    assert commande.startswith("sudo pmset schedule wake")
    assert "07/30/26 23:00:00" in commande


@pytest.mark.skipif(sys.platform != "darwin", reason="caffeinate n'existe que sur macOS")
def test_caffeinate_reel_demarre_et_s_arrete():
    """Un vrai caffeinate, pas un faux — la seule preuve que ça marche ici."""
    assert energie.acquerir() is True
    assert energie.actif() is True
    energie.relacher()
    assert energie.actif() is False
