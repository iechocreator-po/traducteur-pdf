// Module B — « Bibliothèque » : lecture des documents traduits, chapitre par
// chapitre, avec lecture audio (TTS) et panneau IA (points clés + quiz servis
// par le backend Étude).

(() => {
  let docs = [];
  let docActif = null;       // entrée du registre bibliothèque
  let chapitres = [];
  let chapActif = null;
  let chapitresCoches = new Set(); // index cochés pour la génération de fiches
  let ficheParChapitre = {}; // index → FicheChapitre (depuis /etude/statut)
  let pollFiche = null;
  let pollAudio = null;
  let lectureVisible = false; // texte central masqué par défaut (flag biblio_toggle_contenu)

  const audio = $("audio-el");

  // ── Affichage du texte (flag biblio_toggle_contenu) ────────────────────────
  // En mode avancé, le panneau Résumé & Quiz occupe le centre et la colonne de
  // lecture est masquée par défaut pour alléger l'écran ; le bouton la révèle.
  // Flag off → comportement historique : texte toujours visible, bouton masqué.

  function appliquerLectureVisible() {
    const flagActif = featureFlags.biblio_toggle_contenu !== false;
    const visible = flagActif ? lectureVisible : true;
    $("biblio-toggle-lecture").hidden = !flagActif;
    $("biblio-layout").classList.toggle("lecture-cachee", !visible);
    $("biblio-toggle-lecture").textContent = visible ? "Masquer le texte" : "📖 Afficher le texte";
  }

  // Un clic explicite sur un titre de chapitre = intention de lire → on révèle
  // la colonne (la sélection automatique du premier chapitre, elle, ne le fait pas).
  function montrerLecture() {
    if (lectureVisible) return;
    lectureVisible = true;
    appliquerLectureVisible();
  }

  $("biblio-toggle-lecture").addEventListener("click", () => {
    lectureVisible = !lectureVisible;
    appliquerLectureVisible();
  });
  document.addEventListener("flags-charges", appliquerLectureVisible);
  appliquerLectureVisible();

  // ── Poignées de redimensionnement (sidebar | Résumé & Quiz | lecture) ──────
  // Largeur pilotée par une variable CSS sur <html>, mesurée sur l'élément à
  // gauche de la poignée (glisser à droite l'agrandit). Persistée en
  // localStorage ; double-clic remet la largeur par défaut.

  function initResizer(handleId, varName, mesureElId, defaut, min, max) {
    const poignee = $(handleId);
    let enCours = false;
    let departX = 0;
    let largeurDepart = 0;

    const racine = document.documentElement;
    const sauvegarde = parseInt(localStorage.getItem(varName), 10);
    if (!Number.isNaN(sauvegarde)) racine.style.setProperty(`--${varName}`, `${sauvegarde}px`);

    poignee.addEventListener("mousedown", (e) => {
      enCours = true;
      departX = e.clientX;
      largeurDepart = $(mesureElId).getBoundingClientRect().width;
      poignee.classList.add("is-dragging");
      document.body.style.cursor = "col-resize";
      e.preventDefault();
    });

    window.addEventListener("mousemove", (e) => {
      if (!enCours) return;
      const largeur = Math.min(max, Math.max(min, largeurDepart + (e.clientX - departX)));
      racine.style.setProperty(`--${varName}`, `${largeur}px`);
    });

    window.addEventListener("mouseup", () => {
      if (!enCours) return;
      enCours = false;
      poignee.classList.remove("is-dragging");
      document.body.style.cursor = "";
      localStorage.setItem(varName, Math.round($(mesureElId).getBoundingClientRect().width));
    });

    poignee.addEventListener("dblclick", () => {
      racine.style.removeProperty(`--${varName}`);
      localStorage.removeItem(varName);
    });
  }

  initResizer("biblio-resizer-1", "biblio-w1", "biblio-sidebar", 230, 180, 420);
  initResizer("biblio-resizer-2", "biblio-w2", "biblio-ia", 380, 260, 720);

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

  // Statuts « interrompus mais reprenables » : sortie partielle sur le disque,
  // état reprenable. La Bibliothèque est la console unique de reprise.
  const STATUTS_REPRENABLES = new Set(["erreur", "annule"]);

  async function selectionnerDoc(doc) {
    // Un document interrompu (erreur ou annulé) reste lisible : il peut être
    // traduit à 98 %. On l'ouvre, avec un bandeau pour reprendre.
    if (doc.statut !== "termine" && !STATUTS_REPRENABLES.has(doc.statut)) {
      alert("Ce document est encore en cours de traduction — il sera lisible une fois terminé.");
      return;
    }
    docActif = doc;
    chapActif = null;
    chapitresCoches = new Set();
    ficheParChapitre = {};
    arreterPollFiche();
    rendreDocs();

    $("lecture-bandeau").hidden = false;
    $("lecture-badge").textContent = estMarkdown(doc.chemin_source) ? "MD" : "PDF";
    $("lecture-doc-nom").textContent = doc.nom;
    $("lecture-vide").hidden = true;
    $("barre-audio").hidden = false;
    rendreBandeauEchec();
    majBoutonExportDocument();

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

  // ── Bandeau « traduction interrompue » (job erreur ou annulé) ───────────────

  function rendreBandeauEchec() {
    const bandeau = $("lecture-echec");
    const interrompu = docActif && STATUTS_REPRENABLES.has(docActif.statut);
    bandeau.hidden = !interrompu;
    if (!interrompu) return;
    if (docActif.statut === "annule") {
      const fait = docActif.sections_completees || 0;
      const total = docActif.total_sections || 0;
      $("lecture-echec-texte").textContent = total
        ? `Traduction annulée à ${fait}/${total} sections.`
        : "Traduction annulée avant la fin.";
    } else {
      const n = docActif.nb_sections_echouees || 0;
      $("lecture-echec-texte").textContent = n
        ? `${n} section${n > 1 ? "s" : ""} n'${n > 1 ? "ont" : "a"} pas pu être traduite${n > 1 ? "s" : ""}.`
        : "La traduction s'est interrompue avant la fin.";
    }
  }

  async function reprendreTraduction() {
    if (!docActif) return;
    if (!(await exigerSante())) return;
    const bouton = $("lecture-echec-reprendre");
    bouton.disabled = true;
    bouton.textContent = "Reprise en cours…";
    try {
      await apiPost("/translate", corpsSource(docActif.chemin_source, {
        langue_source: docActif.langue_source,
        langue_cible: docActif.langue_cible,
        modele_ollama: docActif.modele,
        resume: true,
      }));
      $("lecture-echec-texte").textContent =
        "Reprise lancée — seules les sections manquantes sont retraduites. Suis la progression dans « Nouveau document ».";
      bouton.hidden = true;
    } catch (e) {
      $("lecture-echec-texte").textContent = `❌ ${e.message}`;
      bouton.disabled = false;
      bouton.textContent = "⏯ Reprendre";
    }
  }

  $("lecture-echec-reprendre").addEventListener("click", reprendreTraduction);

  function rendreChapitres() {
    const zone = $("biblio-chapitres");
    zone.innerHTML = "";
    if (!docActif) return;
    if (chapitres.length === 0) {
      const vide = document.createElement("p");
      vide.className = "aide";
      vide.textContent = "Aucun titre détecté dans ce document.";
      zone.appendChild(vide);
      $("chap-selection-barre").hidden = true;
      return;
    }

    // Barre « tout cocher / décocher » — n'a de sens qu'en mode avancé (fiches).
    $("chap-selection-barre").hidden = false;

    for (const chap of chapitres) {
      const ligne = document.createElement("div");
      ligne.className = "chap-ligne";
      if (chapActif && chap.index === chapActif.index) ligne.classList.add("is-active");

      // Case à cocher : sélectionne le chapitre pour la génération de fiche.
      const check = document.createElement("input");
      check.type = "checkbox";
      check.className = "chap-check";
      check.checked = chapitresCoches.has(chap.index);
      check.title = "Inclure ce chapitre dans le résumé & quiz";
      check.addEventListener("change", () => {
        if (check.checked) chapitresCoches.add(chap.index);
        else chapitresCoches.delete(chap.index);
        majBoutonGenerer();
      });

      // Titre : cliquer lit le chapitre (comportement historique).
      const titre = document.createElement("button");
      titre.type = "button";
      titre.className = "sidebar-item chap-titre";
      titre.style.paddingLeft = `${8 + (chap.niveau - 1) * 12}px`;
      titre.textContent = chap.titre;
      titre.title = chap.titre;
      titre.addEventListener("click", () => {
        montrerLecture();
        selectionnerChapitre(chap);
      });

      ligne.append(check, titre);
      zone.appendChild(ligne);
    }
    majBoutonGenerer();
  }

  function coderTousLesChapitres(coche) {
    chapitresCoches = coche ? new Set(chapitres.map(c => c.index)) : new Set();
    rendreChapitres();
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

  // Résout un chemin relatif (extrait d'un tag ![]() du markdown, ex.
  // "MonDoc_images/xxx.png") en absolu — relatif au dossier du document
  // actif, où l'extraction dépose aussi les images — puis construit l'URL
  // de la route /image.
  function urlImage(cheminRelatif) {
    const dossier = docActif.chemin_sortie.slice(0, docActif.chemin_sortie.lastIndexOf("/"));
    const absolu = `${dossier}/${cheminRelatif}`;
    return `${API_BASE}/image?chemin=${encodeURIComponent(absolu)}`;
  }

  // Rendu Markdown minimal et sûr (DOM construit en textContent/createElement,
  // jamais innerHTML)
  function rendreContenu(markdown) {
    const zone = $("lecture-texte");
    zone.innerHTML = "";
    const lignes = markdown.split("\n");
    let paragraphe = [];
    let premierTitreSaute = false;
    const imagesActives = featureFlags.extraction_images_pdf === true;

    const viderParagraphe = () => {
      if (paragraphe.length === 0) return;
      const p = document.createElement("p");
      p.textContent = paragraphe.join(" ");
      zone.appendChild(p);
      paragraphe = [];
    };

    for (const ligne of lignes) {
      const image = imagesActives ? ligne.trim().match(/^!\[([^\]]*)\]\(([^)]+)\)$/) : null;
      const titre = ligne.match(/^(#{1,6})\s+(.*)/);
      if (image) {
        viderParagraphe();
        const img = document.createElement("img");
        img.src = urlImage(image[2]);
        img.alt = image[1];
        img.loading = "lazy";
        img.className = "lecture-image";
        zone.appendChild(img);
      } else if (titre) {
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

  // Reconstruit ficheParChapitre DEPUIS l'état backend (jamais d'accumulation) :
  // si le backend est reparti de zéro, l'UI ne doit pas garder des fiches mortes.
  function synchroniserFiches(etat) {
    ficheParChapitre = {};
    for (const chap of etat.chapitres) {
      if (chap.etape === "termine") ficheParChapitre[chap.index] = chap;
    }
  }

  async function chargerFicheExistante() {
    try {
      const etat = await apiGet(`/etude/statut?chemin_source=${encodeURIComponent(docActif.chemin_sortie)}`);
      if (etat) {
        synchroniserFiches(etat);
        if (etat.statut === "en_cours" || etat.statut === "en_attente") demarrerPollFiche();
      }
    } catch { /* pas de fiche */ }
    rendreFiche();
  }

  async function genererFiche() {
    if (!docActif) return;
    const selection = [...chapitresCoches];
    if (selection.length === 0) return;
    if (!(await exigerSante())) return;
    try {
      await apiPost("/etude", {
        chemin_md: docActif.chemin_sortie,
        chapitres_selectionnes: selection,
        // Options ANCRÉES sur le document, pas sur les menus d'un autre module :
        // sinon changer un menu de l'Import efface les fiches déjà générées
        // (le backend redémarre à zéro si les options diffèrent).
        modele_ollama: docActif.modele,
        nb_points: 5,
        nb_questions: 3,
        langue_fiche: docActif.langue_cible || "français",
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
      synchroniserFiches(etat);
      const enErreur = etat.chapitres.filter(c => c.etape === "erreur").length;
      if (["termine", "erreur", "annule"].includes(etat.statut)) {
        arreterPollFiche();
        if (etat.statut === "annule") $("ia-statut").textContent = "✕ Annulé";
        else if (enErreur) $("ia-statut").textContent = `⚠ ${enErreur} chapitre(s) en échec — les autres sont prêts.`;
        else $("ia-statut").textContent = "";
        rendreFiche();
      } else {
        // Progression globale plutôt que le premier chapitre en cours : lisible
        // quand plusieurs chapitres sont sélectionnés.
        $("ia-statut").textContent =
          `⏳ Génération — ${etat.etapes_completees}/${etat.total_etapes} étapes`;
        rendreFiche();
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

  function majBoutonGenerer() {
    const n = chapitresCoches.size;
    const btn = $("ia-generer");
    btn.disabled = n === 0 || !!pollFiche;
    btn.textContent = n === 0
      ? "Coche des chapitres à générer"
      : `Générer pour ${n} chapitre${n > 1 ? "s" : ""}`;
  }

  // Rend un bloc de fiche (points + quiz) pour un chapitre donné.
  function rendreBlocFiche(chap, fiche) {
    const bloc = document.createElement("div");
    bloc.className = "ia-bloc-chapitre";

    const titre = document.createElement("div");
    titre.className = "ia-bloc-titre";
    titre.textContent = chap.titre;
    bloc.appendChild(titre);

    const tPoints = document.createElement("div");
    tPoints.className = "ia-zone-titre";
    tPoints.textContent = "Points clés";
    bloc.appendChild(tPoints);
    for (const point of fiche.points) {
      const ligne = document.createElement("div");
      ligne.className = "ia-point";
      const puce = document.createElement("span");
      puce.className = "ia-puce";
      const texte = document.createElement("span");
      texte.textContent = point;
      ligne.append(puce, texte);
      bloc.appendChild(ligne);
    }

    const tQuiz = document.createElement("div");
    tQuiz.className = "ia-zone-titre";
    tQuiz.textContent = "Quiz";
    bloc.appendChild(tQuiz);
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
      bloc.appendChild(carte);
    });
    return bloc;
  }

  function rendreFiche() {
    majBoutonExport();
    majBoutonGenerer();

    // Affiche les fiches des chapitres générés, dans l'ordre du document.
    const zone = $("ia-resultats");
    zone.innerHTML = "";
    const aFiches = chapitres.filter(c => ficheParChapitre[c.index]);
    for (const chap of aFiches) {
      zone.appendChild(rendreBlocFiche(chap, ficheParChapitre[chap.index]));
    }
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

  // Chapitres à inclure dans l'export : ceux cochés qui ont une fiche ; si
  // aucune case n'est cochée, toutes les fiches générées (repli).
  function indicesPourExport() {
    const avecFiche = new Set(Object.keys(ficheParChapitre).map(Number));
    const coches = [...chapitresCoches].filter((i) => avecFiche.has(i));
    return new Set(coches.length ? coches : avecFiche);
  }

  function construireFicheHtml() {
    const titre = echapperHtml(docActif.nom || "Document");
    const meta = [
      docActif.langue_source && docActif.langue_cible
        ? `${echapperHtml(docActif.langue_source)} → ${echapperHtml(docActif.langue_cible)}` : "",
      docActif.modele ? `Modèle : ${echapperHtml(docActif.modele)}` : "",
      docActif.maj_a ? `Généré le ${echapperHtml(new Date(docActif.maj_a).toLocaleString("fr-CA"))}` : "",
    ].filter(Boolean).join(" · ");

    const inclus = indicesPourExport();

    // Table des matières : tous les chapitres, indentés par niveau.
    const toc = chapitres.map((c) => {
      const lien = inclus.has(c.index)
        ? `<a href="#chap-${c.index}">${echapperHtml(c.titre)}</a>`
        : `<span class="sans-fiche">${echapperHtml(c.titre)}</span>`;
      return `<li style="margin-left:${(Math.max(c.niveau, 1) - 1) * 1.2}rem">${lien}</li>`;
    }).join("\n");

    // Sections détaillées : les chapitres inclus dans l'export, dans l'ordre.
    const sections = chapitres
      .filter((c) => inclus.has(c.index))
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
  $("chap-tout-cocher").addEventListener("click", () => coderTousLesChapitres(true));
  $("chap-tout-decocher").addEventListener("click", () => coderTousLesChapitres(false));
  document.addEventListener("flags-charges", majBoutonExport);

  // ── Export HTML du document traduit complet (flag extraction_images_pdf) ───

  function majBoutonExportDocument() {
    $("doc-exporter").hidden = !(featureFlags.extraction_images_pdf === true && !!docActif);
  }

  // Convertit une image servie par /api/image en data-URI, pour un export
  // 100% autonome (portable une fois le fichier sorti du serveur local).
  async function imageEnDataUri(cheminRelatif) {
    const rep = await fetch(urlImage(cheminRelatif));
    const blob = await rep.blob();
    return new Promise((resolve) => {
      const lecteur = new FileReader();
      lecteur.onloadend = () => resolve(lecteur.result);
      lecteur.readAsDataURL(blob);
    });
  }

  // Fragment HTML sûr pour un chapitre : texte en <p> échappé, images en
  // <img> base64 — même politique « jamais d'innerHTML avec du contenu non
  // fiable » que rendreContenu()/echapperHtml().
  async function chapitreEnHtml(markdown) {
    const lignes = markdown.split("\n");
    let html = "";
    let paragraphe = [];
    const vider = () => {
      if (paragraphe.length) html += `<p>${echapperHtml(paragraphe.join(" "))}</p>\n`;
      paragraphe = [];
    };
    for (const ligne of lignes) {
      const image = ligne.trim().match(/^!\[([^\]]*)\]\(([^)]+)\)$/);
      const titre = ligne.match(/^(#{1,6})\s+(.*)/);
      if (image) {
        vider();
        const src = await imageEnDataUri(image[2]);
        html += `<img src="${src}" alt="${echapperHtml(image[1])}" class="doc-image">\n`;
      } else if (titre) {
        vider();
        html += `<h3>${echapperHtml(titre[2])}</h3>\n`;
      } else if (ligne.trim() === "") {
        vider();
      } else {
        paragraphe.push(ligne.trim());
      }
    }
    vider();
    return html;
  }

  async function construireDocumentHtml() {
    const titre = echapperHtml(docActif.nom || "Document");
    const meta = [
      docActif.langue_source && docActif.langue_cible
        ? `${echapperHtml(docActif.langue_source)} → ${echapperHtml(docActif.langue_cible)}` : "",
      docActif.modele ? `Modèle : ${echapperHtml(docActif.modele)}` : "",
      docActif.maj_a ? `Traduit le ${echapperHtml(new Date(docActif.maj_a).toLocaleString("fr-CA"))}` : "",
    ].filter(Boolean).join(" · ");

    const toc = chapitres.map((c) =>
      `<li style="margin-left:${(Math.max(c.niveau, 1) - 1) * 1.2}rem"><a href="#chap-${c.index}">${echapperHtml(c.titre)}</a></li>`
    ).join("\n");

    const sections = [];
    for (const c of chapitres) {
      const data = await apiPost("/chapitres/contenu", { chemin_md: docActif.chemin_sortie, index: c.index });
      const corps = await chapitreEnHtml(data.contenu);
      sections.push(`<section id="chap-${c.index}"><h2>${echapperHtml(c.titre)}</h2>${corps}</section>`);
    }

    return `<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${titre}</title>
<style>
  body { font-family: -apple-system, system-ui, sans-serif; line-height: 1.6; max-width: 820px; margin: 2rem auto; padding: 0 1.2rem; color: #1a1a1a; }
  h1 { margin-bottom: .2rem; } .meta { color: #666; font-size: .9rem; margin-bottom: 1.5rem; }
  nav { background: #f4f5f7; border-radius: 8px; padding: 1rem 1.2rem; margin-bottom: 2rem; }
  nav ul { list-style: none; padding: 0; margin: .4rem 0 0; } nav li { margin: .15rem 0; }
  nav a { color: #2563eb; text-decoration: none; } nav a:hover { text-decoration: underline; }
  section { border-top: 1px solid #e5e7eb; padding-top: 1rem; margin-top: 2rem; }
  h2 { margin-bottom: .3rem; } h3 { margin: 1.1rem 0 .3rem; font-size: 1rem; color: #444; }
  .doc-image { display: block; max-width: 100%; height: auto; border-radius: 8px; margin: 1rem 0; }
  @media (prefers-color-scheme: dark) {
    body { background: #16181c; color: #e5e7eb; } .meta { color: #9aa0a6; }
    nav { background: #22252b; } section { border-color: #33373e; } h3 { color: #b6bcc4; }
    nav a { color: #6ea8fe; }
  }
</style>
</head>
<body>
  <h1>${titre}</h1>
  <p class="meta">${meta}</p>
  <nav>
    <strong>Structure des chapitres</strong>
    <ul>${toc}</ul>
  </nav>
  ${sections.join("\n")}
</body>
</html>`;
  }

  async function exporterDocumentHtml() {
    if (!docActif) return;
    const bouton = $("doc-exporter");
    bouton.disabled = true;
    bouton.textContent = "Préparation…";
    try {
      const html = await construireDocumentHtml();
      const base = (docActif.nom || "document").replace(/\.[^.]+$/, "");
      const nomFichier = `${base}_traduit.html`;
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
          if (e.name !== "AbortError") telechargerHtml(html, nomFichier);
        }
      } else {
        telechargerHtml(html, nomFichier);
      }
    } finally {
      bouton.disabled = false;
      bouton.textContent = "⬇ Exporter (HTML)…";
    }
  }

  $("doc-exporter").addEventListener("click", exporterDocumentHtml);
  document.addEventListener("flags-charges", majBoutonExportDocument);

  // ── Rafraîchissements ───────────────────────────────────────────────────────

  document.addEventListener("module-affiche", (e) => {
    if (e.detail === "bibliotheque") chargerDocs();
  });
  document.addEventListener("traduction-terminee", chargerDocs);
  document.addEventListener("backend-connecte", chargerDocs);
})();
