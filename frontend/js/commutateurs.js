// ── Réglages (⚙) › Personnalisation › « Commutateurs » : format des boutons ON/OFF ─────────────────────────
// Le choix pose <html data-commutateur="…"> ; l'habillage est du pur CSS (css/commutateurs.css).
// « rond » = le bouton d'origine : pas d'attribut. Un script inline du <head> le réapplique avant le premier rendu.

const COMMUTATEURS = [
  { id: "rond", nom: "Bouton rond", desc: "Le bouton actuel : pastille quadrillée qui s'illumine." },
  { id: "machine", nom: "Machine à écrire", desc: "Touche ronde cerclée de chrome qui s'enfonce : O relevée, I enfoncée." },
  { id: "disco", nom: "Boule disco", desc: "Boule à miroirs : éteinte elle est terne, allumée elle tourne et fait flasher ses facettes." },
  { id: "usine", nom: "Switch d'usine", desc: "Plaque ON rouge / OFF noire et levier chromé, comme sur une machine industrielle." },
];
const COMMUTATEUR_KEY = "toledo-commutateur";

function commutateurActuel() {
  return document.documentElement.getAttribute("data-commutateur") || "rond";
}

function appliquerCommutateur(id) {
  if (!COMMUTATEURS.some((c) => c.id === id)) id = "rond";
  if (id === "rond") document.documentElement.removeAttribute("data-commutateur");
  else document.documentElement.setAttribute("data-commutateur", id);
  try { localStorage.setItem(COMMUTATEUR_KEY, id); } catch { /* navigation privée */ }
  document.querySelectorAll(".commut-option").forEach((b) => {
    b.setAttribute("aria-checked", b.dataset.commutateur === id ? "true" : "false");
    b.tabIndex = b.dataset.commutateur === id ? 0 : -1;
  });
}

// Mini-commutateur d'aperçu : même balisage que les vrais, sans <input>, figé sur ON ou OFF.
function apercuCommutateur(variante, actif) {
  const bloc = document.createElement("div");
  bloc.className = "ui-switch" + (actif ? " is-on" : "");
  bloc.dataset.v = variante;   // toujours posé, « rond » compris : sinon l'aperçu suit le format de <html>
  const etat = document.createElement("span");
  etat.className = "ui-switch-state";
  etat.textContent = actif ? "ON" : "OFF";
  const bouton = document.createElement("span");
  bouton.className = "ui-switch-knob";
  bloc.append(etat, bouton);
  return bloc;
}

function construireChoixCommutateurs() {
  const liste = $("commutateurs-grille");
  liste.replaceChildren();
  for (const c of COMMUTATEURS) {
    const li = document.createElement("li");
    const b = document.createElement("button");
    b.type = "button";
    b.className = "style-option commut-option";
    b.dataset.commutateur = c.id;
    b.setAttribute("role", "radio");
    b.id = `commut-option-${c.id}`;

    const apercu = document.createElement("div");
    apercu.className = "commut-apercu";
    apercu.setAttribute("aria-hidden", "true");
    apercu.append(apercuCommutateur(c.id, false), apercuCommutateur(c.id, true));

    const titre = document.createElement("div");
    titre.className = "style-titre";
    const nom = document.createElement("strong");
    nom.textContent = c.nom;
    const coche = document.createElement("span");
    coche.className = "style-coche";
    coche.textContent = "✓";
    titre.append(nom, coche);

    const desc = document.createElement("span");
    desc.className = "style-desc";
    desc.textContent = c.desc;

    b.append(apercu, titre, desc);
    b.addEventListener("click", () => appliquerCommutateur(c.id));
    li.append(b);
    liste.append(li);
  }
  appliquerCommutateur(commutateurActuel());
}

$("commutateurs-grille").addEventListener("keydown", (e) => {
  const delta = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
  if (!delta) return;
  e.preventDefault();
  const ids = COMMUTATEURS.map((c) => c.id);
  const suivant = ids[(ids.indexOf(commutateurActuel()) + delta + ids.length) % ids.length];
  appliquerCommutateur(suivant);
  $(`commut-option-${suivant}`).focus();
});

construireChoixCommutateurs();
