"""
Tests du registre des voix clonées (CRUD sur registre.json, isolé par la
fixture registre_voix_clonees_isole de conftest.py).
"""

import os

import pytest

from app.services import voix_clonees


def test_lister_voix_vide_sans_registre():
    assert voix_clonees.lister_voix() == []


def test_creer_voix_cree_entree_et_dossier():
    entree = voix_clonees.creer_voix("Ma voix")
    assert entree["nom"] == "Ma voix"
    assert entree["statut"] == "en_attente"
    assert os.path.isdir(voix_clonees.chemin_dossier_voix(entree["id"]))
    assert voix_clonees.lister_voix() == [entree]


def test_creer_voix_nom_vide_leve_une_erreur():
    with pytest.raises(ValueError):
        voix_clonees.creer_voix("   ")


def test_creer_voix_suffixe_les_noms_en_collision():
    a = voix_clonees.creer_voix("Ma voix")
    b = voix_clonees.creer_voix("Ma voix")
    assert a["nom"] == "Ma voix"
    assert b["nom"] == "Ma voix (2)"


def test_obtenir_voix_introuvable_retourne_none():
    assert voix_clonees.obtenir_voix("inconnu") is None


def test_mettre_a_jour_voix_modifie_le_statut():
    entree = voix_clonees.creer_voix("Ma voix")
    maj = voix_clonees.mettre_a_jour_voix(entree["id"], statut="termine", chemin_embedding="x.pth")
    assert maj["statut"] == "termine"
    assert maj["chemin_embedding"] == "x.pth"
    assert voix_clonees.obtenir_voix(entree["id"])["statut"] == "termine"


def test_renommer_voix():
    entree = voix_clonees.creer_voix("Ma voix")
    maj = voix_clonees.renommer_voix(entree["id"], "Nouveau nom")
    assert maj["nom"] == "Nouveau nom"


def test_renommer_voix_introuvable_retourne_none():
    assert voix_clonees.renommer_voix("inconnu", "x") is None


def test_supprimer_voix_retire_entree_et_dossier():
    entree = voix_clonees.creer_voix("Ma voix")
    dossier = voix_clonees.chemin_dossier_voix(entree["id"])
    assert voix_clonees.supprimer_voix(entree["id"]) is True
    assert voix_clonees.lister_voix() == []
    assert not os.path.exists(dossier)


def test_supprimer_voix_introuvable_retourne_false():
    assert voix_clonees.supprimer_voix("inconnu") is False
