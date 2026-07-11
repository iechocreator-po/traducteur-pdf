// Frontend — appelle uniquement l'API locale FastAPI (http://localhost:8000).
// Aucune logique métier ici : tout est délégué au backend.

const API_BASE = "http://localhost:8000/api";

const elStatutApi          = document.getElementById("statut-api");
const elStatutOllama       = document.getElementById("statut-ollama");
const elModele             = document.getElementById("modele");
const elCheminFichier      = document.getElementById("chemin-fichier");
const elTypeDetecte        = document.getElementById("type-detecte");
const elLangueSource       = document.getElementById("langue-source");
const elLangueCible        = document.getElementById("langue-cible");
const elExtracteurPdf      = document.getElementById("extracteur-pdf");
const elResultatAnalyse    = document.getElementById("resultat-analyse");
const elContenuAnalyse     = document.getElementById("contenu-analyse");
const elBoutonReprendre    = document.getElementById("bouton-reprendre");
const elRepriseProgression = document.getElementById("reprise-progression");

// ── Chapitres ─────────────────────────────────────────────────────────────────
// Sélecteur réutilisable (onglets Traduction et Étude ont chacun le leur).

function creerSelecteurChapitres(ids) {
  const etat = { disponibles: [], selectionnes: new Set() };
  const elListe = document.getElementById(ids.liste);
  const elZone = document.getElementById(ids.zone);
  const elBouton = document.getElementById(ids.bouton);

  function mettreAJourCompte() {
    document.getElementById(ids.compte).textContent =
      `${etat.selectionnes.size} / ${etat.disponibles.length} sélectionné(s)`;
  }

  function afficher() {
    elListe.innerHTML = "";
    for (const chap of etat.disponibles) {
      const indent = (chap.niveau - 1) * 14;
      const label = document.createElement("label");
      label.className = "chapitre-item";
      label.style.paddingLeft = `${indent + 4}px`;

      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = etat.selectionnes.has(chap.index);
      cb.addEventListener("change", () => {
        if (cb.checked) etat.selectionnes.add(chap.index);
        else etat.selectionnes.delete(chap.index);
        mettreAJourCompte();
      });

      const prefixe = "#".repeat(chap.niveau);
      const pageInfo = chap.page ? ` (p.${chap.page})` : "";
      label.appendChild(cb);
      label.appendChild(document.createTextNode(` ${prefixe} ${chap.titre}${pageInfo}`));
      elListe.appendChild(label);
    }
    mettreAJourCompte();
  }

  async function identifier() {
    const chemin = cheminSource();
    if (!chemin) { alert("Indique un fichier d'abord."); return; }

    elBouton.textContent = "⏳ Identification en cours…";
    elBouton.disabled = true;

    try {
      const body = corpsSourcePourApi({ extracteur_pdf: elExtracteurPdf.value });
      const rep = await fetch(`${API_BASE}/chapitres`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await rep.json();
      if (!rep.ok) { alert(`Erreur : ${data.detail}`); return; }

      etat.disponibles = data.chapitres;
      etat.selectionnes = new Set(data.chapitres.map(c => c.index));
      document.getElementById(ids.source).textContent = data.source === "signets_pdf"
        ? "📑 Table des matières officielle (signets PDF)"
        : "🔍 Titres détectés dans le Markdown";
      afficher();
      elZone.hidden = false;
    } catch (e) {
      alert(`Erreur de connexion : ${e}`);
    } finally {
      elBouton.textContent = "📋 Identifier les chapitres";
      elBouton.disabled = false;
    }
  }

  function reinitialiser() {
    etat.disponibles = [];
    etat.selectionnes = new Set();
    elZone.hidden = true;
    elListe.innerHTML = "";
  }

  elBouton.addEventListener("click", identifier);
  document.getElementById(ids.tout).addEventListener("click", () => {
    etat.selectionnes = new Set(etat.disponibles.map(c => c.index));
    afficher();
  });
  document.getElementById(ids.aucun).addEventListener("click", () => {
    etat.selectionnes = new Set();
    afficher();
  });

  return { etat, reinitialiser };
}

const chapitresTraduction = creerSelecteurChapitres({
  bouton: "bouton-identifier-chapitres", zone: "zone-chapitres", liste: "liste-chapitres",
  source: "chapitres-source", compte: "chapitres-compte", tout: "chapitres-tout", aucun: "chapitres-aucun",
});

const chapitresEtude = creerSelecteurChapitres({
  bouton: "bouton-identifier-chapitres-etude", zone: "zone-chapitres-etude", liste: "liste-chapitres-etude",
  source: "chapitres-source-etude", compte: "chapitres-compte-etude", tout: "chapitres-tout-etude", aucun: "chapitres-aucun-etude",
});

function reinitialiserChapitres() {
  chapitresTraduction.reinitialiser();
  chapitresEtude.reinitialiser();
}

// ── Détection du type de fichier (PDF ou Markdown) ──────────────────────────
// Plus de toggle : l'extension du chemin détermine le mode.

function modeSourceActuel() {
  const chemin = elCheminFichier.value.trim().toLowerCase();
  return chemin.endsWith(".md") || chemin.endsWith(".markdown") ? "md" : "pdf";
}

function mettreAJourTypeDetecte() {
  const chemin = elCheminFichier.value.trim();
  const estMd = modeSourceActuel() === "md";
  elTypeDetecte.textContent = !chemin
    ? ""
    : estMd
      ? "📝 Markdown détecté — traduction directe, sans extraction"
      : "📄 PDF détecté";
  // Extraction, analyse et conversion ne concernent que les PDF
  document.getElementById("zone-extracteur").hidden = estMd;
  document.getElementById("bouton-analyser").hidden = estMd;
  document.getElementById("bouton-convertir").hidden = estMd;
}

elCheminFichier.addEventListener("input", mettreAJourTypeDetecte);

function cheminSource() {
  return elCheminFichier.value.trim();
}

function corpsSourcePourApi(extra = {}) {
  return modeSourceActuel() === "md"
    ? { chemin_md: cheminSource(), ...extra }
    : { chemin_pdf: cheminSource(), ...extra };
}

// Progression
const elSectionProgression = document.getElementById("section-progression");
const elProgressionTexte   = document.getElementById("progression-texte");
const elProgressionTemps   = document.getElementById("progression-temps");
const elBarreProgression   = document.getElementById("barre-progression");
const elTempsEcoule        = document.getElementById("temps-ecoule");
const elTempsRestant       = document.getElementById("temps-restant");
const elBoutonPause        = document.getElementById("bouton-pause");
const elBoutonContinuer    = document.getElementById("bouton-continuer");
const elBoutonAnnuler      = document.getElementById("bouton-annuler");

// Erreurs
const elSectionErreurs  = document.getElementById("section-erreurs");
const elContenuErreurs  = document.getElementById("contenu-erreurs");

// État local
let jobActuel = null;       // { job_id, chemin_pdf }
let intervalPolling = null;
let derniereAnalyse = null; // ResultatAnalyse

// ── Utilitaires ──────────────────────────────────────────────────────────────

function formaterDuree(secondes) {
  if (secondes == null || secondes < 0) return "—";
  const s = Math.round(secondes);
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return `${m}:${String(sec).padStart(2, "0")}`;
}

function afficherErreurs(erreurs) {
  if (!erreurs || erreurs.length === 0) {
    elSectionErreurs.hidden = true;
    return;
  }
  elContenuErreurs.textContent = erreurs.join("\n");
  elSectionErreurs.hidden = false;
}

const LAUNCHER_BASE = "http://localhost:5501";
const elBoutonLancerBackend = document.getElementById("bouton-lancer-backend");

// ── Santé & modèles ──────────────────────────────────────────────────────────

const elBoutonReconnecter = document.getElementById("bouton-reconnecter");
let timerReconnexionAuto = null;

async function verifierStatut() {
  const backendEnLigne = await verifierBackend();
  await mettreAJourBoutonLauncher();
  let ollamaOk = false;
  if (backendEnLigne) {
    try {
      const reponse = await fetch(`${API_BASE}/health`);
      const data = await reponse.json();
      ollamaOk = data.ollama_accessible === "oui";
      elStatutOllama.textContent = ollamaOk
        ? "Accessible ✅"
        : "Inaccessible ⚠️ (vérifie qu'il est lancé)";
    } catch {
      elStatutOllama.textContent = "";
    }
  } else {
    elStatutOllama.textContent = "";
  }
  planifierReconnexionAuto(backendEnLigne && ollamaOk);
  return { backendEnLigne, ollamaOk };
}

// Tant qu'un voyant est rouge, on retente automatiquement toutes les 30 s.
function planifierReconnexionAuto(toutVaBien) {
  clearTimeout(timerReconnexionAuto);
  timerReconnexionAuto = null;
  if (!toutVaBien) {
    timerReconnexionAuto = setTimeout(reconnecter, 30000);
  }
}

async function reconnecter() {
  elBoutonReconnecter.disabled = true;
  elBoutonReconnecter.textContent = "🔄 Vérification…";
  try {
    const { backendEnLigne } = await verifierStatut();
    if (backendEnLigne) {
      await chargerModeles();
      await chargerExtracteurs();
      await chargerGlossaire();
      await chargerMoteursTts();
    }
  } finally {
    elBoutonReconnecter.disabled = false;
    elBoutonReconnecter.textContent = "🔄 Reconnecter";
  }
}

elBoutonReconnecter.addEventListener("click", reconnecter);

async function verifierBackend() {
  try {
    await fetch(`${API_BASE}/health`);
    elStatutApi.textContent = "En ligne ✅";
    elStatutApi.className = "statut-badge badge-ok";
    return true;
  } catch {
    elStatutApi.textContent = "Hors ligne ❌";
    elStatutApi.className = "statut-badge badge-erreur";
    return false;
  }
}

async function mettreAJourBoutonLauncher() {
  try {
    const rep = await fetch(`${LAUNCHER_BASE}/status`);
    const data = await rep.json();
    elBoutonLancerBackend.disabled = false;
    if (data.en_cours) {
      elBoutonLancerBackend.textContent = "Arrêter";
      elBoutonLancerBackend.classList.add("launcher-stop");
    } else {
      elBoutonLancerBackend.textContent = "Lancer";
      elBoutonLancerBackend.classList.remove("launcher-stop");
    }
  } catch {
    // Launcher non actif — désactiver le bouton avec indication
    elBoutonLancerBackend.textContent = "Launcher inactif";
    elBoutonLancerBackend.disabled = true;
    elBoutonLancerBackend.title = "Lance d'abord : python3 launcher.py";
    elBoutonLancerBackend.classList.remove("launcher-stop");
  }
}

elBoutonLancerBackend.addEventListener("click", async () => {
  elBoutonLancerBackend.disabled = true;
  try {
    const rep = await fetch(`${LAUNCHER_BASE}/status`);
    const data = await rep.json();
    const route = data.en_cours ? "/stop" : "/start";
    await fetch(`${LAUNCHER_BASE}${route}`, { method: "POST" });
    // Laisser le temps au processus de démarrer/s'arrêter
    setTimeout(async () => {
      await verifierStatut();
      await chargerModeles();
    }, 2000);
  } catch {
    elBoutonLancerBackend.disabled = false;
  }
});

async function chargerModeles() {
  try {
    const reponse = await fetch(`${API_BASE}/modeles`);
    const data = await reponse.json();
    elModele.innerHTML = "";
    for (const nom of data.modeles) {
      const option = document.createElement("option");
      option.value = nom;
      option.textContent = nom;
      elModele.appendChild(option);
    }
    if (data.modeles.length === 0) {
      elModele.innerHTML = '<option value="">Aucun modèle trouvé</option>';
    }
  } catch {
    elModele.innerHTML = '<option value="">Erreur de chargement</option>';
  }
}

async function chargerExtracteurs() {
  try {
    const reponse = await fetch(`${API_BASE}/config/extracteurs`);
    const data = await reponse.json();
    elExtracteurPdf.innerHTML = "";
    for (const ext of data.extracteurs) {
      const option = document.createElement("option");
      option.value = ext.id;
      option.textContent = ext.disponible ? ext.nom : `${ext.nom} (bientôt disponible)`;
      option.disabled = !ext.disponible;
      if (ext.id === data.defaut) option.selected = true;
      elExtracteurPdf.appendChild(option);
    }
  } catch {
    elExtracteurPdf.innerHTML = '<option value="pymupdf4llm">PyMuPDF4LLM</option>';
  }
}

// ── Glossaire ────────────────────────────────────────────────────────────────

const elGlossaireTermes = document.getElementById("glossaire-termes");
const elGlossaireStatut = document.getElementById("glossaire-statut");

async function chargerGlossaire() {
  try {
    const rep = await fetch(`${API_BASE}/glossaire`);
    const data = await rep.json();
    elGlossaireTermes.value = data.termes.join("\n");
  } catch {
    // Backend hors ligne — le glossaire sera rechargé à la reconnexion
  }
}

document.getElementById("bouton-sauver-glossaire").addEventListener("click", async () => {
  const termes = elGlossaireTermes.value.split("\n").map(t => t.trim()).filter(Boolean);
  try {
    const rep = await fetch(`${API_BASE}/glossaire`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ termes }),
    });
    const data = await rep.json();
    elGlossaireTermes.value = data.termes.join("\n");
    elGlossaireStatut.textContent = `✅ ${data.termes.length} terme(s) enregistré(s)`;
    setTimeout(() => { elGlossaireStatut.textContent = ""; }, 4000);
  } catch {
    elGlossaireStatut.textContent = "❌ Sauvegarde impossible (backend hors ligne ?)";
  }
});

// ── Analyse ──────────────────────────────────────────────────────────────────

async function analyserDocument() {
  const chemin = cheminSource();
  if (!chemin) { alert("Indique le chemin du PDF d'abord."); return; }
  // L'analyse n'est disponible qu'en mode PDF

  elContenuAnalyse.innerHTML = "<em>Analyse en cours…</em>";
  elResultatAnalyse.hidden = false;

  try {
    const reponse = await fetch(`${API_BASE}/analyser`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        chemin_pdf: chemin,
        modele_ollama: elModele.value,
        langue_source: elLangueSource.value,
        langue_cible: elLangueCible.value,
      }),
    });

    if (!reponse.ok) {
      const erreur = await reponse.json();
      elContenuAnalyse.innerHTML = `<span class="erreur">Erreur : ${erreur.detail}</span>`;
      return;
    }

    const data = await reponse.json();
    derniereAnalyse = data;

    const duree = formaterDuree(data.estimation_temps_secondes);
    elContenuAnalyse.innerHTML = `
      <table class="tableau-analyse">
        <tr><th>Pages analysées</th><td>${data.nb_pages_analysees}</td></tr>
        <tr><th>Texte extractible</th><td>${data.texte_extractible ? "✅ Oui" : "❌ Non"}</td></tr>
        <tr><th>Langue détectée</th><td>${data.langue_detectee || "—"}</td></tr>
        <tr><th>Sections (chunks)</th><td>${data.estimation_nb_chunks}</td></tr>
        <tr><th>Durée estimée</th><td>⏱ ~${duree}</td></tr>
        ${data.avertissements.length ? `<tr><th>Avertissements</th><td>${data.avertissements.join("<br>")}</td></tr>` : ""}
        <tr><th>Recommandation</th><td>${data.recommandation}</td></tr>
      </table>
    `;
  } catch (e) {
    elContenuAnalyse.innerHTML = `<span class="erreur">Erreur de connexion à l'API : ${e}</span>`;
  }
}

document.getElementById("bouton-analyser").addEventListener("click", analyserDocument);

// ── Reprise détection ────────────────────────────────────────────────────────

async function checkResume(chemin) {
  if (!chemin) return;
  try {
    const reponse = await fetch(`${API_BASE}/check-resume`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(corpsSourcePourApi()),
    });
    if (!reponse.ok) return;
    const etat = await reponse.json();
    if (etat && etat.derniere_section_completee > 0 && etat.statut !== "termine") {
      elRepriseProgression.textContent = `section ${etat.derniere_section_completee}/${etat.total_sections}`;
      elBoutonReprendre.hidden = false;
    } else {
      elBoutonReprendre.hidden = true;
    }
  } catch {
    elBoutonReprendre.hidden = true;
  }
}

elCheminFichier.addEventListener("blur", () => { checkResume(cheminSource()); reinitialiserChapitres(); });

// ── Lancement de la traduction ────────────────────────────────────────────────

async function lancerTraduction(resume = false) {
  const chemin = cheminSource();
  if (!chemin) {
    alert("Indique le chemin du fichier à traduire d'abord.");
    return;
  }

  // Re-vérifier les connexions juste avant de lancer — inutile de partir un job voué à l'échec.
  const sante = await verifierStatut();
  if (!sante.backendEnLigne) {
    alert("Le backend est hors ligne. Lance-le d'abord (bouton « Lancer »), puis réessaie.");
    return;
  }
  if (!sante.ollamaOk) {
    alert("Ollama est inaccessible. Vérifie qu'il est lancé, puis clique 🔄 Reconnecter et réessaie.");
    return;
  }

  // En mode PDF : analyse préalable si pas encore faite
  if (modeSourceActuel() === "pdf" && !resume && !derniereAnalyse) {
    elContenuAnalyse.innerHTML = "<em>Analyse préalable en cours…</em>";
    elResultatAnalyse.hidden = false;
    await analyserDocument();
    if (!derniereAnalyse) return;
  }

  // Confirmation avec estimation du temps (mode PDF uniquement)
  if (modeSourceActuel() === "pdf" && !resume && derniereAnalyse) {
    const duree = formaterDuree(derniereAnalyse.estimation_temps_secondes);
    const chunks = derniereAnalyse.estimation_nb_chunks;
    const ok = confirm(
      `Prêt à lancer la traduction ?\n\n` +
      `• ${chunks} sections à traduire\n` +
      `• Durée estimée : ~${duree}\n\n` +
      `Lancer la traduction ?`
    );
    if (!ok) return;
  }

  // Démarrer ou reprendre
  demarrerPolling();

  try {
    const chapitresBody =
      chapitresTraduction.etat.disponibles.length > 0 && chapitresTraduction.etat.selectionnes.size > 0
        ? { chapitres_selectionnes: [...chapitresTraduction.etat.selectionnes] }
        : {};

    const body = corpsSourcePourApi({
      langue_source: elLangueSource.value,
      langue_cible: elLangueCible.value,
      modele_ollama: elModele.value,
      extracteur_pdf: elExtracteurPdf.value,
      resume,
      estimation_temps_total: derniereAnalyse?.estimation_temps_secondes ?? null,
      ...chapitresBody,
    });

    const reponse = await fetch(`${API_BASE}/translate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    const data = await reponse.json();
    if (!reponse.ok) {
      arreterPolling();
      elContenuAnalyse.innerHTML = `<span class="erreur">Erreur : ${data.detail}</span>`;
      return;
    }

    jobActuel = { job_id: data.job_id, chemin_pdf: chemin };  // chemin_pdf = identifiant générique pour le poll
    afficherProgression(true);
    elBoutonReprendre.hidden = true;

  } catch (e) {
    arreterPolling();
    elContenuAnalyse.innerHTML = `<span class="erreur">Erreur de connexion à l'API : ${e}</span>`;
  }
}

document.getElementById("bouton-traduire").addEventListener("click", () => lancerTraduction(false));
elBoutonReprendre.addEventListener("click", () => lancerTraduction(true));

// ── Progression & polling ─────────────────────────────────────────────────────

function afficherProgression(visible) {
  elSectionProgression.hidden = !visible;
  if (!visible) return;
  elBoutonPause.hidden = false;
  elBoutonContinuer.hidden = true;
  elBoutonAnnuler.disabled = false;
  elBoutonAnnuler.textContent = "✕ Annuler";
}

function afficherAvertissements(etat) {
  if (!etat.avertissements || etat.avertissements.length === 0) return "";
  return `<p class="erreur">⚠ Avertissements qualité :<br>${etat.avertissements.join("<br>")}</p>`;
}

function mettreAJourProgression(etat) {
  const pct = etat.total_sections > 0
    ? Math.round((etat.derniere_section_completee / etat.total_sections) * 100)
    : 0;

  elBarreProgression.style.width = `${pct}%`;
  elProgressionTexte.textContent =
    `${etat.derniere_section_completee} / ${etat.total_sections} sections (${pct}%)`;

  elTempsEcoule.textContent = formaterDuree(etat.temps_ecoule_secondes);

  const restant = etat.estimation_temps_total_secondes != null
    ? Math.max(0, etat.estimation_temps_total_secondes - etat.temps_ecoule_secondes)
    : null;
  elTempsRestant.textContent = formaterDuree(restant);

  afficherErreurs(etat.erreurs);

  if (etat.statut === "en_attente") {
    elProgressionTexte.textContent = "⏳ En file d'attente — un autre job est en cours…";
  }

  if (etat.statut === "termine") {
    arreterPolling();
    afficherProgression(false);
    elContenuAnalyse.innerHTML =
      `<p>✅ Traduction terminée — ${etat.total_sections} sections<br>` +
      `Fichier : <code>${etat.chemin_sortie}</code></p>` +
      afficherAvertissements(etat);
    elResultatAnalyse.hidden = false;
    elBoutonReprendre.hidden = true;
    jobActuel = null;
  } else if (etat.statut === "annule") {
    arreterPolling();
    afficherProgression(false);
    elContenuAnalyse.innerHTML =
      `<p>✕ Traduction annulée — ${etat.derniere_section_completee} / ${etat.total_sections} sections complétées.<br>` +
      `Tu peux reprendre plus tard là où le job s'est arrêté.</p>`;
    elResultatAnalyse.hidden = false;
    elRepriseProgression.textContent =
      `section ${etat.derniere_section_completee}/${etat.total_sections}`;
    elBoutonReprendre.hidden = etat.derniere_section_completee === 0;
    jobActuel = null;
  } else if (etat.statut === "en_pause") {
    arreterPolling();
    elBoutonPause.hidden = true;
    elBoutonContinuer.hidden = false;
    elRepriseProgression.textContent =
      `section ${etat.derniere_section_completee}/${etat.total_sections}`;
    elBoutonReprendre.hidden = false;
  } else if (etat.statut === "erreur") {
    arreterPolling();
    afficherProgression(false);
    elContenuAnalyse.innerHTML = `<span class="erreur">❌ Erreur fatale du job. Voir le journal d'erreurs.</span>`;
    elResultatAnalyse.hidden = false;
    jobActuel = null;
  }
}

async function pollStatut() {
  if (!jobActuel) return;
  try {
    const url = `${API_BASE}/job/${jobActuel.job_id}/statut?chemin_pdf=${encodeURIComponent(jobActuel.chemin_pdf)}`;
    const reponse = await fetch(url);
    if (!reponse.ok) return;
    const etat = await reponse.json();
    mettreAJourProgression(etat);
  } catch {
    // Connexion perdue momentanément — on réessaie au prochain tick
  }
}

function demarrerPolling() {
  if (intervalPolling) return;
  intervalPolling = setInterval(pollStatut, 2000);
}

function arreterPolling() {
  clearInterval(intervalPolling);
  intervalPolling = null;
}

// ── Pause / Continuer ────────────────────────────────────────────────────────

elBoutonPause.addEventListener("click", async () => {
  if (!jobActuel) return;
  try {
    await fetch(`${API_BASE}/job/${jobActuel.job_id}/pause`, { method: "POST" });
    elBoutonPause.hidden = true;
    elBoutonPause.textContent = "⏸ Pause demandée…";
    // L'état "en_pause" sera détecté par le prochain poll
  } catch (e) {
    alert(`Impossible de mettre en pause : ${e}`);
  }
});

elBoutonContinuer.addEventListener("click", () => {
  elBoutonContinuer.hidden = true;
  elBoutonPause.hidden = false;
  elBoutonPause.textContent = "⏸ Pause";
  lancerTraduction(true);
});

// ── Annulation ───────────────────────────────────────────────────────────────

elBoutonAnnuler.addEventListener("click", async () => {
  if (!jobActuel) return;
  if (!confirm("Annuler la traduction en cours ?\nLa progression déjà faite est conservée.")) return;
  elBoutonAnnuler.disabled = true;
  elBoutonAnnuler.textContent = "✕ Annulation demandée…";
  try {
    const rep = await fetch(`${API_BASE}/job/${jobActuel.job_id}/annuler`, { method: "POST" });
    if (!rep.ok) {
      const data = await rep.json();
      alert(`Impossible d'annuler : ${data.detail}`);
      elBoutonAnnuler.disabled = false;
      elBoutonAnnuler.textContent = "✕ Annuler";
    }
    // L'état "annule" sera détecté par le prochain poll
  } catch (e) {
    alert(`Impossible d'annuler : ${e}`);
    elBoutonAnnuler.disabled = false;
    elBoutonAnnuler.textContent = "✕ Annuler";
  }
});

// ── Planification multi-fichiers ─────────────────────────────────────────────

const elPlanFichiers   = document.getElementById("plan-fichiers");
const elPlanHeure      = document.getElementById("plan-heure");
const elPlanStatut     = document.getElementById("plan-statut");
const elZonePlanifies  = document.getElementById("zone-planifies");
const elTbodyPlanifies = document.getElementById("tbody-planifies");

function nomFichier(chemin) {
  return chemin.split("/").pop();
}

function formaterDateISO(iso) {
  try {
    return new Date(iso).toLocaleString("fr-CA", { dateStyle: "short", timeStyle: "short" });
  } catch {
    return iso;
  }
}

// Pré-remplit l'heure d'exécution à ce soir 23 h (format local exigé par datetime-local)
(function initHeurePlanification() {
  const d = new Date();
  d.setHours(23, 0, 0, 0);
  const pad = n => String(n).padStart(2, "0");
  elPlanHeure.value =
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
})();

async function planifierFichiers() {
  const chemins = elPlanFichiers.value.split("\n").map(c => c.trim()).filter(Boolean);
  if (chemins.length === 0) { alert("Indique au moins un chemin de fichier (un par ligne)."); return; }
  if (!elPlanHeure.value) { alert("Choisis la date et l'heure d'exécution."); return; }

  try {
    const rep = await fetch(`${API_BASE}/schedule/batch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        chemins,
        executer_a: new Date(elPlanHeure.value).toISOString(),
        langue_source: elLangueSource.value,
        langue_cible: elLangueCible.value,
        modele_ollama: elModele.value,
        extracteur_pdf: elExtracteurPdf.value,
      }),
    });
    const data = await rep.json();
    if (!rep.ok) {
      elPlanStatut.textContent = `❌ ${data.detail}`;
      return;
    }
    elPlanStatut.textContent = `✅ ${data.jobs.length} fichier(s) planifié(s)`;
    setTimeout(() => { elPlanStatut.textContent = ""; }, 4000);
    elPlanFichiers.value = "";
    await rafraichirPlanifies();
  } catch {
    elPlanStatut.textContent = "❌ Planification impossible (backend hors ligne ?)";
  }
}

async function statutReelJob(chemin) {
  // Pour un job déclenché, va chercher l'état réel de la traduction
  try {
    const cle = chemin.toLowerCase().endsWith(".md") ? "chemin_md" : "chemin_pdf";
    const rep = await fetch(`${API_BASE}/check-resume`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ [cle]: chemin }),
    });
    if (!rep.ok) return "▶ Déclenché";
    const etat = await rep.json();
    if (!etat) return "▶ Déclenché";
    switch (etat.statut) {
      case "termine":    return "✅ Terminé";
      case "erreur":     return "❌ Erreur";
      case "annule":     return "✕ Annulé";
      case "en_pause":   return "⏸ En pause";
      case "en_attente": return "⏳ En file d'attente";
      default:           return `🔄 En cours — ${etat.derniere_section_completee}/${etat.total_sections} sections`;
    }
  } catch {
    return "▶ Déclenché";
  }
}

async function rafraichirPlanifies() {
  let jobs = [];
  try {
    const rep = await fetch(`${API_BASE}/scheduled/tous`);
    if (!rep.ok) return;
    jobs = (await rep.json()).jobs;
  } catch {
    return; // Backend hors ligne — on retentera au prochain rafraîchissement
  }

  jobs.sort((a, b) => (b.cree_a || "").localeCompare(a.cree_a || ""));
  elZonePlanifies.hidden = jobs.length === 0;
  elTbodyPlanifies.innerHTML = "";

  for (const job of jobs) {
    const tr = document.createElement("tr");

    const tdFichier = document.createElement("td");
    tdFichier.textContent = nomFichier(job.chemin_pdf);
    tdFichier.title = job.chemin_pdf;

    const tdQuand = document.createElement("td");
    tdQuand.textContent = formaterDateISO(job.executer_a);

    const tdStatut = document.createElement("td");
    if (job.statut === "planifie") tdStatut.textContent = "🕐 Planifié";
    else if (job.statut === "annule") tdStatut.textContent = "✕ Annulé";
    else {
      tdStatut.textContent = "▶ Déclenché";
      statutReelJob(job.chemin_pdf).then(txt => { tdStatut.textContent = txt; });
    }

    const tdAction = document.createElement("td");
    if (job.statut === "planifie") {
      const btn = document.createElement("button");
      btn.className = "bouton-mini";
      btn.textContent = "✕ Retirer";
      btn.addEventListener("click", async () => {
        await fetch(`${API_BASE}/scheduled/${job.id}`, { method: "DELETE" });
        rafraichirPlanifies();
      });
      tdAction.appendChild(btn);
    }

    tr.append(tdFichier, tdQuand, tdStatut, tdAction);
    elTbodyPlanifies.appendChild(tr);
  }
}

document.getElementById("bouton-planifier").addEventListener("click", planifierFichiers);
setInterval(rafraichirPlanifies, 10000);

// ── Conversion PDF → Markdown ─────────────────────────────────────────────────

const elResultatConversion = document.getElementById("resultat-conversion");
const elContenuConversion  = document.getElementById("contenu-conversion");

async function convertirEnMarkdown() {
  const chemin = cheminSource();
  if (!chemin) { alert("Indique le chemin du PDF d'abord."); return; }
  // La conversion n'est disponible qu'en mode PDF

  elContenuConversion.innerHTML = "<em>Conversion en cours…</em>";
  elResultatConversion.hidden = false;

  try {
    const reponse = await fetch(`${API_BASE}/convert`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        chemin_pdf: chemin,
        extracteur_pdf: elExtracteurPdf.value,
      }),
    });

    const data = await reponse.json();
    if (!reponse.ok) {
      elContenuConversion.innerHTML = `<span class="erreur">Erreur : ${data.detail}</span>`;
      return;
    }

    elContenuConversion.innerHTML =
      `<p>✅ Conversion terminée — ${data.nb_caracteres.toLocaleString()} caractères<br>` +
      `Fichier : <code>${data.chemin_sortie}</code></p>`;
  } catch (e) {
    elContenuConversion.innerHTML = `<span class="erreur">Erreur de connexion à l'API : ${e}</span>`;
  }
}

document.getElementById("bouton-convertir").addEventListener("click", convertirEnMarkdown);

// ── Lecture audio (TTS) ──────────────────────────────────────────────────────

const elTtsMoteur       = document.getElementById("tts-moteur");
const elTtsVoix         = document.getElementById("tts-voix");
const elTtsAide         = document.getElementById("tts-aide");
const elTtsExtrait      = document.getElementById("tts-extrait");
const elTtsStatut       = document.getElementById("tts-statut");
const elBoutonEcouter   = document.getElementById("bouton-ecouter");
const elBoutonGenererAudio = document.getElementById("bouton-generer-audio");
const elBoutonAnnulerAudio = document.getElementById("bouton-annuler-audio");

let ttsMoteurs = [];          // [{id, nom, disponible, voix[], aide}]
let audioEnLecture = null;    // objet Audio de l'extrait en cours
let jobAudio = null;          // { jobId, cheminSortie }
let intervalAudioPolling = null;

async function chargerMoteursTts() {
  try {
    const rep = await fetch(`${API_BASE}/tts/moteurs`);
    const data = await rep.json();
    ttsMoteurs = data.moteurs;
    elTtsMoteur.innerHTML = "";
    for (const m of ttsMoteurs) {
      const opt = document.createElement("option");
      opt.value = m.id;
      opt.textContent = m.disponible ? m.nom : `${m.nom} (indisponible)`;
      opt.disabled = !m.disponible;
      elTtsMoteur.appendChild(opt);
    }
    // Sélectionne le premier moteur disponible
    const premierDispo = ttsMoteurs.find(m => m.disponible);
    if (premierDispo) elTtsMoteur.value = premierDispo.id;
    mettreAJourVoixTts();
  } catch {
    elTtsMoteur.innerHTML = '<option value="">Erreur de chargement</option>';
  }
}

function mettreAJourVoixTts() {
  const moteur = ttsMoteurs.find(m => m.id === elTtsMoteur.value);
  elTtsVoix.innerHTML = "";
  if (!moteur) return;
  for (const v of moteur.voix) {
    const opt = document.createElement("option");
    opt.value = v;
    opt.textContent = v;
    elTtsVoix.appendChild(opt);
  }
  elTtsAide.textContent = moteur.aide || "";
  const pret = moteur.disponible && moteur.voix.length > 0;
  elBoutonEcouter.disabled = !pret;
  elBoutonGenererAudio.disabled = !pret;
}

elTtsMoteur.addEventListener("change", mettreAJourVoixTts);

elBoutonEcouter.addEventListener("click", async () => {
  const texte = elTtsExtrait.value.trim();
  if (!texte) { alert("Colle un court texte à écouter d'abord."); return; }
  if (audioEnLecture) { audioEnLecture.pause(); audioEnLecture = null; }
  elBoutonEcouter.disabled = true;
  elBoutonEcouter.textContent = "⏳ Synthèse…";
  try {
    const rep = await fetch(`${API_BASE}/tts/extrait`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ texte, moteur: elTtsMoteur.value, voix: elTtsVoix.value }),
    });
    if (!rep.ok) {
      const data = await rep.json();
      alert(`Erreur : ${data.detail}`);
      return;
    }
    const blob = await rep.blob();
    audioEnLecture = new Audio(URL.createObjectURL(blob));
    audioEnLecture.play();
  } catch (e) {
    alert(`Impossible de générer l'extrait : ${e}`);
  } finally {
    elBoutonEcouter.disabled = false;
    elBoutonEcouter.textContent = "▶ Écouter l'extrait";
  }
});

elBoutonGenererAudio.addEventListener("click", async () => {
  const chemin = cheminSource();
  if (!chemin) { alert("Indique d'abord un fichier dans la section 1."); return; }
  if (!chemin.toLowerCase().endsWith(".md")) {
    alert("La génération audio attend un fichier Markdown (.md). Convertis d'abord le PDF, puis indique le .md.");
    return;
  }
  try {
    const rep = await fetch(`${API_BASE}/tts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chemin_md: chemin, moteur: elTtsMoteur.value, voix: elTtsVoix.value }),
    });
    const data = await rep.json();
    if (!rep.ok) { elTtsStatut.textContent = `❌ ${data.detail}`; return; }
    jobAudio = { jobId: data.job_id, cheminSource: chemin };
    elBoutonAnnulerAudio.hidden = false;
    demarrerAudioPolling();
  } catch {
    elTtsStatut.textContent = "❌ Génération impossible (backend hors ligne ?)";
  }
});

elBoutonAnnulerAudio.addEventListener("click", async () => {
  if (!jobAudio) return;
  elBoutonAnnulerAudio.disabled = true;
  try {
    await fetch(`${API_BASE}/job/${jobAudio.jobId}/annuler`, { method: "POST" });
  } catch { /* le polling détectera l'état */ }
});

function demarrerAudioPolling() {
  if (intervalAudioPolling) return;
  pollStatutAudio();
  intervalAudioPolling = setInterval(pollStatutAudio, 2000);
}

function arreterAudioPolling() {
  clearInterval(intervalAudioPolling);
  intervalAudioPolling = null;
}

async function pollStatutAudio() {
  if (!jobAudio) return;
  try {
    const rep = await fetch(`${API_BASE}/tts/statut?chemin_md=${encodeURIComponent(jobAudio.cheminSource)}`);
    if (!rep.ok) return;
    const etat = await rep.json();
    if (!etat) return;
    const pct = etat.total_sections > 0
      ? Math.round((etat.sections_completees / etat.total_sections) * 100)
      : 0;
    switch (etat.statut) {
      case "en_attente":
        elTtsStatut.textContent = "⏳ En file d'attente…";
        break;
      case "en_cours":
        elTtsStatut.textContent = `🔊 Génération audio — ${etat.sections_completees}/${etat.total_sections} sections (${pct}%)`;
        break;
      case "termine":
        arreterAudioPolling();
        elBoutonAnnulerAudio.hidden = true;
        elBoutonAnnulerAudio.disabled = false;
        elTtsStatut.textContent = `✅ Audio généré — ${etat.chemin_sortie}`;
        jobAudio = null;
        break;
      case "annule":
        arreterAudioPolling();
        elBoutonAnnulerAudio.hidden = true;
        elBoutonAnnulerAudio.disabled = false;
        elTtsStatut.textContent = `✕ Génération annulée — ${etat.sections_completees}/${etat.total_sections} sections`;
        jobAudio = null;
        break;
      case "erreur":
        arreterAudioPolling();
        elBoutonAnnulerAudio.hidden = true;
        elBoutonAnnulerAudio.disabled = false;
        elTtsStatut.textContent = `❌ Erreur : ${etat.erreur || "inconnue"}`;
        jobAudio = null;
        break;
    }
  } catch { /* on réessaie au prochain tick */ }
}

// ── Onglets (Traduction / Étude) ─────────────────────────────────────────────
// L'onglet actif est mémorisé pour retrouver son contexte au rechargement.

function activerOnglet(nom) {
  document.getElementById("onglet-traduction").hidden = nom !== "traduction";
  document.getElementById("onglet-etude").hidden = nom !== "etude";
  document.querySelectorAll(".onglets .onglet").forEach((b) => {
    b.classList.toggle("is-active", b.dataset.onglet === nom);
    b.setAttribute("aria-selected", b.dataset.onglet === nom ? "true" : "false");
  });
  localStorage.setItem("onglet", nom);
}

document.querySelectorAll(".onglets .onglet").forEach((b) => {
  b.addEventListener("click", () => activerOnglet(b.dataset.onglet));
});
activerOnglet(localStorage.getItem("onglet") === "etude" ? "etude" : "traduction");

// ── Fiche d'étude ────────────────────────────────────────────────────────────

const elSectionProgressionEtude = document.getElementById("section-progression-etude");
const elEtudeProgressionTexte   = document.getElementById("etude-progression-texte");
const elEtudeBarreProgression   = document.getElementById("etude-barre-progression");
const elEtudeTempsEcoule        = document.getElementById("etude-temps-ecoule");
const elEtudeTempsRestant       = document.getElementById("etude-temps-restant");
const elEtudeListeProgression   = document.getElementById("etude-liste-progression");
const elBoutonPauseEtude        = document.getElementById("bouton-pause-etude");
const elBoutonContinuerEtude    = document.getElementById("bouton-continuer-etude");
const elBoutonAnnulerEtude      = document.getElementById("bouton-annuler-etude");
const elResultatEtude           = document.getElementById("resultat-etude");
const elEtudeFichierSortie      = document.getElementById("etude-fichier-sortie");
const elContenuFiche            = document.getElementById("contenu-fiche");
const elSectionErreursEtude     = document.getElementById("section-erreurs-etude");
const elContenuErreursEtude     = document.getElementById("contenu-erreurs-etude");

let jobEtude = null;            // { job_id, chemin_source }
let intervalEtudePolling = null;

const ETIQUETTES_ETAPE = {
  en_attente: ["⏳", "en attente"],
  points:     ["📝", "points à retenir en cours…"],
  questions:  ["❓", "questions en cours…"],
  termine:    ["✅", "terminé"],
  erreur:     ["❌", "erreur"],
};

async function lancerEtude() {
  const chemin = cheminSource();
  if (!chemin) { alert("Indique le chemin du fichier dans la section Document d'abord."); return; }
  if (chapitresEtude.etat.selectionnes.size === 0) {
    alert("Identifie puis sélectionne au moins un chapitre à étudier.");
    return;
  }

  const sante = await verifierStatut();
  if (!sante.backendEnLigne) {
    alert("Le backend est hors ligne. Lance-le d'abord (bouton « Lancer »), puis réessaie.");
    return;
  }
  if (!sante.ollamaOk) {
    alert("Ollama est inaccessible. Vérifie qu'il est lancé, puis clique 🔄 Reconnecter et réessaie.");
    return;
  }

  try {
    const body = corpsSourcePourApi({
      chapitres_selectionnes: [...chapitresEtude.etat.selectionnes],
      modele_ollama: elModele.value,
      extracteur_pdf: elExtracteurPdf.value,
      langue_fiche: document.getElementById("etude-langue").value,
      nb_points: parseInt(document.getElementById("etude-nb-points").value, 10) || 5,
      nb_questions: parseInt(document.getElementById("etude-nb-questions").value, 10) || 3,
    });
    const rep = await fetch(`${API_BASE}/etude`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await rep.json();
    if (!rep.ok) { alert(`Erreur : ${data.detail}`); return; }

    jobEtude = { job_id: data.job_id, chemin_source: chemin };
    elResultatEtude.hidden = true;
    afficherProgressionEtude(true);
    demarrerEtudePolling();
  } catch (e) {
    alert(`Erreur de connexion à l'API : ${e}`);
  }
}

function afficherProgressionEtude(visible) {
  elSectionProgressionEtude.hidden = !visible;
  if (!visible) return;
  elBoutonPauseEtude.hidden = false;
  elBoutonPauseEtude.textContent = "⏸ Pause";
  elBoutonContinuerEtude.hidden = true;
  elBoutonAnnulerEtude.disabled = false;
  elBoutonAnnulerEtude.textContent = "✕ Annuler";
}

function afficherListeProgressionEtude(etat) {
  elEtudeListeProgression.innerHTML = "";
  for (const chap of etat.chapitres) {
    const [icone, texte] = ETIQUETTES_ETAPE[chap.etape] || ["•", chap.etape];
    const ligne = document.createElement("div");
    ligne.className = "etude-chapitre-ligne";

    const spanTitre = document.createElement("span");
    spanTitre.className = "etude-chapitre-titre";
    spanTitre.textContent = `${icone} ${chap.titre}`;

    const spanEtape = document.createElement("span");
    spanEtape.className = "etude-chapitre-etape";
    spanEtape.textContent = texte;

    ligne.append(spanTitre, spanEtape);
    elEtudeListeProgression.appendChild(ligne);
  }
}

function afficherFiche(etat) {
  elContenuFiche.innerHTML = "";
  for (const chap of etat.chapitres) {
    if (chap.points.length === 0 && chap.questions.length === 0) continue;

    const h3 = document.createElement("h3");
    h3.textContent = chap.titre;
    elContenuFiche.appendChild(h3);

    if (chap.points.length > 0) {
      const h4 = document.createElement("h4");
      h4.textContent = "Points à retenir";
      const ol = document.createElement("ol");
      for (const point of chap.points) {
        const li = document.createElement("li");
        li.textContent = point;
        ol.appendChild(li);
      }
      elContenuFiche.append(h4, ol);
    }

    if (chap.questions.length > 0) {
      const h4 = document.createElement("h4");
      h4.textContent = "Questions de compréhension";
      elContenuFiche.appendChild(h4);
      chap.questions.forEach((q, i) => {
        const p = document.createElement("p");
        p.className = "fiche-question";
        p.textContent = `Q${i + 1}. ${q.question}`;

        const details = document.createElement("details");
        const summary = document.createElement("summary");
        summary.textContent = "Voir la réponse";
        const reponse = document.createElement("p");
        reponse.textContent = q.reponse;
        details.append(summary, reponse);

        elContenuFiche.append(p, details);
      });
    }
  }
  elEtudeFichierSortie.innerHTML = "";
  elEtudeFichierSortie.append("Fichier : ");
  const code = document.createElement("code");
  code.textContent = etat.chemin_sortie;
  elEtudeFichierSortie.appendChild(code);
  elResultatEtude.hidden = false;
}

function mettreAJourProgressionEtude(etat) {
  const pct = etat.total_etapes > 0
    ? Math.round((etat.etapes_completees / etat.total_etapes) * 100)
    : 0;
  elEtudeBarreProgression.style.width = `${pct}%`;
  elEtudeProgressionTexte.textContent =
    `${etat.etapes_completees} / ${etat.total_etapes} étapes (${pct}%)`;
  if (etat.statut === "en_attente") {
    elEtudeProgressionTexte.textContent = "⏳ En file d'attente — un autre job est en cours…";
  }

  elEtudeTempsEcoule.textContent = formaterDuree(etat.temps_ecoule_secondes);
  const restant = etat.estimation_temps_total_secondes != null
    ? Math.max(0, etat.estimation_temps_total_secondes - etat.temps_ecoule_secondes)
    : null;
  elEtudeTempsRestant.textContent = formaterDuree(restant);

  afficherListeProgressionEtude(etat);

  elSectionErreursEtude.hidden = !etat.erreurs || etat.erreurs.length === 0;
  if (etat.erreurs && etat.erreurs.length > 0) {
    elContenuErreursEtude.textContent = etat.erreurs.join("\n");
  }

  if (etat.statut === "termine") {
    arreterEtudePolling();
    afficherProgressionEtude(false);
    afficherFiche(etat);
    jobEtude = null;
  } else if (etat.statut === "annule") {
    arreterEtudePolling();
    afficherProgressionEtude(false);
    afficherFiche(etat);  // les chapitres déjà terminés restent consultables
    jobEtude = null;
  } else if (etat.statut === "en_pause") {
    arreterEtudePolling();
    elBoutonPauseEtude.hidden = true;
    elBoutonContinuerEtude.hidden = false;
  } else if (etat.statut === "erreur") {
    arreterEtudePolling();
    afficherProgressionEtude(false);
    jobEtude = null;
  }
}

async function pollStatutEtude() {
  if (!jobEtude) return;
  try {
    const url = `${API_BASE}/etude/statut?chemin_source=${encodeURIComponent(jobEtude.chemin_source)}`;
    const rep = await fetch(url);
    if (!rep.ok) return;
    const etat = await rep.json();
    if (etat) mettreAJourProgressionEtude(etat);
  } catch {
    // Connexion perdue momentanément — on réessaie au prochain tick
  }
}

function demarrerEtudePolling() {
  if (intervalEtudePolling) return;
  pollStatutEtude();
  intervalEtudePolling = setInterval(pollStatutEtude, 2000);
}

function arreterEtudePolling() {
  clearInterval(intervalEtudePolling);
  intervalEtudePolling = null;
}

document.getElementById("bouton-generer-fiche").addEventListener("click", () => lancerEtude());

elBoutonPauseEtude.addEventListener("click", async () => {
  if (!jobEtude) return;
  try {
    await fetch(`${API_BASE}/job/${jobEtude.job_id}/pause`, { method: "POST" });
    elBoutonPauseEtude.textContent = "⏸ Pause demandée…";
    // L'état "en_pause" sera détecté par le prochain poll
  } catch (e) {
    alert(`Impossible de mettre en pause : ${e}`);
  }
});

elBoutonContinuerEtude.addEventListener("click", () => {
  // La reprise repasse par POST /etude : les chapitres terminés sont conservés
  elBoutonContinuerEtude.hidden = true;
  elBoutonPauseEtude.hidden = false;
  elBoutonPauseEtude.textContent = "⏸ Pause";
  lancerEtude();
});

elBoutonAnnulerEtude.addEventListener("click", async () => {
  if (!jobEtude) return;
  if (!confirm("Annuler la fiche d'étude en cours ?\nLes chapitres déjà terminés sont conservés.")) return;
  elBoutonAnnulerEtude.disabled = true;
  elBoutonAnnulerEtude.textContent = "✕ Annulation demandée…";
  try {
    const rep = await fetch(`${API_BASE}/job/${jobEtude.job_id}/annuler`, { method: "POST" });
    if (!rep.ok) {
      const data = await rep.json();
      alert(`Impossible d'annuler : ${data.detail}`);
      elBoutonAnnulerEtude.disabled = false;
      elBoutonAnnulerEtude.textContent = "✕ Annuler";
    }
  } catch (e) {
    alert(`Impossible d'annuler : ${e}`);
    elBoutonAnnulerEtude.disabled = false;
    elBoutonAnnulerEtude.textContent = "✕ Annuler";
  }
});

// Au chargement ou au changement de fichier : réaffiche une fiche déjà générée
async function chargerFicheExistante() {
  const chemin = cheminSource();
  if (!chemin) { elResultatEtude.hidden = true; return; }
  try {
    const rep = await fetch(`${API_BASE}/etude/statut?chemin_source=${encodeURIComponent(chemin)}`);
    if (!rep.ok) return;
    const etat = await rep.json();
    if (!etat) { elResultatEtude.hidden = true; return; }
    if (etat.statut === "en_cours" || etat.statut === "en_attente") {
      // Un job tourne encore (ex: rechargement de la page) — on raccroche le suivi
      jobEtude = { job_id: etat.job_id, chemin_source: chemin };
      afficherProgressionEtude(true);
      demarrerEtudePolling();
    } else if (etat.chapitres.some(c => c.etape === "termine")) {
      afficherFiche(etat);
    }
  } catch {
    // Backend hors ligne — rien à afficher
  }
}

elCheminFichier.addEventListener("blur", chargerFicheExistante);

// ── Thème (Auto / Clair / Sombre) ───────────────────────────────────────────
// 'auto' = suit le système (aucun data-theme) ; 'light'/'dark' = forcé via <html>.
// Le choix est mémorisé dans localStorage et appliqué dès le <head> (anti-flash).

function appliquerTheme(choix) {
  if (choix === "light" || choix === "dark") {
    document.documentElement.setAttribute("data-theme", choix);
  } else {
    document.documentElement.removeAttribute("data-theme");
    choix = "auto";
  }
  localStorage.setItem("theme", choix);
  document.querySelectorAll("#theme-switch button").forEach((b) => {
    b.classList.toggle("is-active", b.dataset.themeChoice === choix);
  });
}

document.querySelectorAll("#theme-switch button").forEach((b) => {
  b.addEventListener("click", () => appliquerTheme(b.dataset.themeChoice));
});
appliquerTheme(localStorage.getItem("theme") || "auto");

// ── Init ─────────────────────────────────────────────────────────────────────

verifierStatut();
chargerModeles();
chargerExtracteurs();
chargerGlossaire();
chargerMoteursTts();
rafraichirPlanifies();
chargerFicheExistante();
