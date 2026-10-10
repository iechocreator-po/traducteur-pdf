// Laboratoire › onglet « Scan multi-colonnes » (10/10/2026).
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
  try {
    const d = await apiGet(`/labo/scan-colonnes/info?chemin_pdf=${encodeURIComponent(chemin)}`, API_TIMEOUT_LONG_MS);
    if (!d.tesseract) {
      info.textContent = "⚠ Tesseract n'est pas installé : brew install tesseract.";
      return;
    }
    info.textContent = `${d.pages} page(s). Astuce : essaie d'abord quelques pages (ex. 1 à 3) avant tout le document.`;
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

$("bouton-scan-apercu").addEventListener("click", async () => {
  const chemin = $("scan-chemin").value.trim();
  if (!chemin) { alert("Indique d'abord le chemin du PDF, ou utilise Parcourir…"); return; }
  const page = parseInt($("scan-page-debut").value, 10) || 1;
  const img = $("scan-apercu-image");
  const zone = $("scan-resultat");
  zone.innerHTML = "<em>Calcul de l'aperçu…</em>";
  try {
    const url = `${API_BASE}/labo/scan-colonnes/apercu?chemin_pdf=${encodeURIComponent(chemin)}&page=${page}&double_page=${$("scan-double-page").value}&t=${Date.now()}`;
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
  } catch (e) {
    img.hidden = true;
    zone.innerHTML = `<span class="erreur">Erreur : ${e.message}</span>`;
  }
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
}

async function suivreScan(chemin) {
  clearTimeout(scanTimer);
  try {
    const etat = await apiGet(`/labo/scan-colonnes/statut?chemin_pdf=${encodeURIComponent(chemin)}`);
    rendreProgressionScan(etat);
    if (etat && ["en_attente", "en_cours"].includes(etat.statut)) {
      scanTimer = setTimeout(() => suivreScan(chemin), 3000);
      return;
    }
    if (etat) rendreResultatScan(etat);
    $("bouton-scan-lancer").disabled = false;
  } catch (e) {
    // backend momentanément injoignable : on réessaie plutôt que d'afficher un faux « terminé »
    scanTimer = setTimeout(() => suivreScan(chemin), 5000);
  }
}

$("bouton-scan-lancer").addEventListener("click", async () => {
  const chemin = $("scan-chemin").value.trim();
  if (!chemin) { alert("Indique d'abord le chemin du PDF, ou utilise Parcourir…"); return; }
  const corps = { chemin_pdf: chemin, langue_ocr: $("scan-langue").value || "eng", double_page: $("scan-double-page").value };
  const debut = parseInt($("scan-page-debut").value, 10);
  const fin = parseInt($("scan-page-fin").value, 10);
  if (debut) corps.page_debut = debut;
  if (fin) corps.page_fin = fin;
  $("scan-resultat").innerHTML = "";
  $("bouton-scan-lancer").disabled = true;
  try {
    await apiPost("/labo/scan-colonnes", corps, API_TIMEOUT_LONG_MS);
    suivreScan(chemin);
  } catch (e) {
    $("bouton-scan-lancer").disabled = false;
    $("scan-resultat").innerHTML = `<span class="erreur">Erreur : ${e.message}</span>`;
  }
});
