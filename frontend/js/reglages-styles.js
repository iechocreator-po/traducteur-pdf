// ── Réglages (⚙) › carte « Style de l'interface » : choix du style visuel ──────────────────────────────────
// Le style est appliqué par appliquerStyle() (commun.js) via <html data-style>.
// Ici : seulement la grille de sélection, qui réagit au clic et à l'événement "style-change".

const STYLES_VISUELS = [
  { id: "classique", nom: "Classique", desc: "L'apparence habituelle de toledo : vert d'eau, coins arrondis." },
  { id: "suisse", nom: "Bureau suisse", desc: "Grille nette, filets noirs, rouge vif, aucun arrondi." },
  { id: "machine", nom: "Machine à écrire", desc: "Chasse fixe, encre et ruban rouge, boutons entre crochets, progression en caractères." },
  { id: "classeur", nom: "Classeur d'archives", desc: "Onglets de chemise, fiches lignées, statuts en tampon, règle graduée." },
  { id: "plan", nom: "Plan de travail", desc: "Bleu de plan quadrillé, texte technique, repères de cadrage jaunes." },
  { id: "calque", nom: "Calque", desc: "Feuilles de papier calque superposées, titres en italique d'encre." },
];

function construireChoixStyles() {
  const liste = $("styles-grille");
  liste.replaceChildren();
  for (const s of STYLES_VISUELS) {
    const li = document.createElement("li");
    const b = document.createElement("button");
    b.type = "button";
    b.className = "style-option";
    b.dataset.style = s.id;
    b.setAttribute("role", "radio");
    b.id = `style-option-${s.id}`;

    const apercu = document.createElement("div");
    apercu.className = `apercu apercu-${s.id}`;
    apercu.setAttribute("aria-hidden", "true");
    apercu.innerHTML = "<b>Aa</b><i></i>";

    const titre = document.createElement("div");
    titre.className = "style-titre";
    const nom = document.createElement("strong");
    nom.textContent = s.nom;
    const coche = document.createElement("span");
    coche.className = "style-coche";
    coche.textContent = "✓";
    titre.append(nom, coche);

    const desc = document.createElement("span");
    desc.className = "style-desc";
    desc.textContent = s.desc;

    b.append(apercu, titre, desc);
    b.addEventListener("click", () => appliquerStyle(s.id));
    li.append(b);
    liste.append(li);
  }
  marquerStyleActif(styleActuel());
}

function marquerStyleActif(id) {
  document.querySelectorAll(".style-option").forEach((b) => {
    b.setAttribute("aria-checked", b.dataset.style === id ? "true" : "false");
    b.tabIndex = b.dataset.style === id ? 0 : -1;
  });
}

// Navigation clavier d'un groupe radio : flèches = changer de style.
$("styles-grille").addEventListener("keydown", (e) => {
  const delta = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
  if (!delta) return;
  e.preventDefault();
  const ids = STYLES_VISUELS.map((s) => s.id);
  const suivant = ids[(ids.indexOf(styleActuel()) + delta + ids.length) % ids.length];
  appliquerStyle(suivant);
  $(`style-option-${suivant}`).focus();
});

document.addEventListener("style-change", (e) => marquerStyleActif(e.detail));
construireChoixStyles();
