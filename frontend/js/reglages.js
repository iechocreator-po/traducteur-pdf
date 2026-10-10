// Réglages (⚙) : panneau plein écran, onglets internes. Même principe que castellana.
// Pour l'instant un seul onglet (« Personnalisation » : repères d'interface) ; la structure
// accueille les suivants sans rien changer ici.

function ouvrirReglages() { $("reglages-overlay").hidden = false; }
function fermerReglages() { $("reglages-overlay").hidden = true; }

function activerOngletReglages(nom) {
  document.querySelectorAll(".reglages-tab-btn").forEach((b) => {
    const actif = b.dataset.stab === nom;
    b.classList.toggle("is-active", actif);
    b.setAttribute("aria-selected", String(actif));
  });
  document.querySelectorAll(".reglages-panneau").forEach((p) => p.classList.toggle("is-active", p.id === "reglages-tab-" + nom));
}

$("bouton-reglages").addEventListener("click", ouvrirReglages);
$("bouton-fermer-reglages").addEventListener("click", fermerReglages);
document.querySelectorAll(".reglages-tab-btn").forEach((b) => {
  b.addEventListener("click", () => activerOngletReglages(b.dataset.stab));
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("reglages-overlay").hidden) fermerReglages();
});
$("ui-ids-toggle").addEventListener("change", (e) => setUiIdsVisible(e.target.checked));

// ── Laboratoire : chaque carte activable unitairement (10/10/2026) ───────────
// La liste est DÉRIVÉE des cartes du Laboratoire (même règle de sélection que les repères
// d'interface, js/ui-ids.js), jamais écrite à la main : ajouter une carte au Laboratoire l'ajoute
// ici sans toucher à ce fichier. L'identité mémorisée est le TITRE de la carte, pas son repère :
// « C4 » change si on insère une carte avant, le titre non.
// Une carte masquée garde son repère (elle compte dans la numérotation, comme une carte cachée
// par un flag).

const LAB_CARTES_STORAGE_KEY = "toledo-lab-cartes-masquees";

function labCartesMasquees() {
  try { return new Set(JSON.parse(localStorage.getItem(LAB_CARTES_STORAGE_KEY) || "[]")); }
  catch { return new Set(); }
}

function labSauverMasquees(ensemble) {
  try { localStorage.setItem(LAB_CARTES_STORAGE_KEY, JSON.stringify([...ensemble])); } catch { /* navigation privée */ }
}

function labTitreCarte(carte) {
  const t = carte.dataset.titre || (carte.querySelector("h2") || {}).textContent || "";
  return t.replace(/\s+/g, " ").trim() || ("Carte " + (carte.dataset.uiCard || ""));
}

function labCartes() {
  return uiIdsCartes($("module-laboratoire"));
}

// Applique l'état mémorisé aux cartes du Laboratoire.
function appliquerCartesLab() {
  const masquees = labCartesMasquees();
  labCartes().forEach((c) => c.classList.toggle("carte-masquee", masquees.has(labTitreCarte(c))));
}

// Reconstruit la liste de commutateurs dans Réglages.
function rendreListeCartesLab() {
  const liste = $("reglages-lab-liste");
  liste.innerHTML = "";
  const masquees = labCartesMasquees();
  labCartes().filter((c) => !c.hidden).forEach((carte, index) => {   // une carte cachée par un flag n'est pas listée
    const titre = labTitreCarte(carte);
    const ligne = document.createElement("div");
    ligne.className = "reglages-lab-ligne";
    const nom = document.createElement("span");
    const repere = document.createElement("span");
    repere.className = "lab-repere";
    repere.textContent = carte.dataset.uiCard || "";
    nom.append(repere, titre);
    // Même composant que « Mode avancé » et « Repères d'interface » (.ui-switch), pas une variante.
    const actif = !masquees.has(titre);
    const id = "lab-toggle-" + index;
    const commutateur = document.createElement("div");
    commutateur.className = "ui-switch" + (actif ? " is-on" : "");
    const etat = document.createElement("span");
    etat.className = "ui-switch-state";
    etat.textContent = actif ? "ON" : "OFF";
    const bouton = document.createElement("label");
    bouton.className = "ui-switch-knob";
    bouton.htmlFor = id;
    bouton.setAttribute("aria-label", `Afficher « ${titre} »`);
    const entree = document.createElement("input");
    entree.type = "checkbox";
    entree.id = id;
    entree.checked = actif;
    entree.addEventListener("change", () => {
      const m = labCartesMasquees();
      if (entree.checked) m.delete(titre); else m.add(titre);
      labSauverMasquees(m);
      appliquerCartesLab();
      rendreListeCartesLab();
    });
    commutateur.append(etat, bouton, entree);
    ligne.append(nom, commutateur);
    liste.appendChild(ligne);
  });
}

$("bouton-lab-tout-afficher").addEventListener("click", () => {
  labSauverMasquees(new Set());
  appliquerCartesLab();
  rendreListeCartesLab();
});

document.addEventListener("flags-charges", rendreListeCartesLab);
appliquerCartesLab();
rendreListeCartesLab();
