// Module A — « Nouveau document » : lot multi-fichiers.
// Ajout par chemin → analyse auto → lancement en lot (file séquentielle côté
// backend : un seul job Ollama à la fois, progression individuelle par fichier).

(() => {
  // {id, chemin, type, stage: analyse|pret|probleme|lance|termine|erreur,
  //  qualite, eta, chapitres, recommandation, jobId, statutJob, pct, sections}
  let lot = [];
  let pollTimer = null;
  let lotEnPause = false;

  const elListe = $("liste-lot");

  // ── Ajout au lot ────────────────────────────────────────────────────────────

  async function ajouterAuLot() {
    const chemin = $("import-chemin").value.trim();
    if (!chemin) { alert("Indique le chemin absolu d'un fichier .pdf ou .md."); return; }
    if (lot.some(f => f.chemin === chemin)) { alert("Ce fichier est déjà dans le lot."); return; }

    const item = {
      id: `f${Date.now()}`,
      chemin,
      type: estMarkdown(chemin) ? "MD" : "PDF",
      stage: "analyse",
      qualite: null, eta: null, chapitres: null, recommandation: null,
      jobId: null, statutJob: null, pct: 0, sections: "",
    };
    lot.push(item);
    $("import-chemin").value = "";
    rendreLot();
    analyserItem(item);
  }

  async function analyserItem(item) {
    try {
      if (item.type === "MD") {
        // Pas d'analyse LLM pour un Markdown : comptage des chapitres suffit
        const data = await apiPost("/chapitres", corpsSource(item.chemin));
        item.qualite = "Markdown";
        item.eta = null;
        item.chapitres = data.chapitres.length;
        item.stage = "pret";
      } else {
        const data = await apiPost("/analyser", {
          chemin_pdf: item.chemin,
          modele_ollama: $("modele").value || "llama3.1",
        });
        item.chapitres = data.nb_chapitres;
        item.eta = data.estimation_temps_secondes;
        item.recommandation = data.recommandation;
        if (data.texte_extractible) {
          item.qualite = data.avertissements.length ? "Correcte" : "Excellente";
          item.stage = "pret";
        } else {
          item.qualite = "Problème";
          item.stage = "probleme";
        }
      }
    } catch (e) {
      item.stage = "erreur";
      item.recommandation = String(e.message || e);
    }
    rendreLot();
  }

  // ── Lancement du lot ────────────────────────────────────────────────────────

  async function lancerLot() {
    if (!(await exigerSante())) return;
    const prets = lot.filter(f => f.stage === "pret");
    if (prets.length === 0) return;

    for (const item of prets) {
      try {
        const data = await apiPost("/translate", corpsSource(item.chemin, {
          langue_source: $("langue-source").value,
          langue_cible: $("langue-cible").value,
          modele_ollama: $("modele").value,
          extracteur_pdf: $("extracteur-pdf").value,
          estimation_temps_total: item.eta ?? null,
        }));
        item.jobId = data.job_id;
        item.stage = "lance";
        item.statutJob = "en_attente";
      } catch (e) {
        item.stage = "erreur";
        item.recommandation = String(e.message || e);
      }
    }
    lotEnPause = false;
    rendreLot();
    demarrerPolling();
  }

  // ── Suivi (polling par fichier via check-resume) ────────────────────────────

  async function pollLot() {
    const actifs = lot.filter(f => f.stage === "lance");
    if (actifs.length === 0) { arreterPolling(); rendreLot(); return; }

    for (const item of actifs) {
      try {
        const etat = await apiPost("/check-resume", corpsSource(item.chemin));
        if (!etat) continue;
        item.statutJob = etat.statut;
        item.sections = `${etat.derniere_section_completee}/${etat.total_sections}`;
        item.pct = etat.total_sections > 0
          ? Math.round((etat.derniere_section_completee / etat.total_sections) * 100)
          : 0;
        if (etat.statut === "termine") {
          item.stage = "termine";
          item.pct = 100;
          document.dispatchEvent(new CustomEvent("traduction-terminee"));
        } else if (etat.statut === "erreur") {
          item.stage = "erreur";
          item.recommandation = (etat.erreurs || []).slice(-1)[0] || "Erreur du job";
        } else if (etat.statut === "annule") {
          item.stage = "erreur";
          item.recommandation = "Job annulé";
        }
      } catch { /* on retentera au prochain tick */ }
    }
    rendreLot();
  }

  function demarrerPolling() {
    if (pollTimer) return;
    pollLot();
    pollTimer = setInterval(pollLot, 2000);
  }

  function arreterPolling() {
    clearInterval(pollTimer);
    pollTimer = null;
  }

  // ── Pause / reprise globale ─────────────────────────────────────────────────

  async function basculerPauseLot() {
    const actifs = lot.filter(f => f.stage === "lance");
    if (lotEnPause) {
      // Reprise : relance chaque job en pause via resume=true
      for (const item of actifs.filter(f => f.statutJob === "en_pause")) {
        try {
          const data = await apiPost("/translate", corpsSource(item.chemin, {
            langue_source: $("langue-source").value,
            langue_cible: $("langue-cible").value,
            modele_ollama: $("modele").value,
            extracteur_pdf: $("extracteur-pdf").value,
            resume: true,
          }));
          item.jobId = data.job_id;
          item.statutJob = "en_attente";
        } catch { /* réessayable */ }
      }
      lotEnPause = false;
      demarrerPolling();
    } else {
      for (const item of actifs) {
        if (!item.jobId) continue;
        try { await fetch(`${API_BASE}/job/${item.jobId}/pause`, { method: "POST" }); } catch { /* poll détectera */ }
      }
      lotEnPause = true;
    }
    rendreLot();
  }

  // ── Planification du lot ────────────────────────────────────────────────────

  async function planifierLot() {
    const prets = lot.filter(f => f.stage === "pret");
    if (prets.length === 0) { alert("Aucun fichier prêt à planifier."); return; }
    if (!$("plan-heure").value) { alert("Choisis la date et l'heure d'exécution."); return; }
    try {
      const data = await apiPost("/schedule/batch", {
        chemins: prets.map(f => f.chemin),
        executer_a: new Date($("plan-heure").value).toISOString(),
        langue_source: $("langue-source").value,
        langue_cible: $("langue-cible").value,
        modele_ollama: $("modele").value,
        extracteur_pdf: $("extracteur-pdf").value,
      });
      $("plan-statut").textContent = `✅ ${data.jobs.length} fichier(s) planifié(s)`;
      setTimeout(() => { $("plan-statut").textContent = ""; }, 4000);
      lot = lot.filter(f => f.stage !== "pret");
      rendreLot();
      rafraichirPlanifies();
    } catch (e) {
      $("plan-statut").textContent = `❌ ${e.message}`;
    }
  }

  function formaterDateISO(iso) {
    try {
      return new Date(iso).toLocaleString("fr-CA", { dateStyle: "short", timeStyle: "short" });
    } catch { return iso; }
  }

  async function rafraichirPlanifies() {
    let jobs = [];
    try {
      jobs = (await apiGet("/scheduled/tous")).jobs;
    } catch { return; }

    jobs.sort((a, b) => (b.cree_a || "").localeCompare(a.cree_a || ""));
    $("zone-planifies").hidden = jobs.length === 0;
    const tbody = $("tbody-planifies");
    tbody.innerHTML = "";
    for (const job of jobs) {
      const tr = document.createElement("tr");
      const tdFichier = document.createElement("td");
      tdFichier.textContent = nomFichier(job.chemin_pdf);
      tdFichier.title = job.chemin_pdf;
      const tdQuand = document.createElement("td");
      tdQuand.textContent = formaterDateISO(job.executer_a);
      const tdStatut = document.createElement("td");
      tdStatut.textContent = job.statut === "planifie" ? "🕐 Planifié"
        : job.statut === "annule" ? "✕ Annulé" : "▶ Déclenché";
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
      tbody.appendChild(tr);
    }
  }

  // ── Rendu ───────────────────────────────────────────────────────────────────

  const PILLS = {
    analyse:  ["Analyse…", "pill-neutre"],
    pret:     ["Prêt", "pill-accent"],
    probleme: ["Problème", "pill-attention"],
    lance:    ["En cours…", "pill-attention"],
    termine:  ["Terminé", "pill-succes"],
    erreur:   ["Erreur", "pill-erreur"],
  };

  function rendreLot() {
    $("zone-lot").hidden = lot.length === 0;
    const prets = lot.filter(f => f.stage === "pret").length;
    const termines = lot.filter(f => f.stage === "termine").length;
    $("lot-compte").textContent = `${lot.length} fichier${lot.length > 1 ? "s" : ""} dans le lot`;
    $("lot-prets").textContent = termines === lot.length && lot.length > 0
      ? "Tous terminés"
      : `${prets} prêt${prets > 1 ? "s" : ""} à traduire`;

    const btnLancer = $("bouton-lancer-lot");
    btnLancer.disabled = prets === 0;
    btnLancer.textContent = prets > 0 ? `Lancer la traduction (${prets})` : "Lancer la traduction";
    const enCours = lot.some(f => f.stage === "lance");
    $("bouton-pause-lot").hidden = !enCours;
    $("bouton-pause-lot").textContent = lotEnPause ? "▸ Reprendre" : "⏸ Pause";

    elListe.innerHTML = "";
    for (const item of lot) {
      const ligne = document.createElement("div");
      ligne.className = "lot-ligne carte";

      const entete = document.createElement("div");
      entete.className = "lot-ligne-entete";

      const badge = document.createElement("span");
      badge.className = "badge-type";
      badge.textContent = item.type;

      const nom = document.createElement("span");
      nom.className = "lot-nom";
      nom.textContent = nomFichier(item.chemin);
      nom.title = item.chemin;

      const [pillTexte, pillClasse] = PILLS[item.stage] || [item.stage, "pill-neutre"];
      const pill = document.createElement("span");
      pill.className = `pill ${pillClasse}`;
      pill.textContent = item.stage === "lance" && item.statutJob === "en_attente"
        ? "En file…" : item.stage === "lance" && item.statutJob === "en_pause"
        ? "En pause" : pillTexte;

      entete.append(badge, nom, pill);

      if (item.stage !== "lance") {
        const retirer = document.createElement("button");
        retirer.className = "lot-retirer";
        retirer.textContent = "✕";
        retirer.title = "Retirer du lot";
        retirer.addEventListener("click", () => {
          lot = lot.filter(f => f.id !== item.id);
          rendreLot();
        });
        entete.appendChild(retirer);
      }
      ligne.appendChild(entete);

      if (item.stage === "analyse") {
        const info = document.createElement("div");
        info.className = "lot-info";
        info.innerHTML = '<span class="dot-pulse"></span> Analyse en cours…';
        ligne.appendChild(info);
      } else if (item.stage === "pret" || item.stage === "probleme") {
        const info = document.createElement("div");
        info.className = "lot-info";
        const morceaux = [];
        if (item.qualite) morceaux.push(`Qualité : ${item.qualite}`);
        if (item.eta != null) morceaux.push(`≈ ${formaterDuree(item.eta)}`);
        if (item.chapitres != null) morceaux.push(`${item.chapitres} chapitre${item.chapitres > 1 ? "s" : ""}`);
        info.textContent = morceaux.join("   ·   ");
        ligne.appendChild(info);
        if (item.stage === "probleme" && item.recommandation) {
          const reco = document.createElement("div");
          reco.className = "lot-info erreur";
          reco.textContent = `⚠ ${item.recommandation}`;
          ligne.appendChild(reco);
        }
      } else if (item.stage === "lance" || item.stage === "termine") {
        const barre = document.createElement("div");
        barre.className = "barre-conteneur barre-fine";
        const prog = document.createElement("div");
        prog.className = "barre-progression";
        prog.style.width = `${item.pct}%`;
        if (item.stage === "termine") prog.classList.add("barre-succes");
        barre.appendChild(prog);
        ligne.appendChild(barre);
        if (item.sections) {
          const info = document.createElement("div");
          info.className = "lot-info";
          info.textContent = `${item.sections} sections`;
          ligne.appendChild(info);
        }
      } else if (item.stage === "erreur" && item.recommandation) {
        const info = document.createElement("div");
        info.className = "lot-info erreur";
        info.textContent = `⚠ ${item.recommandation}`;
        ligne.appendChild(info);
      }

      elListe.appendChild(ligne);
    }
  }

  // ── Écouteurs ───────────────────────────────────────────────────────────────

  $("bouton-ajouter-lot").addEventListener("click", ajouterAuLot);
  $("import-chemin").addEventListener("keydown", (e) => { if (e.key === "Enter") ajouterAuLot(); });
  $("bouton-lancer-lot").addEventListener("click", lancerLot);
  $("bouton-pause-lot").addEventListener("click", basculerPauseLot);
  $("bouton-planifier").addEventListener("click", planifierLot);
  $("bouton-planifier-toggle").addEventListener("click", () => {
    $("zone-planifier").hidden = !$("zone-planifier").hidden;
  });

  // Pré-remplit l'heure d'exécution à ce soir 23 h
  (function initHeure() {
    const d = new Date();
    d.setHours(23, 0, 0, 0);
    const pad = (n) => String(n).padStart(2, "0");
    $("plan-heure").value =
      `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  })();

  document.addEventListener("backend-connecte", rafraichirPlanifies);
  setInterval(rafraichirPlanifies, 10000);
})();
