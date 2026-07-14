// Module B — « Bibliothèque » : lecture des documents traduits, chapitre par
// chapitre, avec lecture audio (TTS) et panneau IA (points clés + quiz servis
// par le backend Étude).

(() => {
  let docs = [];
  let docActif = null;       // entrée du registre bibliothèque
  let chapitres = [];
  let chapActif = null;
  let ficheParChapitre = {}; // index → FicheChapitre (depuis /etude/statut)
  let pollFiche = null;
  let pollAudio = null;

  const audio = $("audio-el");

  // ── Documents (sidebar) ─────────────────────────────────────────────────────

  async function chargerDocs() {
    try {
      docs = (await apiGet("/bibliotheque")).documents;
    } catch {
      docs = [];
    }
    rendreDocs();
  }

  function rendreDocs() {
    const zone = $("biblio-docs");
    zone.innerHTML = "";
    if (docs.length === 0) {
      const vide = document.createElement("p");
      vide.className = "aide";
      vide.textContent = "Aucun document traduit pour l'instant.";
      zone.appendChild(vide);
      return;
    }
    for (const doc of docs) {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "sidebar-item";
      if (docActif && doc.chemin_sortie === docActif.chemin_sortie) item.classList.add("is-active");

      const badge = document.createElement("span");
      badge.className = "badge-type badge-mini";
      badge.textContent = estMarkdown(doc.chemin_source) ? "MD" : "PDF";

      const nom = document.createElement("span");
      nom.className = "sidebar-item-nom";
      nom.textContent = doc.nom;
      nom.title = doc.chemin_sortie;

      item.append(badge, nom);

      if (doc.statut !== "termine") {
        const statut = document.createElement("span");
        statut.className = "pill pill-attention pill-mini";
        statut.textContent = doc.statut === "en_cours" ? `${doc.sections_completees}/${doc.total_sections}` : doc.statut;
        item.appendChild(statut);
      }

      item.addEventListener("click", () => selectionnerDoc(doc));
      zone.appendChild(item);
    }
  }

  // ── Sélection d'un document ─────────────────────────────────────────────────

  async function selectionnerDoc(doc) {
    if (doc.statut !== "termine") {
      alert("Ce document est encore en cours de traduction — il sera lisible une fois terminé.");
      return;
    }
    docActif = doc;
    chapActif = null;
    ficheParChapitre = {};
    arreterPollFiche();
    rendreDocs();

    $("lecture-bandeau").hidden = false;
    $("lecture-badge").textContent = estMarkdown(doc.chemin_source) ? "MD" : "PDF";
    $("lecture-doc-nom").textContent = doc.nom;
    $("lecture-vide").hidden = true;
    $("barre-audio").hidden = false;

    try {
      const data = await apiPost("/chapitres", { chemin_md: doc.chemin_sortie });
      chapitres = data.chapitres;
    } catch {
      chapitres = [];
    }
    rendreChapitres();
    if (chapitres.length > 0) {
      selectionnerChapitre(chapitres[0]);
    } else {
      // Document sans titres Markdown : lecture du fichier entier impossible par
      // chapitre — on l'affiche comme un chapitre unique via l'index 0 absent.
      $("lecture-titre").hidden = false;
      $("lecture-titre").textContent = doc.nom;
      $("lecture-texte").textContent = "Ce document ne contient aucun titre de chapitre — utilise la lecture audio ou ouvre le fichier directement : " + doc.chemin_sortie;
    }

    chargerFicheExistante();
    rafraichirAudio();
  }

  function rendreChapitres() {
    const zone = $("biblio-chapitres");
    zone.innerHTML = "";
    if (!docActif) return;
    if (chapitres.length === 0) {
      const vide = document.createElement("p");
      vide.className = "aide";
      vide.textContent = "Aucun titre détecté dans ce document.";
      zone.appendChild(vide);
      return;
    }
    for (const chap of chapitres) {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "sidebar-item";
      item.style.paddingLeft = `${8 + (chap.niveau - 1) * 12}px`;
      if (chapActif && chap.index === chapActif.index) item.classList.add("is-active");
      item.textContent = chap.titre;
      item.title = chap.titre;
      item.addEventListener("click", () => selectionnerChapitre(chap));
      zone.appendChild(item);
    }
  }

  // ── Lecture d'un chapitre ───────────────────────────────────────────────────

  async function selectionnerChapitre(chap) {
    chapActif = chap;
    rendreChapitres();
    $("lecture-titre").hidden = false;
    $("lecture-titre").textContent = chap.titre;
    $("lecture-texte").textContent = "Chargement…";
    try {
      const data = await apiPost("/chapitres/contenu", {
        chemin_md: docActif.chemin_sortie,
        index: chap.index,
      });
      rendreContenu(data.contenu);
    } catch (e) {
      $("lecture-texte").textContent = `Impossible de charger le chapitre : ${e.message}`;
    }
    rendreFiche();
  }

  // Rendu Markdown minimal et sûr (DOM construit en textContent, jamais innerHTML)
  function rendreContenu(markdown) {
    const zone = $("lecture-texte");
    zone.innerHTML = "";
    const lignes = markdown.split("\n");
    let paragraphe = [];
    let premierTitreSaute = false;

    const viderParagraphe = () => {
      if (paragraphe.length === 0) return;
      const p = document.createElement("p");
      p.textContent = paragraphe.join(" ");
      zone.appendChild(p);
      paragraphe = [];
    };

    for (const ligne of lignes) {
      const titre = ligne.match(/^(#{1,6})\s+(.*)/);
      if (titre) {
        if (!premierTitreSaute) { premierTitreSaute = true; continue; } // déjà affiché en h2
        viderParagraphe();
        const h = document.createElement(titre[1].length <= 2 ? "h3" : "h4");
        h.textContent = titre[2];
        zone.appendChild(h);
      } else if (ligne.trim() === "") {
        viderParagraphe();
      } else {
        paragraphe.push(ligne.trim());
      }
    }
    viderParagraphe();
  }

  // ── Barre audio ─────────────────────────────────────────────────────────────

  async function rafraichirAudio() {
    arreterPollAudio();
    $("audio-play").disabled = true;
    $("audio-telecharger").hidden = true;
    $("audio-generer").hidden = false;
    $("audio-statut").textContent = "";
    $("audio-temps").textContent = "—";
    $("audio-progress").style.width = "0%";
    audio.removeAttribute("src");

    try {
      const etat = await apiGet(`/tts/statut?chemin_md=${encodeURIComponent(docActif.chemin_sortie)}`);
      if (etat && etat.statut === "termine") {
        activerLecteur(etat.chemin_sortie);
      } else if (etat && (etat.statut === "en_cours" || etat.statut === "en_attente")) {
        $("audio-generer").hidden = true;
        demarrerPollAudio();
      }
    } catch { /* pas d'audio pour ce document */ }
  }

  function activerLecteur(cheminWav) {
    const url = `${API_BASE}/tts/audio?chemin_wav=${encodeURIComponent(cheminWav)}`;
    audio.src = url;
    $("audio-play").disabled = false;
    $("audio-generer").hidden = true;
    $("audio-telecharger").hidden = false;
    $("audio-telecharger").href = url;
    $("audio-statut").textContent = "";
  }

  async function genererAudio() {
    if (!docActif) return;
    if (!(await exigerSante())) return;
    try {
      await apiPost("/tts", {
        chemin_md: docActif.chemin_sortie,
        moteur: $("tts-moteur").value,
        voix: $("tts-voix").value,
        // Voix clonée : la langue de synthèse suit la langue cible du document.
        langue: docActif.langue_cible || "français",
      });
      $("audio-generer").hidden = true;
      demarrerPollAudio();
    } catch (e) {
      $("audio-statut").textContent = `❌ ${e.message}`;
    }
  }

  async function pollStatutAudio() {
    if (!docActif) { arreterPollAudio(); return; }
    try {
      const etat = await apiGet(`/tts/statut?chemin_md=${encodeURIComponent(docActif.chemin_sortie)}`);
      if (!etat) return;
      const pct = etat.total_sections > 0
        ? Math.round((etat.sections_completees / etat.total_sections) * 100) : 0;
      if (etat.statut === "en_attente") {
        $("audio-statut").textContent = "⏳ En file d'attente…";
      } else if (etat.statut === "en_cours") {
        $("audio-statut").textContent = `🔊 Génération — ${pct}%`;
      } else if (etat.statut === "termine") {
        arreterPollAudio();
        activerLecteur(etat.chemin_sortie);
      } else {
        arreterPollAudio();
        $("audio-generer").hidden = false;
        $("audio-statut").textContent = etat.statut === "erreur" ? `❌ ${etat.erreur || "Erreur"}` : "✕ Annulé";
      }
    } catch { /* prochain tick */ }
  }

  function demarrerPollAudio() {
    if (pollAudio) return;
    pollStatutAudio();
    pollAudio = setInterval(pollStatutAudio, 2000);
  }

  function arreterPollAudio() {
    clearInterval(pollAudio);
    pollAudio = null;
  }

  $("audio-play").addEventListener("click", () => {
    if (audio.paused) audio.play(); else audio.pause();
  });
  audio.addEventListener("play", () => { $("audio-play").textContent = "❚❚"; });
  audio.addEventListener("pause", () => { $("audio-play").textContent = "▸"; });
  audio.addEventListener("timeupdate", () => {
    if (!audio.duration) return;
    $("audio-progress").style.width = `${(audio.currentTime / audio.duration) * 100}%`;
    $("audio-temps").textContent = `${formaterDuree(audio.currentTime)} / ${formaterDuree(audio.duration)}`;
  });
  $("audio-progress-conteneur").addEventListener("click", (e) => {
    if (!audio.duration) return;
    const rect = e.currentTarget.getBoundingClientRect();
    audio.currentTime = ((e.clientX - rect.left) / rect.width) * audio.duration;
  });
  $("audio-generer").addEventListener("click", genererAudio);

  // ── Panneau IA (points clés + quiz via le backend Étude) ────────────────────

  async function chargerFicheExistante() {
    try {
      const etat = await apiGet(`/etude/statut?chemin_source=${encodeURIComponent(docActif.chemin_sortie)}`);
      if (etat) {
        for (const chap of etat.chapitres) {
          if (chap.etape === "termine") ficheParChapitre[chap.index] = chap;
        }
        if (etat.statut === "en_cours" || etat.statut === "en_attente") demarrerPollFiche();
      }
    } catch { /* pas de fiche */ }
    rendreFiche();
  }

  async function genererFiche() {
    if (!docActif || !chapActif) return;
    if (!(await exigerSante())) return;
    try {
      await apiPost("/etude", {
        chemin_md: docActif.chemin_sortie,
        chapitres_selectionnes: [chapActif.index],
        modele_ollama: $("modele").value,
        nb_points: 5,
        nb_questions: 3,
        langue_fiche: $("langue-cible").value,
      });
      $("ia-statut").textContent = "⏳ Génération en cours…";
      $("ia-generer").disabled = true;
      demarrerPollFiche();
    } catch (e) {
      $("ia-statut").textContent = `❌ ${e.message}`;
    }
  }

  async function pollStatutFiche() {
    if (!docActif) { arreterPollFiche(); return; }
    try {
      const etat = await apiGet(`/etude/statut?chemin_source=${encodeURIComponent(docActif.chemin_sortie)}`);
      if (!etat) return;
      for (const chap of etat.chapitres) {
        if (chap.etape === "termine") ficheParChapitre[chap.index] = chap;
      }
      if (["termine", "erreur", "annule"].includes(etat.statut)) {
        arreterPollFiche();
        $("ia-statut").textContent = etat.statut === "termine" ? "" : `❌ ${etat.erreurs.slice(-1)[0] || "Erreur"}`;
        rendreFiche();
      } else {
        const chapEnCours = etat.chapitres.find(c => ["points", "questions"].includes(c.etape));
        $("ia-statut").textContent = chapEnCours
          ? `⏳ ${chapEnCours.etape === "points" ? "Points clés" : "Questions"} en cours…`
          : "⏳ En file d'attente…";
      }
    } catch { /* prochain tick */ }
  }

  function demarrerPollFiche() {
    if (pollFiche) return;
    pollStatutFiche();
    pollFiche = setInterval(pollStatutFiche, 2000);
  }

  function arreterPollFiche() {
    clearInterval(pollFiche);
    pollFiche = null;
  }

  function rendreFiche() {
    majBoutonExport();
    const fiche = chapActif ? ficheParChapitre[chapActif.index] : null;
    $("ia-generer").disabled = !chapActif || !!pollFiche;
    $("ia-generer").textContent = fiche ? "↻ Régénérer points clés + quiz" : "Générer les 5 points clés + quiz";

    $("ia-points-zone").hidden = !fiche;
    $("ia-quiz-zone").hidden = !fiche;
    if (!fiche) return;

    const zonePoints = $("ia-points-resultat");
    zonePoints.innerHTML = "";
    for (const point of fiche.points) {
      const ligne = document.createElement("div");
      ligne.className = "ia-point";
      const puce = document.createElement("span");
      puce.className = "ia-puce";
      const texte = document.createElement("span");
      texte.textContent = point;
      ligne.append(puce, texte);
      zonePoints.appendChild(ligne);
    }

    const zoneQuiz = $("ia-quiz-resultat");
    zoneQuiz.innerHTML = "";
    fiche.questions.forEach((q, i) => {
      const carte = document.createElement("div");
      carte.className = "ia-question";
      const question = document.createElement("div");
      question.className = "ia-question-texte";
      question.textContent = `Q${i + 1}. ${q.question}`;
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = "Voir la réponse";
      const reponse = document.createElement("p");
      reponse.textContent = q.reponse;
      details.append(summary, reponse);
      carte.append(question, details);
      zoneQuiz.appendChild(carte);
    });
  }

  $("ia-generer").addEventListener("click", genererFiche);

  // ── Export HTML de la fiche d'étude (flag export_fiche_html) ────────────────

  function majBoutonExport() {
    const dispo = featureFlags.export_fiche_html !== false
      && Object.keys(ficheParChapitre).length > 0;
    $("ia-exporter").hidden = !dispo;
  }

  function echapperHtml(txt) {
    const div = document.createElement("div");
    div.textContent = txt == null ? "" : String(txt);
    return div.innerHTML;
  }

  function construireFicheHtml() {
    const titre = echapperHtml(docActif.nom || "Document");
    const meta = [
      docActif.langue_source && docActif.langue_cible
        ? `${echapperHtml(docActif.langue_source)} → ${echapperHtml(docActif.langue_cible)}` : "",
      docActif.modele ? `Modèle : ${echapperHtml(docActif.modele)}` : "",
      docActif.maj_a ? `Généré le ${echapperHtml(new Date(docActif.maj_a).toLocaleString("fr-CA"))}` : "",
    ].filter(Boolean).join(" · ");

    // Table des matières : tous les chapitres, indentés par niveau.
    const toc = chapitres.map((c) => {
      const aFiche = !!ficheParChapitre[c.index];
      const lien = aFiche
        ? `<a href="#chap-${c.index}">${echapperHtml(c.titre)}</a>`
        : `<span class="sans-fiche">${echapperHtml(c.titre)}</span>`;
      return `<li style="margin-left:${(Math.max(c.niveau, 1) - 1) * 1.2}rem">${lien}</li>`;
    }).join("\n");

    // Sections détaillées : uniquement les chapitres ayant une fiche, dans l'ordre.
    const sections = chapitres
      .filter((c) => ficheParChapitre[c.index])
      .map((c) => {
        const fiche = ficheParChapitre[c.index];
        const points = (fiche.points || [])
          .map((p) => `<li>${echapperHtml(p)}</li>`).join("\n");
        const questions = (fiche.questions || []).map((q, i) => `
          <div class="question">
            <p class="q">Q${i + 1}. ${echapperHtml(q.question)}</p>
            <details><summary>Voir la réponse</summary><p>${echapperHtml(q.reponse)}</p></details>
          </div>`).join("\n");
        return `
        <section id="chap-${c.index}">
          <h2>${echapperHtml(c.titre)}</h2>
          <h3>Points à retenir</h3>
          <ol>${points}</ol>
          <h3>Questions de compréhension</h3>
          ${questions}
        </section>`;
      }).join("\n");

    return `<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fiche d'étude — ${titre}</title>
<style>
  body { font-family: -apple-system, system-ui, sans-serif; line-height: 1.55; max-width: 820px; margin: 2rem auto; padding: 0 1.2rem; color: #1a1a1a; }
  h1 { margin-bottom: .2rem; } .meta { color: #666; font-size: .9rem; margin-bottom: 1.5rem; }
  nav { background: #f4f5f7; border-radius: 8px; padding: 1rem 1.2rem; margin-bottom: 2rem; }
  nav ul { list-style: none; padding: 0; margin: .4rem 0 0; } nav li { margin: .15rem 0; }
  nav a { color: #2563eb; text-decoration: none; } nav a:hover { text-decoration: underline; }
  .sans-fiche { color: #999; }
  section { border-top: 1px solid #e5e7eb; padding-top: 1rem; margin-top: 2rem; }
  h2 { margin-bottom: .3rem; } h3 { margin: 1.1rem 0 .3rem; font-size: 1rem; color: #444; }
  .question { margin: .6rem 0; } .q { font-weight: 600; margin: 0 0 .2rem; }
  details { background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 6px; padding: .5rem .8rem; }
  summary { cursor: pointer; color: #2563eb; } details[open] summary { margin-bottom: .4rem; }
  details p { margin: 0; }
  @media (prefers-color-scheme: dark) {
    body { background: #16181c; color: #e5e7eb; } .meta { color: #9aa0a6; }
    nav { background: #22252b; } .question .q { color: #e5e7eb; }
    section { border-color: #33373e; } h3 { color: #b6bcc4; }
    details { background: #1c1f24; border-color: #33373e; } nav a, summary { color: #6ea8fe; }
  }
</style>
</head>
<body>
  <h1>Fiche d'étude — ${titre}</h1>
  <p class="meta">${meta}</p>
  <nav>
    <strong>Structure des chapitres</strong>
    <ul>${toc}</ul>
  </nav>
  ${sections}
</body>
</html>`;
  }

  function telechargerHtml(html, nomFichier) {
    // Repli (Safari/Firefox) : téléchargement direct dans le dossier par défaut.
    const url = URL.createObjectURL(new Blob([html], { type: "text/html" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = nomFichier;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function exporterFicheHtml() {
    if (!docActif || Object.keys(ficheParChapitre).length === 0) return;
    const html = construireFicheHtml();
    const base = (docActif.nom || "document").replace(/\.[^.]+$/, "");
    const nomFichier = `${base}_fiche_etude.html`;

    // Boîte « Enregistrer sous » native (Chrome/Edge) pour choisir l'emplacement.
    if (window.showSaveFilePicker) {
      try {
        const handle = await window.showSaveFilePicker({
          suggestedName: nomFichier,
          types: [{ description: "Page HTML", accept: { "text/html": [".html"] } }],
        });
        const writable = await handle.createWritable();
        await writable.write(html);
        await writable.close();
      } catch (e) {
        if (e.name === "AbortError") return;       // l'utilisateur a annulé
        telechargerHtml(html, nomFichier);          // autre erreur → repli
      }
      return;
    }
    telechargerHtml(html, nomFichier);
  }

  $("ia-exporter").addEventListener("click", exporterFicheHtml);
  document.addEventListener("flags-charges", majBoutonExport);

  // ── Rafraîchissements ───────────────────────────────────────────────────────

  document.addEventListener("module-affiche", (e) => {
    if (e.detail === "bibliotheque") chargerDocs();
  });
  document.addEventListener("traduction-terminee", chargerDocs);
  document.addEventListener("backend-connecte", chargerDocs);
})();
