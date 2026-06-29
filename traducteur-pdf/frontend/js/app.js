// Frontend — appelle uniquement l'API locale FastAPI (http://localhost:8000).
// Aucune logique métier ici : tout est délégué au backend.

const API_BASE = "http://localhost:8000/api";

const elStatutApi       = document.getElementById("statut-api");
const elStatutOllama    = document.getElementById("statut-ollama");
const elModele          = document.getElementById("modele");
const elCheminPdf       = document.getElementById("chemin-pdf");
const elLangueSource    = document.getElementById("langue-source");
const elLangueCible     = document.getElementById("langue-cible");
const elExtracteurPdf   = document.getElementById("extracteur-pdf");
const elResultatAnalyse = document.getElementById("resultat-analyse");
const elContenuAnalyse  = document.getElementById("contenu-analyse");
const elBoutonReprendre = document.getElementById("bouton-reprendre");
const elRepriseProgression = document.getElementById("reprise-progression");

// Progression
const elSectionProgression = document.getElementById("section-progression");
const elProgressionTexte   = document.getElementById("progression-texte");
const elProgressionTemps   = document.getElementById("progression-temps");
const elBarreProgression   = document.getElementById("barre-progression");
const elTempsEcoule        = document.getElementById("temps-ecoule");
const elTempsRestant       = document.getElementById("temps-restant");
const elBoutonPause        = document.getElementById("bouton-pause");
const elBoutonContinuer    = document.getElementById("bouton-continuer");

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

async function verifierStatut() {
  const backendEnLigne = await verifierBackend();
  await mettreAJourBoutonLauncher();
  if (backendEnLigne) {
    try {
      const reponse = await fetch(`${API_BASE}/health`);
      const data = await reponse.json();
      elStatutOllama.textContent =
        data.ollama_accessible === "oui"
          ? "Accessible ✅"
          : "Inaccessible ⚠️ (vérifie qu'il est lancé)";
    } catch {
      elStatutOllama.textContent = "";
    }
  } else {
    elStatutOllama.textContent = "";
  }
}

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

// ── Analyse ──────────────────────────────────────────────────────────────────

async function analyserDocument() {
  const chemin = elCheminPdf.value.trim();
  if (!chemin) { alert("Indique le chemin du PDF d'abord."); return; }

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
      body: JSON.stringify({ chemin_pdf: chemin }),
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

elCheminPdf.addEventListener("blur", () => checkResume(elCheminPdf.value.trim()));

// ── Lancement de la traduction ────────────────────────────────────────────────

async function lancerTraduction(resume = false) {
  const chemin = elCheminPdf.value.trim();
  if (!chemin) { alert("Indique le chemin du PDF d'abord."); return; }

  // Si pas encore analysé et ce n'est pas une reprise, analyser d'abord
  if (!resume && !derniereAnalyse) {
    elContenuAnalyse.innerHTML = "<em>Analyse préalable en cours…</em>";
    elResultatAnalyse.hidden = false;
    await analyserDocument();
    if (!derniereAnalyse) return; // analyse a échoué
  }

  // Confirmation avec estimation du temps
  if (!resume && derniereAnalyse) {
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
    const body = {
      chemin_pdf: chemin,
      langue_source: elLangueSource.value,
      langue_cible: elLangueCible.value,
      modele_ollama: elModele.value,
      extracteur_pdf: elExtracteurPdf.value,
      resume,
      estimation_temps_total: derniereAnalyse?.estimation_temps_secondes ?? null,
    };

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

    jobActuel = { job_id: data.job_id, chemin_pdf: chemin };
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

  if (etat.statut === "termine") {
    arreterPolling();
    afficherProgression(false);
    elContenuAnalyse.innerHTML =
      `<p>✅ Traduction terminée — ${etat.total_sections} sections<br>` +
      `Fichier : <code>${etat.chemin_sortie}</code></p>`;
    elResultatAnalyse.hidden = false;
    elBoutonReprendre.hidden = true;
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

// ── Init ─────────────────────────────────────────────────────────────────────

verifierStatut();
chargerModeles();
chargerExtracteurs();
