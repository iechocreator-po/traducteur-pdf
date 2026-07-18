// Module A — « Nouveau document » : lot multi-fichiers.
// Ajout par chemin → analyse auto → lancement en lot (file séquentielle côté
// backend : un seul job Ollama à la fois, progression individuelle par fichier).

(() => {
  // {id, chemin, type, stage: analyse|pret|probleme|lance|termine|erreur,
  //  qualite, eta, chapitres, recommandation, jobId, statutJob, pct, sections,
  //  listeChapitres, chapitresCoches, chapitresOuverts, chapitresChargement}
  let lot = [];
  let pollTimer = null;
  let lotEnPause = false;

  const elListe = $("liste-lot");

  // ── Ajout au lot ────────────────────────────────────────────────────────────

  function ajouterAuLot(chemin) {
    chemin = (chemin || "").trim();
    if (!chemin) { alert("Indique le chemin absolu d'un fichier .pdf ou .md."); return; }
    if (lot.some(f => f.chemin === chemin)) { return; } // déjà présent — silencieux (multi-ajout)

    const item = {
      id: `f${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      chemin,
      type: estMarkdown(chemin) ? "MD" : "PDF",
      stage: "analyse",
      qualite: null, eta: null, chapitres: null, recommandation: null,
      jobId: null, statutJob: null, pct: 0, sections: "",
      listeChapitres: null, chapitresCoches: null,
      chapitresOuverts: false, chapitresChargement: false,
    };
    lot.push(item);
    rendreLot();
    analyserItem(item);
  }

  // ── Upload navigateur (Parcourir + glisser-déposer) ─────────────────────────
  // Le navigateur ne révèle jamais le chemin disque d'un fichier : on envoie ses
  // octets à /api/upload, qui retourne un chemin serveur réinjecté dans le lot.

  async function televerser(file) {
    const statut = $("import-upload-statut");
    statut.textContent = `⏳ Envoi de ${file.name}…`;
    const form = new FormData();
    form.append("fichier", file);
    try {
      const rep = await fetch(`${API_BASE}/upload`, { method: "POST", body: form });
      const data = await rep.json().catch(() => ({}));
      if (!rep.ok) throw new Error(data.detail || `Erreur HTTP ${rep.status}`);
      statut.textContent = "";
      ajouterAuLot(data.chemin);
    } catch (e) {
      statut.textContent = `❌ ${file.name} : ${e.message}`;
    }
  }

  async function televerserPlusieurs(fileList) {
    for (const file of fileList) {
      const nom = file.name.toLowerCase();
      if (!nom.endsWith(".pdf") && !nom.endsWith(".md") && !nom.endsWith(".markdown")) {
        $("import-upload-statut").textContent = `❌ ${file.name} : seuls .pdf et .md sont acceptés.`;
        continue;
      }
      await televerser(file);
    }
  }

  async function analyserItem(item) {
    try {
      if (item.type === "MD") {
        // Pas d'analyse LLM pour un Markdown : comptage des chapitres suffit
        const data = await apiPost("/chapitres", corpsSource(item.chemin));
        item.qualite = "Markdown";
        item.eta = null;
        item.chapitres = data.chapitres.length;
        // La liste complète est déjà là : le sélecteur de chapitres n'aura pas
        // à la re-demander (contrairement aux PDF, chargés à l'ouverture).
        initSelectionChapitres(item, data.chapitres);
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

  // ── Sélection fine des chapitres à traduire ─────────────────────────────────
  // Tout coché (défaut) → traduction complète classique (cache et reprise
  // intacts) : chapitres_selectionnes n'est envoyé au backend QUE pour une
  // sélection partielle (mode « ajout » par chapitres). Pour un PDF, la liste
  // n'est chargée qu'à l'ouverture du sélecteur — l'extraction peut être longue
  // pour un PDF sans signets.

  function initSelectionChapitres(item, chapitres) {
    item.listeChapitres = chapitres;
    item.chapitresCoches = new Set(chapitres.map(c => c.index));
  }

  function selectionPartielle(item) {
    return item.listeChapitres != null
      && item.chapitresCoches.size > 0
      && item.chapitresCoches.size < item.listeChapitres.length;
  }

  // Un fichier dont tous les chapitres sont décochés est exclu du lancement.
  function itemLancable(item) {
    if (item.stage !== "pret") return false;
    return item.listeChapitres == null || item.chapitresCoches.size > 0;
  }

  async function chargerChapitresItem(item) {
    item.chapitresChargement = true;
    rendreLot();
    try {
      const data = await apiPost("/chapitres", corpsSource(item.chemin, {
        extracteur_pdf: $("extracteur-pdf").value,
      }));
      initSelectionChapitres(item, data.chapitres);
    } catch (e) {
      item.chapitresOuverts = false;
      alert(`Impossible de lister les chapitres : ${e.message}`);
    }
    item.chapitresChargement = false;
    rendreLot();
  }

  // ── Lancement du lot ────────────────────────────────────────────────────────

  async function lancerLot() {
    if (!(await exigerSante())) return;
    const prets = lot.filter(itemLancable);
    if (prets.length === 0) return;

    for (const item of prets) {
      try {
        const data = await apiPost("/translate", corpsSource(item.chemin, {
          langue_source: $("langue-source").value,
          langue_cible: $("langue-cible").value,
          modele_ollama: $("modele").value,
          extracteur_pdf: $("extracteur-pdf").value,
          estimation_temps_total: item.eta ?? null,
          ...(selectionPartielle(item)
            ? { chapitres_selectionnes: [...item.chapitresCoches].sort((a, b) => a - b) }
            : {}),
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

  // Sélecteur dépliable « Chapitres à traduire » d'un fichier du lot.
  function rendreSelecteurChapitres(item) {
    const bloc = document.createElement("div");
    bloc.className = "lot-chapitres";

    const entete = document.createElement("div");
    entete.className = "lot-chapitres-entete";

    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "bouton-lien";
    const resume = item.listeChapitres == null ? ""
      : selectionPartielle(item) ? ` (${item.chapitresCoches.size}/${item.listeChapitres.length} sélectionnés)`
      : item.chapitresCoches.size === 0 ? " (aucun)"
      : " (tous)";
    toggle.textContent = `${item.chapitresOuverts ? "▾" : "▸"} Chapitres à traduire${resume}`;
    toggle.addEventListener("click", () => {
      item.chapitresOuverts = !item.chapitresOuverts;
      if (item.chapitresOuverts && item.listeChapitres == null && !item.chapitresChargement) {
        chargerChapitresItem(item);
      } else {
        rendreLot();
      }
    });
    entete.appendChild(toggle);

    if (item.chapitresOuverts && item.listeChapitres != null) {
      for (const [label, coche] of [["Tout", true], ["Aucun", false]]) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "bouton-lien";
        btn.textContent = label;
        btn.addEventListener("click", () => {
          item.chapitresCoches = coche ? new Set(item.listeChapitres.map(c => c.index)) : new Set();
          rendreLot();
        });
        entete.appendChild(btn);
      }
    }
    bloc.appendChild(entete);

    if (item.chapitresOuverts) {
      if (item.chapitresChargement) {
        const aide = document.createElement("p");
        aide.className = "aide";
        aide.textContent = "⏳ Chargement des chapitres…";
        bloc.appendChild(aide);
      } else if (item.listeChapitres != null) {
        const liste = document.createElement("div");
        liste.className = "lot-chapitres-liste";
        for (const chap of item.listeChapitres) {
          const ligne = document.createElement("label");
          ligne.className = "lot-chap-ligne";
          ligne.style.paddingLeft = `${4 + (Math.max(chap.niveau, 1) - 1) * 14}px`;
          const check = document.createElement("input");
          check.type = "checkbox";
          check.checked = item.chapitresCoches.has(chap.index);
          check.addEventListener("change", () => {
            if (check.checked) item.chapitresCoches.add(chap.index);
            else item.chapitresCoches.delete(chap.index);
            rendreLot();
          });
          const titre = document.createElement("span");
          titre.textContent = chap.titre;
          ligne.append(check, titre);
          liste.appendChild(ligne);
        }
        bloc.appendChild(liste);
        if (item.chapitresCoches.size === 0) {
          const alerte = document.createElement("p");
          alerte.className = "aide erreur";
          alerte.textContent = "⚠ Aucun chapitre coché — ce fichier ne sera pas traduit.";
          bloc.appendChild(alerte);
        }
      }
    }
    return bloc;
  }

  function rendreLot() {
    $("zone-lot").hidden = lot.length === 0;
    const prets = lot.filter(itemLancable).length;
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
        if (item.stage === "pret" && (item.chapitres ?? 0) > 0) {
          ligne.appendChild(rendreSelecteurChapitres(item));
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

  $("bouton-ajouter-lot").addEventListener("click", () => {
    ajouterAuLot($("import-chemin").value);
    $("import-chemin").value = "";
  });
  $("import-chemin").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { ajouterAuLot($("import-chemin").value); $("import-chemin").value = ""; }
  });

  // Parcourir : ouvre le sélecteur natif puis uploade.
  $("bouton-parcourir").addEventListener("click", () => $("import-fichier").click());
  $("import-fichier").addEventListener("change", (e) => {
    televerserPlusieurs(e.target.files);
    e.target.value = ""; // permet de re-sélectionner le même fichier
  });

  // Glisser-déposer sur la zone. Un preventDefault global évite qu'un fichier
  // lâché à côté fasse naviguer le navigateur vers lui (et perde le lot).
  const zone = $("dropzone");
  ["dragover", "drop"].forEach((evt) =>
    window.addEventListener(evt, (e) => e.preventDefault())
  );
  zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("is-survol"); });
  zone.addEventListener("dragleave", (e) => {
    if (e.target === zone) zone.classList.remove("is-survol");
  });
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.classList.remove("is-survol");
    if (e.dataTransfer.files.length) televerserPlusieurs(e.dataTransfer.files);
  });
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

// ── Reprendre une traduction (jobs non terminés, persistants) ─────────────────
// Section distincte du lot en mémoire : elle est alimentée par le backend
// (GET /jobs/reprenables), donc elle survit au rechargement de page et aux
// sessions. Chaque document non terminé (en cours / en pause / interrompu /
// erreur / annulé) peut être repris (⏯) ou retiré de la liste (🗑).
(() => {
  const STATUTS_LABEL = {
    en_cours: ["En cours…", "pill-attention"],
    en_attente: ["En file…", "pill-neutre"],
    en_pause: ["En pause", "pill-attention"],
    erreur: ["Interrompu", "pill-erreur"],
    annule: ["Annulé", "pill-erreur"],
  };

  let pollTimer = null;

  async function rafraichir() {
    let docs = [];
    try {
      docs = (await apiGet("/jobs/reprenables")).documents || [];
    } catch { return; }

    $("zone-reprendre").hidden = docs.length === 0;
    const liste = $("liste-reprendre");
    liste.innerHTML = "";

    for (const doc of docs) {
      liste.appendChild(rendreLigne(doc));
    }

    // Poll tant qu'un document bouge encore (en cours / en file).
    const actif = docs.some(d => d.statut === "en_cours" || d.statut === "en_attente");
    if (actif && !pollTimer) pollTimer = setInterval(rafraichir, 2500);
    if (!actif && pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  function rendreLigne(doc) {
    const ligne = document.createElement("div");
    ligne.className = "reprendre-ligne carte";

    const entete = document.createElement("div");
    entete.className = "lot-ligne-entete";

    const badge = document.createElement("span");
    badge.className = "badge-type";
    badge.textContent = estMarkdown(doc.chemin_source) ? "MD" : "PDF";

    const nom = document.createElement("span");
    nom.className = "lot-nom";
    nom.textContent = doc.nom || nomFichier(doc.chemin_source);
    nom.title = doc.chemin_source;

    const [txt, classe] = STATUTS_LABEL[doc.statut] || [doc.statut, "pill-neutre"];
    const pill = document.createElement("span");
    pill.className = `pill ${classe}`;
    pill.textContent = txt;

    entete.append(badge, nom, pill);
    ligne.appendChild(entete);

    const total = doc.total_sections || 0;
    const faites = doc.sections_completees || 0;
    if (total > 0) {
      const barre = document.createElement("div");
      barre.className = "barre-conteneur barre-fine";
      const prog = document.createElement("div");
      prog.className = "barre-progression";
      prog.style.width = `${Math.round((faites / total) * 100)}%`;
      barre.appendChild(prog);
      ligne.appendChild(barre);
    }

    const info = document.createElement("div");
    info.className = "lot-info";
    const morceaux = [];
    if (total > 0) morceaux.push(`${faites}/${total} morceaux`);
    if (doc.nb_sections_echouees) morceaux.push(`${doc.nb_sections_echouees} en échec`);
    info.textContent = morceaux.join("   ·   ");
    ligne.appendChild(info);

    const actions = document.createElement("div");
    actions.className = "reprendre-actions";

    const reprendre = document.createElement("button");
    reprendre.className = "bouton-mini";
    reprendre.textContent = doc.statut === "en_cours" || doc.statut === "en_attente"
      ? "⏳ En cours" : "⏯ Reprendre";
    reprendre.disabled = doc.statut === "en_cours" || doc.statut === "en_attente";
    reprendre.addEventListener("click", () => reprendreDoc(doc, reprendre));
    actions.appendChild(reprendre);

    const supprimer = document.createElement("button");
    supprimer.className = "bouton-mini bouton-danger";
    supprimer.textContent = "🗑 Supprimer";
    supprimer.title = "Retirer de la liste (les fichiers sur disque sont conservés)";
    supprimer.addEventListener("click", () => supprimerDoc(doc));
    actions.appendChild(supprimer);

    ligne.appendChild(actions);
    return ligne;
  }

  async function reprendreDoc(doc, bouton) {
    if (!(await exigerSante())) return;
    bouton.disabled = true;
    bouton.textContent = "⏳ Reprise…";
    try {
      // Les options suivent le DOCUMENT (registre), pas les menus de l'Import.
      await apiPost("/translate", corpsSource(doc.chemin_source, {
        langue_source: doc.langue_source,
        langue_cible: doc.langue_cible,
        modele_ollama: doc.modele,
        resume: true,
      }));
    } catch (e) {
      bouton.disabled = false;
      bouton.textContent = "⏯ Reprendre";
      alert(`Impossible de reprendre : ${e.message}`);
      return;
    }
    rafraichir();
  }

  async function supprimerDoc(doc) {
    if (!confirm(`Retirer « ${doc.nom || nomFichier(doc.chemin_source)} » de la liste ?\n\nLes fichiers déjà traduits sur le disque sont conservés.`)) return;
    try {
      const rep = await fetch(`${API_BASE}/bibliotheque`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chemin_sortie: doc.chemin_sortie }),
      });
      if (!rep.ok) {
        const data = await rep.json().catch(() => ({}));
        throw new Error(data.detail || `Erreur HTTP ${rep.status}`);
      }
    } catch (e) {
      alert(`Suppression impossible : ${e.message}`);
      return;
    }
    rafraichir();
  }

  // Rafraîchit à la connexion backend, à l'affichage du module Import, et après
  // qu'une traduction se termine (elle quitte alors la liste).
  document.addEventListener("backend-connecte", rafraichir);
  document.addEventListener("module-affiche", (e) => { if (e.detail === "import") rafraichir(); });
  document.addEventListener("traduction-terminee", rafraichir);
})();
