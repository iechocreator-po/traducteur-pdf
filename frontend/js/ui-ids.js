// Repères d'interface (10/10/2026) — même patron que castellana (js/ui-ids.js) : une lettre
// par onglet, une lettre+numéro par carte (ex. « C3 »), pour pointer précisément un endroit
// de l'app dans un retour sans décrire l'onglet et la section.
//
// Les identifiants sont DÉRIVÉS de l'ordre réel du DOM au chargement (lettres = ordre des
// onglets de module, numéros = ordre des .ds-card DANS chaque module), jamais d'une table
// écrite à la main : une table dupliquée dérive en silence dès qu'on ajoute un onglet ou une
// carte sans penser à la mettre à jour (même piège que SCORE_BANDS/SCREEN_FILTERS chez
// castellana).
//
// Un onglet caché (le Laboratoire hors mode avancé) ou une carte masquée (carte-export, flag)
// compte quand même : sa place dans le DOM ne change pas selon l'état, donc « C3 » désigne
// toujours la même carte. Les cartes des sous-onglets du Laboratoire sont numérotées à la
// suite, dans l'ordre du document.
//
// Ne comptent PAS : les cartes créées dynamiquement (lignes du lot et de « Vos traductions »,
// `.lot-ligne` / `.reprendre-ligne`) — leur nombre change à chaque rendu et décalerait les
// repères de toutes les cartes qui les suivent — ni une carte imbriquée dans une autre.
//
// Rendu en CSS pur (style.css) via data-ui-tab / data-ui-card, lus par un ::before.

const UI_IDS_STORAGE_KEY = "toledo-ui-ids";

// A, B, C … Z, puis AA, AB…
function uiIdLetter(index) {
  let n = index;
  let out = "";
  do {
    out = String.fromCharCode(65 + (n % 26)) + out;
    n = Math.floor(n / 26) - 1;
  } while (n >= 0);
  return out;
}

function uiIdsCartes(panneau) {
  return Array.from(panneau.querySelectorAll(".ds-card")).filter(
    (c) => !c.parentElement.closest(".ds-card") && !c.matches(".lot-ligne, .reprendre-ligne")
  );
}

// Numérote onglets et cartes d'après le DOM actuel — idempotente.
function uiIdsNumber() {
  document.querySelectorAll(".modules-nav > .onglet-module").forEach((btn, i) => {
    const lettre = uiIdLetter(i);
    btn.setAttribute("data-ui-tab", lettre);
    const panneau = document.getElementById("module-" + btn.dataset.module);
    if (!panneau) return;
    uiIdsCartes(panneau).forEach((carte, j) => carte.setAttribute("data-ui-card", lettre + (j + 1)));
  });

  // Réglages : même règle, préfixe « R- » pour ne jamais confondre « R-A1 » (Réglages) avec une
  // carte d'un module.
  document.querySelectorAll("#reglages-overlay .reglages-tab-btn").forEach((btn, i) => {
    const id = "R-" + uiIdLetter(i);
    btn.setAttribute("data-ui-tab", id);
    const panneau = document.getElementById("reglages-tab-" + btn.dataset.stab);
    if (!panneau) return;
    uiIdsCartes(panneau).forEach((carte, j) => carte.setAttribute("data-ui-card", id + (j + 1)));
  });
}

function setUiIdsVisible(visible) {
  document.documentElement.setAttribute("data-ui-ids", visible ? "on" : "off");
  const toggle = document.getElementById("ui-ids-toggle");
  if (toggle) toggle.checked = visible;
  const wrap = document.getElementById("ui-ids-switch");
  if (wrap) wrap.classList.toggle("is-on", visible);
  const state = document.getElementById("ui-ids-state");
  if (state) state.textContent = visible ? "ON" : "OFF";
  try { localStorage.setItem(UI_IDS_STORAGE_KEY, visible ? "on" : "off"); } catch { /* navigation privée */ }
}

function initUiIds() {
  uiIdsNumber();
  let saved = null;
  try { saved = localStorage.getItem(UI_IDS_STORAGE_KEY); } catch { /* navigation privée */ }
  setUiIdsVisible(saved === "on"); // OFF par défaut
}
