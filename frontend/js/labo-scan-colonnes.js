// Laboratoire › onglet « Documents complexes », ex-« Scan multi-colonnes » (10/10/2026).
// Nettoie un PDF scanné dont la couche texte OCR est mal ordonnée : l'OCR est refait
// colonne par colonne côté backend (services/scan_colonnes.py). Aucun LLM.
// Le traitement est un job de la file d'attente (comme la traduction) : on le suit
// par sondage de GET /labo/scan-colonnes/statut.

// ── Sous-onglets du Laboratoire ──────────────────────────────────────────────

function activerPanneauLabo(nom) {
  document.querySelectorAll(".labo-onglet").forEach(b => {
    const actif = b.dataset.laboPanneau === nom;
    b.classList.toggle("is-active", actif);
    b.setAttribute("aria-selected", String(actif));
  });
  document.querySelectorAll(".labo-panneau").forEach(p => {
    p.hidden = p.id !== `labo-panneau-${nom}`;
  });
}

document.querySelectorAll(".labo-onglet").forEach(b => {
  b.addEventListener("click", () => activerPanneauLabo(b.dataset.laboPanneau));
});

// Flag extraction_scan_colonnes (géré par bilbao) : off → l'onglet disparaît et on
// retombe sur « Outils ».
document.addEventListener("flags-charges", () => {
  const visible = featureFlags.extraction_scan_colonnes !== false;
  $("onglet-labo-scan").hidden = !visible;
  if (!visible) activerPanneauLabo("outils");
});

// ── Fichier : chemin saisi ou import par navigateur ─────────────────────────

let scanTimer = null;
let scanNbPages = null;     // nombre de pages du PDF choisi (borne de la navigation de l'aperçu)
let scanSuiviActif = false; // un scan a été vu actif par cette page : à sa fin, on fait défiler jusqu'au résultat

$("bouton-scan-parcourir").addEventListener("click", () => $("scan-fichier").click());

$("scan-fichier").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  const statut = $("scan-upload-statut");
  statut.textContent = `⏳ Envoi de ${file.name}… (un gros scan peut prendre un moment)`;
  const form = new FormData();
  form.append("fichier", file);
  try {
    const rep = await fetch(`${API_BASE}/upload`, { method: "POST", body: form });
    const data = await rep.json().catch(() => ({}));
    if (!rep.ok) throw new Error(data.detail || `Erreur HTTP ${rep.status}`);
    $("scan-chemin").value = data.chemin;
    statut.textContent = `✅ ${file.name} envoyé.`;
    setTimeout(() => { statut.textContent = ""; }, 4000);
    chargerInfoScan(data.chemin);
  } catch (err) {
    statut.textContent = `❌ ${file.name} : ${err.message}`;
  }
});

$("scan-chemin").addEventListener("change", () => {
  const chemin = $("scan-chemin").value.trim();
  if (chemin) chargerInfoScan(chemin);
});

// Nombre de pages + langues OCR réellement installées (lecture seule, rapide).
async function chargerInfoScan(chemin) {
  const info = $("scan-info");
  info.textContent = "";
  scanNbPages = null;
  $("scan-apercu-bloc").hidden = true;    // l'aperçu affiché était celui d'un autre PDF
  try {
    const d = await apiGet(`/labo/scan-colonnes/info?chemin_pdf=${encodeURIComponent(chemin)}`, API_TIMEOUT_LONG_MS);
    if (!d.tesseract) {
      info.textContent = "⚠ Tesseract n'est pas installé : brew install tesseract.";
      return;
    }
    scanNbPages = d.pages;
    info.textContent = `${d.pages} page(s). Astuce : essayez d'abord quelques pages (ex. 1 à 3) avant tout le document.`;
    $("scan-page-fin").max = d.pages;
    const select = $("scan-langue");
    const noms = { eng: "Anglais", fra: "Français", spa: "Espagnol", deu: "Allemand", ita: "Italien", por: "Portugais" };
    const utiles = d.langues.filter(l => !["osd", "snum"].includes(l));
    select.innerHTML = utiles.map(l => `<option value="${l}">${noms[l] || l}</option>`).join("");
    if (utiles.includes("eng")) select.value = "eng";
  } catch (e) {
    info.textContent = `❌ ${e.message}`;
  }
}

// ── Aperçu des régions détectées (géométrie seule, aucun OCR) ────────────────

// Aperçu partagé : le Laboratoire et le parcours « Nettoyer » de l'Accueil (js/parcours.js) l'appellent
// avec leurs propres éléments d'affichage. Renvoie true si l'image est affichée.
async function chargerApercuScan(chemin, page, doublePage, img, zone) {
  zone.innerHTML = "<em>Calcul de l'aperçu…</em>";
  try {
    const url = `${API_BASE}/labo/scan-colonnes/apercu?chemin_pdf=${encodeURIComponent(chemin)}&page=${page}&double_page=${doublePage}&t=${Date.now()}`;
    const rep = await fetch(url);
    if (!rep.ok) {
      const data = await rep.json().catch(() => ({}));
      throw new Error(data.detail || `Erreur HTTP ${rep.status}`);
    }
    const blob = await rep.blob();
    if (img.dataset.url) URL.revokeObjectURL(img.dataset.url);
    img.dataset.url = URL.createObjectURL(blob);
    img.src = img.dataset.url;
    img.hidden = false;
    zone.innerHTML = `<span class="aide">Page ${page}.</span>`;
    return true;
  } catch (e) {
    img.hidden = true;
    zone.innerHTML = `<span class="erreur">Erreur : ${e.message}</span>`;
    return false;
  }
}

// Aperçu du Laboratoire : zone à lui (sous le résultat, qui reste visible) et navigation ‹ › page par page.
// Les boutons sont désactivés pendant un calcul : une réponse lente ne peut pas écraser une page plus récente.
async function afficherApercuLabo(page) {
  const chemin = $("scan-chemin").value.trim();
  if (!chemin) { alert("Indiquez d'abord le chemin du PDF, ou utilisez Parcourir…"); return; }
  const max = scanNbPages || Infinity;
  page = Math.min(Math.max(1, page || 1), max);
  $("scan-apercu-page").value = page;
  if (scanNbPages) { $("scan-apercu-page").max = scanNbPages; $("scan-apercu-total").textContent = `sur ${scanNbPages}`; }
  $("scan-apercu-bloc").hidden = false;
  const nav = [$("scan-apercu-prec"), $("scan-apercu-suiv"), $("scan-apercu-page")];
  nav.forEach(el => { el.disabled = true; });
  masquerEditeurRegions();
  const doublePage = $("scan-double-page").value;
  const [ok, geo] = await Promise.all([
    chargerApercuScan(chemin, page, doublePage, $("scan-apercu-image"), $("scan-apercu-statut")),
    apiGet(`/labo/scan-colonnes/geometrie?chemin_pdf=${encodeURIComponent(chemin)}&page=${page}&double_page=${doublePage}`,
           API_TIMEOUT_LONG_MS).catch(() => null),
  ]);
  if (ok && geo) afficherEditeurRegions(chemin, page, geo);
  nav.forEach(el => { el.disabled = false; });
  $("scan-apercu-prec").disabled = page <= 1;
  $("scan-apercu-suiv").disabled = page >= max;
}

// ── Réglage manuel des régions d'une page (niveaux 1 et 2) ───────────────────
// Niveau 1 : un mode par demi-page (auto, 1 à 3 colonnes, recopier comme image, ignorer).
// Niveau 2 : gouttière et bords de colonnes déplaçables, illustrations retirables.
// Le backend reste la seule source : chaque geste envoie le réglage complet de la page
// (PUT /labo/scan-colonnes/reglage-page), puis l'aperçu ET la géométrie sont recalculés par lui.
// Coordonnées : pixels de l'image de l'aperçu (rendu 200 dpi), via le viewBox du SVG.

const SVG_NS = "http://www.w3.org/2000/svg";
const COULEURS_REGIONS = { rouge: "rgb(220, 0, 0)", vert: "rgb(0, 160, 0)", violet: "rgb(170, 0, 200)" };
const LARGEUR_MIN_COLONNE = 30;     // = scan_colonnes.LARGEUR_MIN_COLONNE
let editeurRegions = null;          // { chemin, page, geo, reglage }

function masquerEditeurRegions() {
  editeurRegions = null;
  $("scan-regions").hidden = true;
  $("scan-apercu-svg").setAttribute("hidden", "");   // un <svg> n'a pas la propriété .hidden des éléments HTML
  $("scan-regions-aide").hidden = true;
}

// Réglage de départ = celui en vigueur, complété pour avoir une entrée par demi-page.
function reglageInitial(geo) {
  const r = geo.reglage || {};
  const moities = geo.moities.map((m, i) => (r.moities && r.moities.length === geo.moities.length && r.moities[i])
    || { mode: "auto" });
  return { gouttiere: r.gouttiere, moities, illustrations_retirees: [...(r.illustrations_retirees || [])] };
}

function libelleMoitie(geo, i) {
  return geo.moities.length === 1 ? "Page" : (i === 0 ? "Page de gauche" : "Page de droite");
}

function valeurMode(m) {
  if (m.mode !== "colonnes") return m.mode;
  return `c${m.colonnes.length}`;
}

function afficherEditeurRegions(chemin, page, geo) {
  editeurRegions = { chemin, page, geo, reglage: reglageInitial(geo) };
  // Menus de mode
  const modes = $("scan-regions-modes");
  modes.innerHTML = "";
  geo.moities.forEach((m, i) => {
    const label = document.createElement("label");
    const n = m.colonnes.length;
    const options = [["auto", "Automatique"], ["c1", "1 colonne"], ["c2", "2 colonnes"], ["c3", "3 colonnes"],
                     ["image", "Recopier comme image"], ["ignorer", "Ignorer (ni lue ni recopiée)"]];
    if (m.mode === "colonnes" && n > 3) options.splice(4, 0, [`c${n}`, `${n} colonnes`]);
    label.innerHTML = `${libelleMoitie(geo, i)} :`;
    const select = document.createElement("select");
    select.className = "ds-select";
    select.innerHTML = options.map(([v, t]) => `<option value="${v}">${t}</option>`).join("");
    select.value = valeurMode(m);
    select.addEventListener("change", () => changerModeMoitie(i, select.value));
    label.appendChild(select);
    modes.appendChild(label);
  });
  // Alertes de la détection (page à vérifier) et état du réglage
  const alertes = geo.moities.flatMap((m, i) => m.alertes.map(a => `${libelleMoitie(geo, i)} : ${a}`));
  $("scan-regions-alertes").hidden = !alertes.length;
  $("scan-regions-alertes").textContent = alertes.length ? `⚠ À vérifier — ${alertes.join(" · ")}.` : "";
  $("scan-regions-etat").textContent = geo.reglage
    ? "✋ Régions réglées à la main pour cette page."
    : "Détection automatique.";
  $("scan-regions-auto").hidden = !geo.reglage;
  $("scan-regions-auto").disabled = false;     // désactivé pendant l'enregistrement qui a précédé ce rendu
  $("scan-regions").hidden = false;
  $("scan-regions-aide").hidden = false;
  dessinerPoignees();
}

function changerModeMoitie(i, valeur) {
  const { geo, reglage } = editeurRegions;
  const m = geo.moities[i];
  if (valeur.startsWith("c")) {
    // N colonnes égales dans la demi-page, marges de 5 % et gouttières de 2 % : un point de départ à ajuster.
    const n = parseInt(valeur.slice(1), 10);
    const largeur = m.x1 - m.x0, marge = largeur * 0.05, ecart = largeur * 0.02;
    const pas = (largeur - 2 * marge - (n - 1) * ecart) / n;
    reglage.moities[i] = { mode: "colonnes", colonnes: Array.from({ length: n }, (_, k) => {
      const a = m.x0 + marge + k * (pas + ecart);
      return [Math.round(a), Math.round(a + pas)];
    }) };
  } else {
    reglage.moities[i] = { mode: valeur };
  }
  enregistrerRegions(reglage);
}

async function enregistrerRegions(reglage) {
  const { chemin, page } = editeurRegions;
  $("scan-regions-etat").textContent = "⏳ Enregistrement…";
  document.querySelectorAll("#scan-regions select, #scan-regions button").forEach(el => { el.disabled = true; });
  try {
    const rep = await fetch(`${API_BASE}/labo/scan-colonnes/reglage-page`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chemin_pdf: chemin, page, reglage }),
    });
    const data = await rep.json().catch(() => ({}));
    if (!rep.ok) throw new Error(data.detail || `Erreur HTTP ${rep.status}`);
    await afficherApercuLabo(page);
  } catch (e) {
    $("scan-regions-etat").innerHTML = `<span class="erreur">Erreur : ${e.message}</span>`;
    document.querySelectorAll("#scan-regions select, #scan-regions button").forEach(el => { el.disabled = false; });
  }
}

$("scan-regions-auto").addEventListener("click", () => {
  if (editeurRegions) enregistrerRegions(null);
});

function elementSvg(nom, attributs, parent) {
  const el = document.createElementNS(SVG_NS, nom);
  Object.entries(attributs).forEach(([k, v]) => el.setAttribute(k, v));
  if (parent) parent.appendChild(el);
  return el;
}

// Une poignée = un trait vertical (large zone de prise invisible + trait visible + rond au milieu).
// `bornes()` donne [min, max] autorisés au moment du geste ; `valider(x)` enregistre la position finale.
function poignee(svg, x, couleur, libelle, bornes, valider) {
  const { hauteur } = editeurRegions.geo;
  const g = elementSvg("g", { class: "scan-poignee", tabindex: "0", role: "slider", "aria-label": libelle,
                              "aria-valuenow": String(x) }, svg);
  const zone = elementSvg("line", { class: "zone", x1: x, x2: x, y1: 0, y2: hauteur, "stroke-width": 36 }, g);
  const trait = elementSvg("line", { x1: x, x2: x, y1: 0, y2: hauteur, stroke: couleur, "stroke-width": 3,
                                     "stroke-dasharray": "14 10" }, g);
  const rond = elementSvg("circle", { class: "grip", cx: x, cy: hauteur / 2, r: 16, fill: "white", stroke: couleur,
                                      "stroke-width": 5 }, g);
  let pos = x, minuteur = null;
  const placer = (nx) => {
    const [mn, mx] = bornes();
    pos = Math.round(Math.min(Math.max(nx, mn), mx));
    [zone, trait].forEach(l => { l.setAttribute("x1", pos); l.setAttribute("x2", pos); });
    rond.setAttribute("cx", pos);
    g.setAttribute("aria-valuenow", String(pos));
  };
  const versImage = (clientX) => {
    const r = svg.getBoundingClientRect();
    return (clientX - r.left) * editeurRegions.geo.largeur / r.width;
  };
  g.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    g.setPointerCapture(e.pointerId);
    g.classList.add("glisse");
  });
  g.addEventListener("pointermove", (e) => { if (g.hasPointerCapture(e.pointerId)) placer(versImage(e.clientX)); });
  g.addEventListener("pointerup", (e) => {
    if (!g.hasPointerCapture(e.pointerId)) return;
    g.releasePointerCapture(e.pointerId);
    g.classList.remove("glisse");
    if (pos !== x) valider(pos);
  });
  g.addEventListener("keydown", (e) => {
    if (!["ArrowLeft", "ArrowRight"].includes(e.key)) return;
    e.preventDefault();
    placer(pos + (e.key === "ArrowLeft" ? -1 : 1) * (e.shiftKey ? 20 : 4));
    clearTimeout(minuteur);       // le clavier enregistre après une courte pause, pas à chaque flèche
    minuteur = setTimeout(() => { if (pos !== x) valider(pos); }, 700);
  });
}

function dessinerPoignees() {
  const { geo, reglage } = editeurRegions;
  const svg = $("scan-apercu-svg");
  svg.innerHTML = "";
  svg.setAttribute("viewBox", `0 0 ${geo.largeur} ${geo.hauteur}`);
  svg.setAttribute("preserveAspectRatio", "none");
  svg.removeAttribute("hidden");
  // Gouttière (double page)
  if (geo.gouttiere !== null) {
    poignee(svg, geo.gouttiere, COULEURS_REGIONS.rouge, "Gouttière",
      () => [geo.largeur * 0.2, geo.largeur * 0.8],
      (x) => { reglage.gouttiere = x; enregistrerRegions(reglage); });
  }
  // Bords des colonnes : déplacer un bord en mode automatique fige les colonnes de la demi-page à la main.
  geo.moities.forEach((m, i) => {
    if (!["auto", "colonnes"].includes(m.mode)) return;
    const cols = m.colonnes.map(c => [...c]);
    cols.forEach((c, k) => {
      [0, 1].forEach((bord) => {
        const precedente = k > 0 ? cols[k - 1][1] : m.x0;
        const suivante = k < cols.length - 1 ? cols[k + 1][0] : m.x1;
        const bornes = () => bord === 0 ? [precedente, c[1] - LARGEUR_MIN_COLONNE] : [c[0] + LARGEUR_MIN_COLONNE, suivante];
        poignee(svg, c[bord], COULEURS_REGIONS.vert,
          `${libelleMoitie(geo, i)}, colonne ${k + 1}, bord ${bord === 0 ? "gauche" : "droit"}`, bornes,
          (x) => {
            cols[k][bord] = x;
            reglage.moities[i] = { mode: "colonnes", colonnes: cols };
            enregistrerRegions(reglage);
          });
      });
    });
  });
  // Illustrations retirables (pas celle d'une demi-page « recopiée comme image » : c'est le menu qui la règle).
  geo.illustrations.forEach(([x0, y0, x1, y1]) => {
    const moitie = geo.moities.find(m => m.x0 <= (x0 + x1) / 2 && (x0 + x1) / 2 < m.x1);
    if (moitie && moitie.mode === "image") return;
    const b = elementSvg("g", { class: "scan-retirer", tabindex: "0", role: "button",
                                "aria-label": "Retirer cette illustration (son contenu sera lu comme du texte)" }, svg);
    elementSvg("title", {}, b).textContent = "Retirer cette illustration";
    elementSvg("circle", { cx: x1 - 26, cy: y0 + 26, r: 20, fill: "white", stroke: COULEURS_REGIONS.violet, "stroke-width": 5 }, b);
    const t = elementSvg("text", { x: x1 - 26, y: y0 + 26, "text-anchor": "middle", "dominant-baseline": "central",
                                   "font-size": 30, "font-weight": 700, fill: COULEURS_REGIONS.violet }, b);
    t.textContent = "×";
    const retirer = () => {
      reglage.illustrations_retirees.push([x0, y0, x1, y1]);
      enregistrerRegions(reglage);
    };
    b.addEventListener("click", retirer);
    b.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); retirer(); } });
  });
}

$("bouton-scan-apercu").addEventListener("click", () =>
  afficherApercuLabo(parseInt($("scan-page-debut").value, 10) || 1));
$("scan-apercu-prec").addEventListener("click", () =>
  afficherApercuLabo((parseInt($("scan-apercu-page").value, 10) || 1) - 1));
$("scan-apercu-suiv").addEventListener("click", () =>
  afficherApercuLabo((parseInt($("scan-apercu-page").value, 10) || 1) + 1));
$("scan-apercu-page").addEventListener("change", () =>
  afficherApercuLabo(parseInt($("scan-apercu-page").value, 10) || 1));
$("scan-double-page").addEventListener("change", () => {
  if (!$("scan-apercu-bloc").hidden) afficherApercuLabo(parseInt($("scan-apercu-page").value, 10) || 1);
});

// ── Lancement et suivi du job ────────────────────────────────────────────────

function rendreProgressionScan(etat) {
  const zone = $("scan-progression");
  if (!etat) { zone.innerHTML = ""; return; }
  const pct = etat.pages_total ? Math.round((etat.pages_faites / etat.pages_total) * 100) : 0;
  const libelles = { en_attente: "En attente dans la file…", en_cours: "Traitement en cours", termine: "Terminé",
                     erreur: "Terminé avec des erreurs", annule: "Annulé" };
  let html = `<p><strong>${libelles[etat.statut] || etat.statut}</strong> — ${etat.pages_faites}/${etat.pages_total} page(s)`;
  if (etat.temps_ecoule_secondes) html += ` · ${formaterDuree(etat.temps_ecoule_secondes)}`;
  html += `</p><div class="barre-conteneur"><div class="barre-progression ${etat.statut ==="termine" ? "barre-succes" : ""}" style="width:${pct}%"></div></div>`;
  zone.innerHTML = html;
}

function rendreResultatScan(etat) {
  const zone = $("scan-resultat");
  if (!["termine", "erreur"].includes(etat.statut)) return;
  let html = "";
  if (etat.statut === "termine") {
    html += `<p>✅ ${etat.titres} titre(s) détecté(s). Fichier écrit : <code>${etat.chemin_sortie}</code></p>`;
  } else {
    html += `<p class="erreur">⚠ Terminé avec ${etat.pages_en_erreur.length} page(s) en erreur — le fichier reste écrit avec les autres pages ; relance pour réessayer seulement celles-là.</p>`;
    html += "<ul>" + etat.pages_en_erreur.slice(0, 10).map(p => `<li>Page ${p.page} : ${p.erreur}</li>`).join("") + "</ul>";
    if (etat.erreur) html += `<p class="erreur">${etat.erreur}</p>`;
  }
  // Le .md n'est pas dans la Bibliothèque (rien n'est traduit) : on le propose en un clic comme
  // source de « Nouveau document » (analyse, traduction). Écrit même si certaines pages ont échoué.
  if (!etat.erreur) {
    html += `<p><button id="bouton-scan-envoyer" class="ds-btn ds-btn--primary">Envoyer vers « Nouveau document »</button></p>`;
  }
  if ((etat.reparations || []).length) {
    html += `<details><summary>${etat.reparations.length} réparation(s) automatique(s) à vérifier</summary><ul>` +
            etat.reparations.map(r => `<li>${r}</li>`).join("") + "</ul></details>";
  }
  zone.innerHTML = html;
  const envoyer = $("bouton-scan-envoyer");
  if (envoyer) {
    envoyer.addEventListener("click", () => {
      document.dispatchEvent(new CustomEvent("ajouter-au-lot", { detail: etat.chemin_sortie }));
      activerModule("import");
    });
  }
  if (enchainerScanTermine(etat)) {
    zone.insertAdjacentHTML("beforeend", `<p class="aide">➜ Envoyé à l'Accueil : la traduction démarre dès l'analyse terminée.</p>`);
  } else if ($("scan-puis-traduire").checked && etat.statut === "erreur") {
    zone.insertAdjacentHTML("beforeend", `<p class="aide">La traduction automatique n'a pas été lancée : le scan contient des pages en erreur. Relancez le scan, ou envoyez le fichier à la main.</p>`);
  }
}


// Enchaînement scan → traduction (case cochée). Source unique : le Laboratoire et le parcours sondent tous
// deux le même scan, et chacun appelle cette fonction à chaque état reçu.
//   - Il ne part que sur une TRANSITION observée (scan vu actif, puis terminé) : recharger la page ou cocher
//     la case sur un scan déjà fini ne relance jamais une traduction, le bouton de l'Accueil reste là pour ça.
//   - Seulement sans page en erreur : un scan partiel, ce serait traduire un texte troué.
//   - Langues et modèle sont ceux du LANCEMENT du scan, pas des menus à la fin d'un OCR de plusieurs heures.
//   - Le PDF brut quitte le lot, pour ne jamais être traduit sans nettoyage.
const SCAN_REGLAGES_KEY = "toledo-scan-reglages";
const scansVusActifs = new Set();

function lireReglagesScan() {
  try { return JSON.parse(localStorage.getItem(SCAN_REGLAGES_KEY)) || {}; } catch { return {}; }
}

function ecrireReglagesScan(reglages) {
  try { localStorage.setItem(SCAN_REGLAGES_KEY, JSON.stringify(reglages)); } catch { /* navigation privée */ }
}

function enchainerScanTermine(etat) {
  if (!etat) return false;
  if (["en_attente", "en_cours"].includes(etat.statut)) { scansVusActifs.add(etat.chemin_pdf); return false; }
  if (!scansVusActifs.delete(etat.chemin_pdf)) return false;
  const reglages = lireReglagesScan();
  const figes = reglages[etat.chemin_pdf] || null;
  delete reglages[etat.chemin_pdf];
  ecrireReglagesScan(reglages);
  if (etat.statut !== "termine" || etat.erreur || (etat.pages_en_erreur || []).length
      || !$("scan-puis-traduire").checked) return false;
  document.dispatchEvent(new CustomEvent("ajouter-au-lot-et-traduire", { detail: { chemin: etat.chemin_sortie, reglages: figes } }));
  document.dispatchEvent(new CustomEvent("retirer-du-lot", { detail: etat.chemin_pdf }));
  return true;
}

try { $("scan-puis-traduire").checked = localStorage.getItem("toledo-scan-puis-traduire") === "1"; } catch { /* navigation privée */ }
$("scan-puis-traduire").addEventListener("change", (e) => {
  try { localStorage.setItem("toledo-scan-puis-traduire", e.target.checked ? "1" : "0"); } catch { /* navigation privée */ }
});

async function suivreScan(chemin) {
  clearTimeout(scanTimer);
  try {
    const etat = await apiGet(`/labo/scan-colonnes/statut?chemin_pdf=${encodeURIComponent(chemin)}`);
    rendreProgressionScan(etat);
    if (etat && ["en_attente", "en_cours"].includes(etat.statut)) {
      enchainerScanTermine(etat);       // note que ce scan est actif
      scanSuiviActif = true;
      scanTimer = setTimeout(() => suivreScan(chemin), 3000);
      return;
    }
    if (etat) rendreResultatScan(etat);
    // Le résultat (et son bouton « Envoyer ») apparaît sous les réglages : on l'amène à l'écran.
    if (etat && scanSuiviActif && !$("scan-resultat").hidden) {
      $("scan-resultat").scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
    scanSuiviActif = false;
    $("bouton-scan-lancer").disabled = false;
  } catch (e) {
    // backend momentanément injoignable : on réessaie plutôt que d'afficher un faux « terminé »
    scanTimer = setTimeout(() => suivreScan(chemin), 5000);
  }
}

// Point d'entrée UNIQUE du nettoyage : le bouton du Laboratoire et le parcours « Nettoyer un document »
// de l'Accueil (js/parcours.js) passent tous deux ici, avec les mêmes champs (#scan-*). C'est ce qui
// garantit le même fichier en sortie, quel que soit le chemin emprunté. Rend le message d'erreur, ou null :
// lancé depuis l'Accueil, le Laboratoire est masqué et l'appelant doit pouvoir l'afficher.
async function lancerScan() {
  const chemin = $("scan-chemin").value.trim();
  if (!chemin) { alert("Indiquez d'abord le chemin du PDF, ou utilisez Parcourir…"); return null; }
  const corps = { chemin_pdf: chemin, langue_ocr: $("scan-langue").value || "eng", double_page: $("scan-double-page").value };
  const debut = parseInt($("scan-page-debut").value, 10);
  const fin = parseInt($("scan-page-fin").value, 10);
  if (debut) corps.page_debut = debut;
  if (fin) corps.page_fin = fin;
  $("scan-resultat").innerHTML = "";
  $("bouton-scan-lancer").disabled = true;
  try {
    await apiPost("/labo/scan-colonnes", corps, API_TIMEOUT_LONG_MS);
    scansVusActifs.add(chemin);
    ecrireReglagesScan({ ...lireReglagesScan(), [chemin]: {
      langue_source: $("langue-source").value, langue_cible: $("langue-cible").value, modele: $("modele").value,
    } });
    suivreScan(chemin);
    return null;
  } catch (e) {
    $("bouton-scan-lancer").disabled = false;
    $("scan-resultat").innerHTML = `<span class="erreur">Erreur : ${e.message}</span>`;
    return e.message;
  }
}

$("bouton-scan-lancer").addEventListener("click", lancerScan);

