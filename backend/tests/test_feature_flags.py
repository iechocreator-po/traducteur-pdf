"""
Fusion et priorité des feature flags :
défauts < bilbao.features.json < feature_flags.json local < env FEATURE_*.
"""

import json

from app.config import feature_flags


def _rediriger(tmp_path, monkeypatch, bilbao=None, local=None):
    """Redirige les deux fichiers de flags vers tmp_path (créés si fournis)."""
    chemin_bilbao = tmp_path / "bilbao.features.json"
    chemin_local = tmp_path / "feature_flags.json"
    if bilbao is not None:
        chemin_bilbao.write_text(json.dumps(bilbao), encoding="utf-8")
    if local is not None:
        chemin_local.write_text(json.dumps(local), encoding="utf-8")
    monkeypatch.setattr(feature_flags, "CHEMIN_FLAGS_BILBAO", chemin_bilbao)
    monkeypatch.setattr(feature_flags, "CHEMIN_FLAGS_DEFAUT", chemin_local)


def test_defauts_sans_aucun_fichier(tmp_path, monkeypatch):
    _rediriger(tmp_path, monkeypatch)
    flags = feature_flags.charger_flags()
    assert flags["mode_avance"] is True
    assert flags["export_fiche_html"] is True


def test_bilbao_ecrase_les_defauts(tmp_path, monkeypatch):
    _rediriger(tmp_path, monkeypatch, bilbao={
        "genere_par": "bilbao",
        "produit": "toledo",
        "flags": {"export_fiche_html": False, "mode_avance": False},
    })
    flags = feature_flags.charger_flags()
    assert flags["export_fiche_html"] is False
    assert flags["mode_avance"] is False


def test_bilbao_ignore_les_metadonnees(tmp_path, monkeypatch):
    _rediriger(tmp_path, monkeypatch, bilbao={
        "genere_par": "bilbao", "genere_le": "2026-07-14T00:00:00Z",
        "produit": "toledo", "note": "…", "flags": {"export_fiche_html": False},
    })
    flags = feature_flags.charger_flags()
    assert "genere_par" not in flags
    assert flags["export_fiche_html"] is False


def test_fichier_bilbao_invalide_est_tolere(tmp_path, monkeypatch):
    chemin_bilbao = tmp_path / "bilbao.features.json"
    chemin_bilbao.write_text("{ pas du json", encoding="utf-8")
    monkeypatch.setattr(feature_flags, "CHEMIN_FLAGS_BILBAO", chemin_bilbao)
    monkeypatch.setattr(feature_flags, "CHEMIN_FLAGS_DEFAUT", tmp_path / "absent.json")
    flags = feature_flags.charger_flags()
    assert flags["export_fiche_html"] is True  # retombe sur les défauts


def test_local_ecrase_bilbao(tmp_path, monkeypatch):
    _rediriger(
        tmp_path, monkeypatch,
        bilbao={"flags": {"export_fiche_html": False}},
        local={"export_fiche_html": True},
    )
    assert feature_flags.charger_flags()["export_fiche_html"] is True


def test_env_ecrase_tout(tmp_path, monkeypatch):
    _rediriger(
        tmp_path, monkeypatch,
        bilbao={"flags": {"export_fiche_html": False}},
        local={"export_fiche_html": False},
    )
    monkeypatch.setenv("FEATURE_EXPORT_FICHE_HTML", "true")
    assert feature_flags.charger_flags()["export_fiche_html"] is True
