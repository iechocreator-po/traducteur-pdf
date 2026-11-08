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
// `edition` : l'éditeur de régions dessine lui-même illustrations et zones (déplaçables), le PNG les omet.
async function chargerApercuScan(chemin, page, doublePage, img, zone, edition = false) {
  zone.innerHTML = "<em>Calcul de l'aperçu…</em>";
  try {
    const url = `${API_BASE}/labo/scan-colonnes/apercu?chemin_pdf=${encodeURIComponent(chemin)}&page=${page}&double_page=${doublePage}${edition ? "&edition=true" : ""}&t=${Date.now()}`;
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
    chargerApercuScan(chemin, page, doublePage, $("scan-apercu-image"), $("scan-apercu-statut"), true),
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
  return { gouttiere: r.gouttiere, moities, illustrations_retirees: [...(r.illustrations_retirees || [])],
           zones: (r.zones || []).map(z => ({ type: z.type, rect: [...z.rect] })) };
}

function libelleMoitie(geo, i) {
  return geo.moities.length === 1 ? "Page" : (i === 0 ? "Page de gauche" : "Page de droite");
}

function valeurMode(m) {
  if (m.mode !== "colonnes") return m.mode;
  return `c${m.colonnes.length}`;
}

function afficherEditeurRegions(chemin, page, geo) {
  // La sélection survit à l'enregistrement qui a provoqué ce rendu (même page du même PDF).
  const garder = editeurRegions === null && selectionEnAttente && selectionEnAttente.chemin === chemin
    && selectionEnAttente.page === page ? selectionEnAttente.selection : null;
  selectionEnAttente = null;
  editeurRegions = { chemin, page, geo, reglage: reglageInitial(geo), selection: garder, outil: null };
  if (garder && garder.source === "zone" && !editeurRegions.reglage.zones[garder.index]) editeurRegions.selection = null;
  if (garder && garder.source === "detectee"
      && !geo.illustrations.some(r => r.every((v, k) => v === garder.rect[k]))) editeurRegions.selection = null;
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
  construireOutilsZones();
  $("scan-apercu-svg").classList.remove("dessin");
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
  const { chemin, page, selection } = editeurRegions;
  selectionEnAttente = { chemin, page, selection: selection || null };
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
  dessinerZones(svg);
}

// ── Zones (niveau 3) : illustrations détectées et zones dessinées à la main ──
// Une zone détectée qu'on recadre ou dont on change le type devient une zone manuelle : la détection est
// retirée (illustrations_retirees) et la zone ajoutée à reglage.zones. Le backend fait le reste.

const TYPES_ZONE = [
  ["illustration", "Illustration", "violet", "recopiée telle quelle, jamais lue"],
  ["titre", "Titre", "orange", "lue et rendue en titre (un chapitre)"],
  ["texte", "Texte", "bleu", "lue à part, en paragraphes (ex. un tableau sur deux colonnes)"],
  ["conserver", "Ne pas traduire", "turquoise", "lue, mais recopiée dans sa langue d'origine"],
  ["ignorer", "Ignorer", "gris", "ni lue ni recopiée"],
];
const COULEURS_ZONES = { illustration: "rgb(170, 0, 200)", titre: "rgb(240, 140, 0)", texte: "rgb(0, 120, 200)",
                         conserver: "rgb(0, 150, 140)", ignorer: "rgb(110, 110, 110)" };   // = scan_colonnes.COULEURS_ZONES
const COTE_MIN_ZONE = 10;           // = scan_colonnes.COTE_MIN_ZONE
let selectionEnAttente = null;      // sélection à rétablir après le rendu qui suit un enregistrement

const libelleType = (type) => (TYPES_ZONE.find(t => t[0] === type) || [type, type])[1];

// Zones affichées : [{ source: "zone", index } | { source: "detectee", rect }, type, rect]
function zonesAffichees() {
  const { geo, reglage } = editeurRegions;
  const detectees = geo.illustrations.filter(([x0, , x1]) => {
    const m = geo.moities.find(mm => mm.x0 <= (x0 + x1) / 2 && (x0 + x1) / 2 < mm.x1);
    return !(m && m.mode === "image");      // l'encre d'une demi-page « image » : c'est le menu qui la règle
  }).map(r => ({ ref: { source: "detectee", rect: r }, type: "illustration", rect: r }));
  const manuelles = reglage.zones.map((z, index) => ({ ref: { source: "zone", index }, type: z.type, rect: z.rect }));
  return [...detectees, ...manuelles];
}

const memeRef = (a, b) => !!a && !!b && a.source === b.source
  && (a.source === "zone" ? a.index === b.index : a.rect.every((v, k) => v === b.rect[k]));

function versImageXY(svg, e) {
  const r = svg.getBoundingClientRect(), { largeur, hauteur } = editeurRegions.geo;
  return [Math.min(Math.max((e.clientX - r.left) * largeur / r.width, 0), largeur),
          Math.min(Math.max((e.clientY - r.top) * hauteur / r.height, 0), hauteur)];
}

const rectNormal = ([x0, y0, x1, y1]) =>
  [Math.round(Math.min(x0, x1)), Math.round(Math.min(y0, y1)), Math.round(Math.max(x0, x1)), Math.round(Math.max(y0, y1))];

// Applique une modification (nouveau rect et/ou type, ou suppression) à la zone `ref`, puis enregistre.
function modifierZone(ref, { rect, type, supprimer = false }) {
  const { reglage } = editeurRegions;
  if (ref.source === "detectee") {
    reglage.illustrations_retirees.push([...ref.rect]);
    if (supprimer) { editeurRegions.selection = null; enregistrerRegions(reglage); return; }
    reglage.zones.push({ type: type || "illustration", rect: rect || [...ref.rect] });
    editeurRegions.selection = { source: "zone", index: reglage.zones.length - 1 };
  } else if (supprimer) {
    reglage.zones.splice(ref.index, 1);
    editeurRegions.selection = null;
  } else {
    const z = reglage.zones[ref.index];
    reglage.zones[ref.index] = { type: type || z.type, rect: rect || z.rect };
  }
  enregistrerRegions(reglage);
}

function selectionner(ref) {
  editeurRegions.selection = ref;
  dessinerPoignees();
  majPanneauZone();
}

function dessinerZones(svg) {
  const { selection, outil } = editeurRegions;
  const liste = zonesAffichees();
  liste.forEach(({ ref, type, rect }) => {
    const [x0, y0, x1, y1] = rect, couleur = COULEURS_ZONES[type];
    const choisie = memeRef(ref, selection);
    const manuelle = ref.source === "zone";
    const g = elementSvg("g", { class: `scan-zone${choisie ? " choisie" : ""}`, tabindex: "0", role: "button",
      "aria-label": `${manuelle ? "Zone" : "Illustration détectée"} « ${libelleType(type)} »${choisie ? ", sélectionnée" : ""}. `
        + "Flèches : déplacer ; Suppr : supprimer." }, svg);
    const cadre = elementSvg("rect", { x: x0, y: y0, width: x1 - x0, height: y1 - y0, fill: couleur,
      "fill-opacity": manuelle ? 0.18 : 0.04, stroke: couleur, "stroke-width": choisie ? 8 : 5,
      "stroke-dasharray": manuelle ? "" : "18 10" }, g);
    if (manuelle) {
      const t = elementSvg("text", { x: x0 + 10, y: y0 + 34, "font-size": 30, "font-weight": 700, fill: couleur,
                                     class: "etiquette" }, g);
      t.textContent = libelleType(type);
    }
    // Déplacer : glisser le corps de la zone (un simple clic la sélectionne).
    let depart = null, minuteur = null, courant = [...rect];
    const poser = (r) => {
      courant = r;
      cadre.setAttribute("x", r[0]); cadre.setAttribute("y", r[1]);
      g.querySelectorAll("text.etiquette").forEach(t => { t.setAttribute("x", r[0] + 10); t.setAttribute("y", r[1] + 34); });
    };
    g.addEventListener("pointerdown", (e) => {
      if (editeurRegions.outil) return;
      e.preventDefault(); e.stopPropagation();
      g.setPointerCapture(e.pointerId);
      depart = { xy: versImageXY(svg, e), rect: [...rect] };
    });
    g.addEventListener("pointermove", (e) => {
      if (!depart) return;
      const [x, y] = versImageXY(svg, e), { largeur, hauteur } = editeurRegions.geo;
      const dx = Math.min(Math.max(x - depart.xy[0], -depart.rect[0]), largeur - depart.rect[2]);
      const dy = Math.min(Math.max(y - depart.xy[1], -depart.rect[1]), hauteur - depart.rect[3]);
      poser([depart.rect[0] + dx, depart.rect[1] + dy, depart.rect[2] + dx, depart.rect[3] + dy].map(Math.round));
    });
    g.addEventListener("pointerup", (e) => {
      if (!depart) return;
      if (g.hasPointerCapture(e.pointerId)) g.releasePointerCapture(e.pointerId);
      depart = null;
      const bouge = courant.some((v, k) => Math.abs(v - rect[k]) >= 2);
      if (bouge) { editeurRegions.selection = ref; modifierZone(ref, { rect: courant }); }
      else if (!choisie) selectionner(ref);
    });
    g.addEventListener("keydown", (e) => {
      if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); modifierZone(ref, { supprimer: true }); return; }
      if (e.key === "Escape") { e.preventDefault(); selectionner(null); return; }
      if ((e.key === "Enter" || e.key === " ") && !choisie) { e.preventDefault(); selectionner(ref); return; }
      const pas = e.shiftKey ? 20 : 4;
      const d = { ArrowLeft: [-pas, 0], ArrowRight: [pas, 0], ArrowUp: [0, -pas], ArrowDown: [0, pas] }[e.key];
      if (!d) return;
      e.preventDefault();
      const { largeur, hauteur } = editeurRegions.geo;
      const dx = Math.min(Math.max(d[0], -courant[0]), largeur - courant[2]);
      const dy = Math.min(Math.max(d[1], -courant[1]), hauteur - courant[3]);
      poser([courant[0] + dx, courant[1] + dy, courant[2] + dx, courant[3] + dy]);
      clearTimeout(minuteur);      // comme les lignes : enregistré après une courte pause
      minuteur = setTimeout(() => { editeurRegions.selection = ref; modifierZone(ref, { rect: courant }); }, 700);
    });
    // × : supprimer (une illustration détectée retirée est lue comme du texte, comme avant)
    const b = elementSvg("g", { class: "scan-retirer", tabindex: "-1", "aria-hidden": "true" }, g);
    elementSvg("title", {}, b).textContent = manuelle ? "Supprimer cette zone" : "Retirer cette illustration (son contenu sera lu comme du texte)";
    elementSvg("circle", { cx: x1 - 26, cy: y0 + 26, r: 20, fill: "white", stroke: couleur, "stroke-width": 5 }, b);
    const t = elementSvg("text", { x: x1 - 26, y: y0 + 26, "text-anchor": "middle", "dominant-baseline": "central",
                                   "font-size": 30, "font-weight": 700, fill: couleur }, b);
    t.textContent = "×";
    b.addEventListener("pointerdown", (e) => { e.stopPropagation(); });
    b.addEventListener("click", (e) => { e.stopPropagation(); modifierZone(ref, { supprimer: true }); });
    if (choisie) poigneesCoins(svg, ref, rect, couleur);
  });
  // Mode dessin : un calque sur toute la page capte le glisser qui crée la zone.
  if (outil) {
    const calque = elementSvg("rect", { class: "scan-calque-dessin", x: 0, y: 0, width: editeurRegions.geo.largeur,
                                        height: editeurRegions.geo.hauteur, fill: "transparent" }, svg);
    let origine = null, apercu = null;
    calque.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      calque.setPointerCapture(e.pointerId);
      origine = versImageXY(svg, e);
      apercu = elementSvg("rect", { x: origine[0], y: origine[1], width: 0, height: 0, fill: COULEURS_ZONES[outil],
        "fill-opacity": 0.18, stroke: COULEURS_ZONES[outil], "stroke-width": 5, "pointer-events": "none" }, svg);
    });
    calque.addEventListener("pointermove", (e) => {
      if (!origine) return;
      const [x0, y0, x1, y1] = rectNormal([...origine, ...versImageXY(svg, e)]);
      Object.entries({ x: x0, y: y0, width: x1 - x0, height: y1 - y0 }).forEach(([k, v]) => apercu.setAttribute(k, v));
    });
    calque.addEventListener("pointerup", (e) => {
      if (!origine) return;
      if (calque.hasPointerCapture(e.pointerId)) calque.releasePointerCapture(e.pointerId);
      const rect = rectNormal([...origine, ...versImageXY(svg, e)]);
      origine = null;
      if (rect[2] - rect[0] < COTE_MIN_ZONE || rect[3] - rect[1] < COTE_MIN_ZONE) { apercu.remove(); return; }
      const { reglage } = editeurRegions;
      reglage.zones.push({ type: outil, rect });
      editeurRegions.selection = { source: "zone", index: reglage.zones.length - 1 };
      choisirOutil(null, false);
      enregistrerRegions(reglage);
    });
  }
}

// Quatre coins de la zone sélectionnée : les glisser la recadre.
function poigneesCoins(svg, ref, rect, couleur) {
  [[0, 1], [2, 1], [0, 3], [2, 3]].forEach(([ix, iy]) => {
    const c = elementSvg("circle", { class: "scan-coin", cx: rect[ix], cy: rect[iy], r: 16, fill: "white",
                                     stroke: couleur, "stroke-width": 5 }, svg);
    let actif = false, courant = [...rect];
    c.addEventListener("pointerdown", (e) => { e.preventDefault(); e.stopPropagation(); c.setPointerCapture(e.pointerId); actif = true; });
    c.addEventListener("pointermove", (e) => {
      if (!actif) return;
      const [x, y] = versImageXY(svg, e);
      courant = [...rect];
      courant[ix] = Math.round(x); courant[iy] = Math.round(y);
      c.setAttribute("cx", courant[ix]); c.setAttribute("cy", courant[iy]);
      const n = rectNormal(courant);
      const cadre = [...svg.querySelectorAll(".scan-zone.choisie rect")][0];
      if (cadre) Object.entries({ x: n[0], y: n[1], width: n[2] - n[0], height: n[3] - n[1] }).forEach(([k, v]) => cadre.setAttribute(k, v));
    });
    c.addEventListener("pointerup", (e) => {
      if (!actif) return;
      actif = false;
      if (c.hasPointerCapture(e.pointerId)) c.releasePointerCapture(e.pointerId);
      const n = rectNormal(courant);
      if (n[2] - n[0] < COTE_MIN_ZONE || n[3] - n[1] < COTE_MIN_ZONE || n.every((v, k) => v === rect[k])) { dessinerPoignees(); return; }
      modifierZone(ref, { rect: n });
    });
  });
}

// Barre « Nouvelle zone » et panneau de la zone sélectionnée (sous les menus de demi-page).
function construireOutilsZones() {
  const barre = $("scan-zones-outils");
  barre.innerHTML = "";
  const titre = document.createElement("span");
  titre.className = "aide";
  titre.textContent = "Nouvelle zone :";
  barre.appendChild(titre);
  TYPES_ZONE.forEach(([type, libelle, , aide]) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "ds-btn ds-btn--sm scan-outil";
    b.dataset.type = type;
    b.title = `${libelle} : ${aide}. Cliquez, puis tracez un rectangle sur la page.`;
    b.setAttribute("aria-pressed", "false");
    b.innerHTML = `<span class="scan-pastille-inline" style="border-color:${COULEURS_ZONES[type]}"></span>${libelle}`;
    b.addEventListener("click", () => choisirOutil(editeurRegions.outil === type ? null : type));
    barre.appendChild(b);
  });
  const select = $("scan-zone-type");
  select.innerHTML = TYPES_ZONE.map(([v, t]) => `<option value="${v}">${t}</option>`).join("");
  select.disabled = false;                     // désactivés pendant l'enregistrement qui a précédé ce rendu
  $("scan-zone-supprimer").disabled = false;
  majPanneauZone();
}

function choisirOutil(type, redessiner = true) {
  editeurRegions.outil = type;
  if (type) editeurRegions.selection = null;
  document.querySelectorAll("#scan-zones-outils .scan-outil").forEach(b => {
    b.setAttribute("aria-pressed", String(b.dataset.type === type));
  });
  $("scan-apercu-svg").classList.toggle("dessin", !!type);
  if (redessiner) { dessinerPoignees(); majPanneauZone(); }
}

function majPanneauZone() {
  const panneau = $("scan-zone-panneau");
  const sel = editeurRegions && editeurRegions.selection;
  const zone = sel && zonesAffichees().find(z => memeRef(z.ref, sel));
  panneau.hidden = !zone;
  if (!zone) return;
  $("scan-zone-type").value = zone.type;
  $("scan-zone-libelle").textContent = zone.ref.source === "zone" ? "Zone sélectionnée :" : "Illustration détectée :";
}

$("scan-zone-type").addEventListener("change", (e) => {
  if (editeurRegions && editeurRegions.selection) modifierZone(editeurRegions.selection, { type: e.target.value });
});
$("scan-zone-supprimer").addEventListener("click", () => {
  if (editeurRegions && editeurRegions.selection) modifierZone(editeurRegions.selection, { supprimer: true });
});
$("scan-apercu-svg").addEventListener("pointerdown", (e) => {
  // Clic dans le vide : désélectionne (les zones et poignées arrêtent la propagation).
  if (editeurRegions && !editeurRegions.outil && editeurRegions.selection && e.target === e.currentTarget) selectionner(null);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && editeurRegions && editeurRegions.outil) choisirOutil(null);
});

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

