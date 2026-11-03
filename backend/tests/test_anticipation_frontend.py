"""
Onglet Anticipation du frontend (module E) : cohérence de frontend/anticipation/cards.json,
câblage dans index.html, et rendu de js/module-anticipation.js (échappement, épingles,
filtre) exécuté par node quand il est installé.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
CARDS = json.loads((FRONTEND / "anticipation" / "cards.json").read_text(encoding="utf-8"))
STATUTS = {"rapport", "maquette", "planifiee", "en_construction", "livree", "rejetee"}


def test_cards_json_coherent():
    cards = CARDS["cards"]
    assert cards, "au moins une carte"
    ids = [c["id"] for c in cards]
    assert len(set(ids)) == len(ids), "ids uniques"
    for c in cards:
        assert c["statut"] in STATUTS, c["id"]
        assert c["titre"] and c["description"] and re.fullmatch(r"\d{4}-\d{2}-\d{2}", c["date"]), c["id"]
        if "page" in c:
            assert re.fullmatch(r"docs/[a-z0-9][a-z0-9/-]*\.html", c["page"]) and "//" not in c["page"], c["page"]
            assert (FRONTEND / c["page"]).is_file(), f"page absente : {c['page']}"
        for cle in ("derniereRevue", "dernierAudit"):
            if cle in c:
                assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", c[cle]["date"]), f"{c['id']} {cle}"
                assert all(re.fullmatch(r"[0-9a-f]{7,40}", h) for h in c[cle].get("commits", []))


def test_onglet_cable_dans_index():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert 'data-module="anticipation"' in html
    assert 'id="anticipation-liste"' in html
    assert re.search(r'<script src="js/module-anticipation\.js\?v=\d+"></script>', html)
    commun = (FRONTEND / "js" / "commun.js").read_text(encoding="utf-8")
    assert '"anticipation"' in commun, "activerModule connaît le module"


SCRIPT_NODE = r"""
const fs = require("fs"); const vm = require("vm");
const ctx = { console }; vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8").split("\n(() => {\n  const CLE_EPINGLES")[0] + "\nglobalThis.R = AnticipationRendu;", ctx);
const R = ctx.R;
const cards = [
  { id: "audit", titre: "Audit", description: "d", statut: "rapport", date: "2026-10-26", epingle: true,
    page: "docs/audit-code.html", dernierAudit: { date: "2026-10-26", commits: ["9a69822", "<b>"] } },
  { id: "a", titre: "<img src=x onerror=alert(1)>", description: "\"><i>", statut: "maquette", date: "2026-10-11",
    page: "../../etc/passwd.html", liens: [{ titre: "ko", url: "javascript:alert(1)" }, { titre: "ok", url: "https://ex.com" }] },
  { id: "b", titre: "B", description: "d", statut: "livree", date: "2026-10-30", commits: ["abc1234"] },
];
const tout = R.listeHTML(cards, { filtre: "toutes", tri: "date", epingles: {} });
const filtre = R.listeHTML(cards, { filtre: "livree", tri: "date", epingles: { audit: false } });
process.stdout.write(JSON.stringify({ tout, filtre }));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node absent")
def test_rendu_echappe_epingle_et_filtre():
    sortie = subprocess.run(
        ["node", "-e", SCRIPT_NODE, str(FRONTEND / "js" / "module-anticipation.js")],
        capture_output=True, text=True, check=True, timeout=30,
    )
    r = json.loads(sortie.stdout)
    tout, filtre = r["tout"], r["filtre"]
    # Carte épinglée par défaut au-dessus de la barre ; compteurs sans elle.
    assert tout.index('data-antic-id="audit"') < tout.index("antic-barre")
    assert "Toutes (2)" in tout
    assert tout.index('data-antic-id="b"') < tout.index('data-antic-id="a"'), "plus récente d'abord"
    # Échappement, chemin hors docs/ refusé, lien non http(s) écarté, commit non hexadécimal écarté.
    assert "<img" not in tout and "<i>" not in tout and "<b>" not in tout
    assert "etc/passwd" not in tout and "javascript:" not in tout
    assert 'href="https://ex.com"' in tout
    assert "<code>9a69822</code>" in tout and "Livré dans <code>abc1234</code>" in tout
    # Désépinglée par le navigateur : la carte revient dans la liste ; le filtre Livrée ne garde que B.
    assert "Rapport (1)" in filtre
    assert 'data-antic-id="b"' in filtre and 'data-antic-id="a"' not in filtre and 'data-antic-id="audit"' not in filtre
