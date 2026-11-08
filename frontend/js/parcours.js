// Accueil : parcours par objectif (maquette 2, 11/10/2026).
//
// On choisit ce qu'on veut faire ; la barre d'étapes montre où l'on en est et le bouton propose la
// suite. AUCUN outil n'est retiré ni déplacé : le lot, « Vos traductions », la Bibliothèque, le
// Laboratoire et la Chronique restent tels quels. Cette couche ne fait que LIRE l'état existant
// (GET /api/bibliotheque) et NAVIGUER — elle n'écrit rien et ne lance aucun traitement.
//
// « Document suivi » = le document en cours de traitement s'il y en a un, sinon celui dont l'activité
// est la plus récente (`maj_a`). C'est une heuristique assumée : le registre n'a pas de « document courant ».
//
// Chaque étape ne s'allume que sur un SIGNAL RÉEL, jamais par déduction :
//   Importer  = un fichier est dans le lot, ou un document existe au registre
//   Nettoyer  = la source du document est une sortie « scan multi-colonnes » ; « sautée » (tiret) si le
//               document a été traduit tel quel, sans nettoyage
//   Traduire  = statut du registre « termine »
//   Résumé    = GET /etude/statut renvoie une fiche « termine » pour cette traduction (la fiche se range à côté
//               de chemin_sortie, pas de la source), quels que soient son modèle et sa stratégie
//   Écouter   = GET /tts/statut renvoie un audio « termine »
//   Lire      = jamais cochée : lire ne laisse aucune trace, c'est l'étape d'arrivée

const OBJECTIF_STORAGE_KEY = "toledo-objectif";

// Étapes par objectif. `cle` est lue par etatEtapes() ; les libellés sont ceux de l'écran.
const PARCOURS_OBJECTIFS = {
  lire:     [["importer", "Importer"], ["traduire", "Traduire"], ["lire", "Lire"]],
  etudier:  [["importer", "Importer"], ["traduire", "Traduire"], ["etudier", "Résumé et quiz"]],
  ecouter:  [["importer", "Importer"], ["traduire", "Traduire"], ["ecouter", "Écouter"]],
  nettoyer: [["importer", "Importer"], ["analyser", "Analyser"], ["scan", "Scan colonnes"], ["verifier", "Vérifier"]],
};

let parcoursDocs = [];
let parcoursSignaux = { etude: null, audio: null };   // statut de la fiche / de l'audio du document suivi
const SECONDES_ETAPE = 1000;

// ── Parcours « Nettoyer un document » : Importer → Analyser → Scan colonnes → Vérifier ─────────────
// Le scan passe par lancerScan() (js/labo-scan-colonnes.js), le MÊME point d'entrée que le bouton du
// Laboratoire, avec les mêmes champs : le fichier produit est donc identique quel que soit le chemin.
const CIBLE_STORAGE_KEY = "toledo-nettoyage-cible";
let nettoyageCible = null;      // chemin du PDF suivi
try { nettoyageCible = localStorage.getItem(CIBLE_STORAGE_KEY); } catch { /* navigation privée */ }
let scanEtat = null;            // GET /labo/scan-colonnes/statut de la cible (null = jamais lancé)
let cibleInfoChargee = null;    // cible dont les langues OCR ont été lues
let parcoursErreurLancement = null;   // échec du dernier lancement : le Laboratoire qui l'affiche est masqué ici
let parcoursObjectif = "lire";

// Un objectif dont la fonction est coupée par un flag (bilbao) n'est pas proposé : off, l'onglet du Laboratoire
// disparaît et la route refuse (extraction_scan_colonnes) ; off, Résumé et quiz reste masqué (mode_avance).
function parcoursObjectifDispo(o) {
  if (o === "nettoyer") return featureFlags.extraction_scan_colonnes !== false;
  if (o === "etudier") return featureFlags.mode_avance !== false;
  return true;
}

function parcoursDocSuivi() {
  const enMarche = parcoursDocs.find((d) => d.statut === "en_cours" || d.statut === "en_attente");
  if (enMarche) return enMarche;
  return [...parcoursDocs].sort((a, b) => String(b.maj_a || "").localeCompare(String(a.maj_a || "")))[0] || null;
}

function parcoursLotNonVide() {
  const zone = document.getElementById("zone-lot");
  return !!zone && !zone.hidden;
}

function parcoursFormaterDuree(sec) {
  if (!sec || sec < 1) return "";
  const m = Math.round(sec / 60);
  return m < 1 ? "moins d'une minute" : m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")}`;
}

// Temps restant d'une traduction en marche : rythme observé si possible, sinon l'estimation d'avant lancement.
function parcoursRestant(doc) {
  const depuis = doc.maj_a ? (Date.now() - Date.parse(doc.maj_a)) / SECONDES_ETAPE : 0;
  const ecoule = (doc.temps_ecoule_secondes || 0) + (Number.isFinite(depuis) && depuis > 0 ? depuis : 0);
  const total = doc.total_sections || 0, faites = doc.sections_completees || 0;
  if (faites > 0 && total > faites && ecoule > 0) return (ecoule / faites) * (total - faites);
  if (doc.estimation_temps_total_secondes) return Math.max(0, doc.estimation_temps_total_secondes - ecoule);
  return null;
}

function parcoursNettoyerCalculer() {
  const etapes = PARCOURS_OBJECTIFS.nettoyer;
  const lot = window.__lotItems ? window.__lotItems() : [];
  const itemLot = lot.find((f) => f.chemin === nettoyageCible);
  const sortieScan = scanEtat ? scanEtat.chemin_sortie : null;
  const envoye = !!sortieScan && parcoursDocs.some((d) => d.chemin_source === sortieScan);
  const enMarche = !!scanEtat && ["en_attente", "en_cours"].includes(scanEtat.statut);
  const termine = !!scanEtat && scanEtat.statut === "termine";
  const partiel = !!scanEtat && scanEtat.statut === "erreur";

  const fait = {
    importer: !!nettoyageCible,
    // « En cours » seulement si une analyse tourne vraiment dans le lot : le lot est en mémoire et la cible
    // survit au rechargement, une cible hors du lot n'attend donc plus rien.
    analyser: !!nettoyageCible && (!!scanEtat || !itemLot || itemLot.stage !== "analyse"),
    scan: termine,
    verifier: envoye,
  };
  const pas = etapes.map(([cle, lib]) => [cle, lib, fait[cle] ? "fait" : ""]);
  const iCours = pas.findIndex((p) => p[2] === "");
  if (iCours >= 0) pas[iCours][2] = "cours";
  const cle = iCours >= 0 ? pas[iCours][0] : null;
  const titre = iCours >= 0 ? "" : "Parcours terminé";   // la barre d'étapes dit déjà où l'on en est
  const nom = nettoyageCible ? nettoyageCible.split("/").pop() : "";

  let detail = "", action = null, progression = null, ocr = false;
  if (cle === "importer") {
    detail = "Déposez le PDF scanné dans la zone ci-dessous. Texte brut sans titres (conférence, transcription) : « Segmentation automatique », dans le Laboratoire.";
  } else if (cle === "analyser") {
    detail = `Analyse de « ${nom} » en cours…`;
  } else if (cle === "scan") {
    ocr = true;
    if (enMarche) {
      progression = scanEtat.pages_total ? scanEtat.pages_faites / scanEtat.pages_total : 0;
      const reste = scanEtat.pages_faites > 0 && scanEtat.temps_ecoule_secondes
        ? parcoursFormaterDuree((scanEtat.temps_ecoule_secondes / scanEtat.pages_faites) * (scanEtat.pages_total - scanEtat.pages_faites)) : "";
      detail = `${scanEtat.statut === "en_attente" ? "En attente dans la file…" : "OCR colonne par colonne"} — ${scanEtat.pages_faites}/${scanEtat.pages_total} pages${reste ? ` · reste ≈ ${reste}` : ""}.`;
      action = { texte: "Arrêter", fn: parcoursArreterScan, secondaire: true };
    } else if (partiel || (scanEtat && scanEtat.statut === "annule")) {
      const n = (scanEtat.pages_en_erreur || []).length;
      detail = partiel
        ? (n ? `${n} page(s) en erreur. Relancer reprend uniquement celles-là ; les pages déjà faites sont conservées.`
             : `Le nettoyage a échoué : ${scanEtat.erreur || "cause inconnue"}. Relancer reprend là où il s'est arrêté.`)
        : "Arrêté. Relancer reprend là où le scan s'est arrêté : les pages déjà faites sont conservées.";
      action = { texte: "Relancer le nettoyage", fn: parcoursLancerScan };
    } else {
      detail = `« ${nom} » est prêt${$("scan-info").textContent && /page\(s\)/.test($("scan-info").textContent) ? ` (${$("scan-info").textContent.split(".")[0]})` : ""}. Vérifiez l'aperçu des régions, choisissez la langue et la mise en page, puis lancez.`;
      if (itemLot) detail += " ⚠ Ne lancez pas « Lancer la traduction » sur ce PDF : il serait traduit sans nettoyage.";
      action = { texte: "Nettoyer le document", fn: parcoursLancerScan };
    }
    if (parcoursErreurLancement && !enMarche) detail = `Le nettoyage n'a pas démarré : ${parcoursErreurLancement}`;
  } else if (cle === "verifier") {
    const rep = (scanEtat.reparations || []).length;
    if (lot.some((f) => f.chemin === sortieScan)) {
      return { pas, titre, detail: "Fichier nettoyé envoyé au lot : réglez langues et modèle dans « Réglages du lot », puis lancez la traduction.", action: null, progression, ocr };
    }
    detail = `✅ ${scanEtat.titres} titre(s) détecté(s)${rep ? `, ${rep} réparation(s) automatique(s) à vérifier (Laboratoire › Documents complexes)` : ""}. Relisez le fichier, puis lancez sa traduction (langues et modèle : réglages du lot, plus bas).`;
    action = { texte: "Traduire le fichier nettoyé", fn: parcoursEnvoyerScan };
  } else {
    detail = "Le fichier nettoyé est dans l'Accueil : suivez sa traduction dans « Vos traductions ».";
  }
  return { pas, titre, detail, action, progression, ocr, reset: !!scanEtat && !enMarche };
}

function parcoursEnvoyerScan() {
  // Un seul geste : ajoute le fichier nettoyé au lot ET lance sa traduction (réglages du lot), sans repasser par
  // « Lancer la traduction » ; le PDF brut quitte le lot pour ne jamais être traduit sans nettoyage.
  document.dispatchEvent(new CustomEvent("ajouter-au-lot-et-traduire", { detail: { chemin: scanEtat.chemin_sortie, reglages: null } }));
  document.dispatchEvent(new CustomEvent("retirer-du-lot", { detail: nettoyageCible }));
}

async function parcoursLancerScan() {
  // Mêmes champs que le Laboratoire : on y écrit, puis on passe par la fonction commune.
  $("scan-chemin").value = nettoyageCible;
  await chargerInfoScan(nettoyageCible);
  parcoursSyncOcr("vers-labo");
  parcoursErreurLancement = await lancerScan();
  parcoursCharger();
}

async function parcoursRecommencer() {
  if (!confirm("Recommencer à zéro ?\n\nLes pages déjà traitées, le fichier nettoyé et l'état du scan de ce PDF seront effacés ; le prochain scan refera tout. Le PDF d'origine n'est pas touché. C'est refusé si une traduction de « Vos traductions » vient de ce fichier nettoyé.")) return;
  try {
    await apiPost(`/labo/scan-colonnes/reinitialiser?chemin_pdf=${encodeURIComponent(nettoyageCible)}`, {});
  } catch (e) {
    alert(`Impossible de recommencer : ${e.message}`);
    return;
  }
  scanEtat = null;
  document.getElementById("parcours-apercu-image").hidden = true;
  document.getElementById("parcours-apercu-legende").hidden = true;
  document.getElementById("parcours-apercu-info").textContent = "";
  parcoursCharger();
}

async function parcoursArreterScan() {
  try {
    const r = await apiPost(`/labo/scan-colonnes/annuler?chemin_pdf=${encodeURIComponent(nettoyageCible)}`, {});
    if (!r.annulation_demandee) alert("Ce nettoyage n'est plus actif : il n'y a rien à arrêter.");
  } catch (e) {
    alert(`Impossible d'arrêter : ${e.message}`);
  }
  parcoursCharger();
}

// Les listes OCR du parcours et du Laboratoire sont deux vues des MÊMES réglages (#scan-langue,
// #scan-double-page) : une seule source, jamais deux copies qui divergent.
function parcoursSyncOcr(sens) {
  const paires = [["parcours-ocr-langue", "scan-langue"], ["parcours-ocr-double", "scan-double-page"],
                  ["parcours-ocr-debut", "scan-page-debut"], ["parcours-ocr-fin", "scan-page-fin"]];
  for (const [a, b] of paires) {
    if (sens === "vers-labo") { $(b).value = $(a).value; }
    else { if ($(a).tagName === "SELECT" && $(a).innerHTML !== $(b).innerHTML) $(a).innerHTML = $(b).innerHTML; $(a).value = $(b).value; }
  }
}

// Renvoie { pas: [[cle, libellé, état]], titre, detail, action: {texte, fn}|null }. États : fait | cours | saute | "".
function parcoursCalculer() {
  if (parcoursObjectif === "nettoyer") return parcoursNettoyerCalculer();
  const doc = parcoursDocSuivi();
  const modeAvance = localStorage.getItem("modeAvance") === "1";
  const etapes = PARCOURS_OBJECTIFS[parcoursObjectif];
  const traduit = !!doc && doc.statut === "termine";
  const enMarche = !!doc && (doc.statut === "en_cours" || doc.statut === "en_attente");
  const importe = parcoursLotNonVide() || !!doc;
  const nettoye = !!doc && /_scan_colonnes\.md$/i.test(doc.chemin_source || "");

  const etat = (cle) => {
    switch (cle) {
      case "importer": return importe ? "fait" : "";
      case "nettoyer": return nettoye ? "fait" : ((traduit || enMarche) ? "saute" : "");
      case "traduire": return traduit ? "fait" : "";
      case "etudier": return traduit && parcoursSignaux.etude === "termine" ? "fait" : "";
      case "ecouter": return traduit && parcoursSignaux.audio === "termine" ? "fait" : "";
      default: return "";   // « lire »
    }
  };
  const pas = etapes.map(([cle, lib]) => [cle, lib, etat(cle)]);
  const iCours = pas.findIndex((p) => p[2] === "");
  if (iCours >= 0) pas[iCours][2] = "cours";

  const nomDoc = doc ? (doc.nom || "document") : "";
  const cle = iCours >= 0 ? pas[iCours][0] : null;
  const titre = iCours >= 0 ? "" : "Parcours terminé";

  let detail = "";
  let action = null;
  const ouvrirBiblio = () => {
    activerModule("bibliotheque");
    if (doc) document.dispatchEvent(new CustomEvent("ouvrir-document", { detail: { chemin_sortie: doc.chemin_sortie } }));
  };
  const exigeAvance = (texte, fn) => {
    if (modeAvance) return { texte, fn };
    if (featureFlags.mode_avance === false) return null;   // jamais d'activation malgré le flag
    return { texte: "Activer le mode avancé", fn: () => { appliquerModeAvance(true); parcoursRendre(); } };
  };

  if (cle === "importer") {
    detail = "Glissez un fichier .pdf ou .md dans la zone ci-dessous.";
  } else if (cle === "traduire") {
    if (enMarche) {
      const scope = doc.chapitres_selectionnes || [];
      const faits = (doc.chapitres_traduits || []).filter((i) => scope.includes(i)).length;
      const reste = parcoursFormaterDuree(parcoursRestant(doc));
      detail = `« ${nomDoc} » : ${scope.length ? `${faits}/${scope.length} chapitres` : "en cours"}${reste ? ` · reste ≈ ${reste}` : ""}. Suivi détaillé dans « Vos traductions ».`;
    } else if (parcoursLotNonVide()) {
      detail = "Vérifiez les langues et le modèle, puis lancez la traduction du lot.";
    } else if (doc) {
      detail = `« ${nomDoc} » n'est pas terminé (${(doc.statut || "").replace("_", " ")}) : reprenez-le depuis « Vos traductions ».`;
    }
  } else if (cle === "nettoyer") {
    detail = "Scan à colonnes ou double page : « Documents complexes ». Texte brut sans titres : « Segmentation automatique ». Les deux sont dans le Laboratoire ; dans le scan, cochez « Traduire ensuite » pour enchaîner sans intervention.";
    action = exigeAvance("Ouvrir le Laboratoire", () => activerModule("laboratoire"));
  } else if (cle === "lire") {
    detail = doc ? `« ${nomDoc} » est traduit : lisez-le chapitre par chapitre, ou comparez avec l'original.` : "";
    action = doc ? { texte: "Lire dans la Bibliothèque", fn: ouvrirBiblio } : null;
  } else if (cle === "etudier") {
    detail = ["en_cours", "en_attente"].includes(parcoursSignaux.etude)
      ? "Génération du résumé et du quiz en cours — suivez-la dans la Bibliothèque."
      : "Le panneau Résumé et quiz de la Bibliothèque génère les points clés et les questions par chapitre.";
    action = exigeAvance("Ouvrir Résumé et quiz", ouvrirBiblio);
  } else if (cle === "ecouter") {
    detail = ["en_cours", "en_attente"].includes(parcoursSignaux.audio)
      ? "Génération de l'audio en cours — suivez-la dans la Bibliothèque."
      : "La lecture audio se trouve sous le texte, dans la Bibliothèque.";
    action = { texte: "Écouter dans la Bibliothèque", fn: ouvrirBiblio };
  } else if (iCours < 0 && doc) {
    detail = `« ${nomDoc} » : toutes les étapes sont faites.`;
    action = { texte: "Ouvrir dans la Bibliothèque", fn: ouvrirBiblio };
  }
  return { pas, titre, detail, action };
}

function parcoursRendre() {
  document.querySelectorAll(".objectif-option").forEach((b) => {
    const actif = b.dataset.objectif === parcoursObjectif;
    b.classList.toggle("is-active", actif);
    b.setAttribute("aria-checked", String(actif));
  });
  const etat = parcoursCalculer();
  const rail = document.getElementById("parcours-rail");
  rail.replaceChildren();
  etat.pas.forEach(([, lib, st], i) => {
    const d = document.createElement("div");
    d.className = "parcours-pas " + st;
    const pt = document.createElement("div");
    pt.className = "pt";
    pt.textContent = st === "fait" ? "✓" : (st === "saute" ? "–" : String(i + 1));
    const lb = document.createElement("div");
    lb.className = "lb";
    lb.textContent = lib;
    d.append(pt, lb);
    rail.appendChild(d);
  });
  document.getElementById("parcours-titre").textContent = etat.titre;
  document.getElementById("parcours-detail").textContent = etat.detail;
  const btn = document.getElementById("parcours-action");
  btn.hidden = !etat.action;
  if (etat.action) {
    btn.textContent = etat.action.texte; btn.onclick = etat.action.fn;
    btn.classList.toggle("ds-btn--primary", !etat.action.secondaire);
  }
  const prog = document.getElementById("parcours-progress");
  prog.hidden = etat.progression == null;
  if (etat.progression != null) document.getElementById("parcours-progress-barre").style.width = `${Math.round(etat.progression * 100)}%`;
  document.getElementById("parcours-ocr").hidden = !etat.ocr;
  document.getElementById("parcours-reset").hidden = !etat.reset;
}

async function parcoursCharger() {
  try { parcoursDocs = (await apiGet("/bibliotheque")).documents || []; } catch { parcoursDocs = []; }
  // Signaux de fin des étapes « Résumé » et « Écouter » : lus seulement quand l'objectif en a besoin et
  // que le document est traduit. Une erreur réseau = « inconnu » (null), jamais « fait ».
  const doc = parcoursDocSuivi();
  parcoursSignaux = { etude: null, audio: null };
  if (parcoursObjectif === "nettoyer") await parcoursChargerNettoyage();
  if (doc && doc.statut === "termine") {
    if (parcoursObjectif === "etudier") {
      try { parcoursSignaux.etude = ((await apiGet(`/etude/statut?chemin_source=${encodeURIComponent(doc.chemin_sortie)}`)) || {}).statut || null; } catch { /* inconnu */ }
    } else if (parcoursObjectif === "ecouter") {
      try { parcoursSignaux.audio = ((await apiGet(`/tts/statut?chemin_md=${encodeURIComponent(doc.chemin_sortie)}`)) || {}).statut || null; } catch { /* inconnu */ }
    }
  }
  parcoursRendre();
}

// Cible = le PDF le plus récemment déposé dans le lot ; mémorisée pour survivre à un rechargement de page.
async function parcoursChargerNettoyage() {
  const pdf = (window.__lotItems ? window.__lotItems() : []).filter((f) => f.type === "PDF").pop();
  if (pdf && pdf.chemin !== nettoyageCible) {
    nettoyageCible = pdf.chemin; scanEtat = null; cibleInfoChargee = null; parcoursErreurLancement = null;
    try { localStorage.setItem(CIBLE_STORAGE_KEY, nettoyageCible); } catch { /* navigation privée */ }
  }
  if (!nettoyageCible) return;
  if (cibleInfoChargee !== nettoyageCible) {      // langues OCR réellement installées, lues une fois par cible
    cibleInfoChargee = nettoyageCible;
    $("scan-chemin").value = nettoyageCible;
    await chargerInfoScan(nettoyageCible);
    parcoursSyncOcr("vers-parcours");
  }
  try { scanEtat = await apiGet(`/labo/scan-colonnes/statut?chemin_pdf=${encodeURIComponent(nettoyageCible)}`); } catch { /* inconnu : on garde l'état précédent */ }
  enchainerScanTermine(scanEtat);
}

function choisirObjectif(o) {
  if (!PARCOURS_OBJECTIFS[o] || !parcoursObjectifDispo(o)) o = "lire";
  parcoursObjectif = o;
  document.documentElement.dataset.objectif = o;
  try { localStorage.setItem(OBJECTIF_STORAGE_KEY, o); } catch { /* navigation privée */ }
  parcoursRendre();
  parcoursCharger();
}

// Écouteurs posés UNE fois, jamais dans parcoursRendre : elle est rappelée toutes les 4 s et les ferait s'empiler.
// Champs OCR du parcours → Laboratoire (une seule source de réglages) ; case d'enchaînement partagée.
["parcours-ocr-langue", "parcours-ocr-double", "parcours-ocr-debut", "parcours-ocr-fin"].forEach((id) =>
  $(id).addEventListener("change", () => parcoursSyncOcr("vers-labo")));
// Aperçu des régions détectées (colonnes, encadrés, gouttière) : même fonction et mêmes réglages que le Laboratoire.
$("parcours-reset").addEventListener("click", parcoursRecommencer);
$("parcours-apercu-bouton").addEventListener("click", async () => {
  if (!nettoyageCible) return;
  const ok = await chargerApercuScan(nettoyageCible, parseInt($("parcours-ocr-debut").value, 10) || 1,
                                     $("parcours-ocr-double").value, $("parcours-apercu-image"), $("parcours-apercu-info"));
  $("parcours-apercu-legende").hidden = !ok;
});
$("parcours-auto").checked = $("scan-puis-traduire").checked;
$("parcours-auto").addEventListener("change", (e) => { $("scan-puis-traduire").checked = e.target.checked; $("scan-puis-traduire").dispatchEvent(new Event("change")); });
$("scan-puis-traduire").addEventListener("change", (e) => { $("parcours-auto").checked = e.target.checked; });

document.querySelectorAll(".objectif-option").forEach((b) => b.addEventListener("click", () => choisirObjectif(b.dataset.objectif)));
document.addEventListener("flags-charges", () => {
  document.querySelectorAll(".objectif-option").forEach((b) => { b.hidden = !parcoursObjectifDispo(b.dataset.objectif); });
  if (!parcoursObjectifDispo(parcoursObjectif)) choisirObjectif("lire");
});
document.addEventListener("module-affiche", (e) => { if (e.detail === "import") parcoursCharger(); });
document.addEventListener("traduction-terminee", parcoursCharger);
document.addEventListener("backend-connecte", parcoursCharger);
// Le lot change sans événement dédié : on rafraîchit à intervalle raisonnable tant que l'Accueil est visible.
setInterval(() => { if (!document.getElementById("module-import").hidden) parcoursCharger(); }, 4000);

let objectifSauve = null;
try { objectifSauve = localStorage.getItem(OBJECTIF_STORAGE_KEY); } catch { /* navigation privée */ }
choisirObjectif(objectifSauve);

// ── Réglages du lot repliés : le <summary> garde un résumé lisible (langues → langue, modèle) ──
function majResumeReglagesLot() {
  const txt = (id) => { const el = document.getElementById(id); return el && el.selectedOptions[0] ? el.selectedOptions[0].textContent.trim() : ""; };
  const cible = txt("langue-cible").replace(/^Traduire vers (l'|le |la )?/i, "");
  const source = txt("langue-source").replace(/^Depuis (l'|le |la )?/i, "");
  const resume = [source && cible ? `${source} → ${cible}` : "", txt("modele")].filter(Boolean).join(" · ");
  document.getElementById("reglages-lot-resume").textContent = resume;
}
["langue-source", "langue-cible", "modele"].forEach((id) => document.getElementById(id).addEventListener("change", majResumeReglagesLot));
// Le menu des modèles est rempli après coup (GET /api/modeles) : on suit ses changements de contenu.
new MutationObserver(majResumeReglagesLot).observe(document.getElementById("modele"), { childList: true });
majResumeReglagesLot();
