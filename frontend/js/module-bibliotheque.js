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

  // Le corps traduit d'un chapitre commence par son titre déjà traduit — le
  // backend l'expose dans titre_traduit (feature 348), sans appel LLM
  // supplémentaire. Le marqueur garde le titre SOURCE (chap.titre) pour
  // l'alignement de la relecture comparative (feature 297, voir plus bas) :
  // ne jamais lui substituer titre_traduit pour la logique, seulement pour
  // ce qui est montré au lecteur. Repli sur le titre source si l'extraction
  // échoue (chapitre implicite, corps tronqué par un arrêt impromptu…).
  const titreAffiche = (chap) => titreChapitreListe(chap.titre_traduit || chap.titre);

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
  document.addEventListener("montrer-lecture", montrerLecture);   // vue « Mise en page » (bibliotheque-exports.js)
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
      badge.className = "ds-badge ds-badge--accent badge-type badge-mini";
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
    rendreMeta(doc);
    rendreBandeauEchec();
    majBoutonExportDocument();
    // Exports « mise en page d'origine » et signature (js/bibliotheque-exports.js).
    document.dispatchEvent(new CustomEvent("doc-actif", { detail: doc }));

    try {
      const data = await apiPost("/chapitres", { chemin_md: doc.chemin_sortie }, API_TIMEOUT_LONG_MS);
      // Un autre document a été choisi pendant l'attente : cette réponse n'est plus la bonne
      // (sinon les chapitres de A s'affichaient sous le document B).
      if (docActif !== doc) return;
      chapitres = data.chapitres;
    } catch {
      if (docActif !== doc) return;
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

  // ── Infos du document (registre seulement, aucun appel de plus) ─────────────
  // Une valeur absente du registre s'affiche « — », jamais 0 ni une valeur inventée. Les entrées
  // reconstruites depuis un .state.json n'ont ni `cree_a` ni `qualite` (services/bibliotheque.py).

  const LIBELLES_STATUT = { termine: "Terminé", erreur: "Interrompu (erreur)", annule: "Annulé", en_cours: "En cours", en_pause: "En pause" };

  function dateLisible(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "—";
    return d.toLocaleString("fr-CA", { dateStyle: "medium", timeStyle: "short" });
  }

  function dureeLisible(secondes) {
    if (!secondes || secondes < 0) return "—";
    const s = Math.round(secondes);
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
    if (h) return `${h} h ${String(m).padStart(2, "0")} min`;
    if (m) return `${m} min ${String(r).padStart(2, "0")} s`;
    return `${r} s`;
  }

  function rendreMeta(doc) {
    const bloc = $("lecture-meta");
    const langues = doc.langue_source && doc.langue_cible ? `${doc.langue_source} → ${doc.langue_cible}` : "—";
    const nbChap = Array.isArray(doc.chapitres_traduits) ? doc.chapitres_traduits.length : null;
    // Pas de « N sur M sélectionnés » : un titre sans texte est sélectionné mais jamais marqué traduit, et un
    // document terminé affichait « 15 sur 79 » comme s'il avait des trous (les trous, c'est nb_sections_echouees).
    const chapitresTxt = nbChap == null ? "—" : String(nbChap);
    const morceaux = doc.total_sections ? `${doc.sections_completees ?? 0} / ${doc.total_sections}` : "—";
    const echecs = doc.nb_sections_echouees ? String(doc.nb_sections_echouees) : null;
    const dossier = (doc.chemin_sortie || "").split("/").slice(0, -1).join("/");
    const lignes = [
      ["Statut", LIBELLES_STATUT[doc.statut] || doc.statut || "—"],
      ["Langues", langues],
      ["Modèle", doc.modele || "—"],
      ["Type de source", estMarkdown(doc.chemin_source) ? "Markdown (.md)" : "PDF"],
      // Source .md : aucun PDF lu, le registre y garde l'extracteur par défaut — ces deux lignes mentiraient.
      ...(estMarkdown(doc.chemin_source) ? [] : [
        ["Extraction", doc.qualite ? libelleExtraction(doc.qualite) : "—"],
        ["Extracteur", doc.extracteur_pdf || "—"],
      ]),
      ["Chapitres traduits", chapitresTxt],
      ["Morceaux traduits", morceaux],
      ...(echecs ? [["Morceaux en échec", echecs]] : []),
      ["Temps de traduction", dureeLisible(doc.temps_ecoule_secondes)],
      ["Ajouté le", dateLisible(doc.cree_a)],
      ["Mis à jour le", dateLisible(doc.maj_a)],
      ["Source", doc.chemin_source || "—", true],
      ["Traduction", doc.chemin_sortie || "—", true],
      ["Dossier", dossier || "—", true],
    ];
    const grille = $("lecture-meta-grille");
    grille.innerHTML = "";
    for (const [cle, valeur, chemin] of lignes) {
      const dt = document.createElement("dt");
      dt.textContent = cle;
      const dd = document.createElement("dd");
      dd.textContent = valeur;
      if (chemin) dd.classList.add("lecture-meta-chemin");
      grille.append(dt, dd);
    }
    const resume = [langues, doc.modele, nbChap != null ? `${nbChap} chapitre${nbChap > 1 ? "s" : ""}` : null,
                    doc.maj_a ? `mis à jour le ${dateLisible(doc.maj_a)}` : null].filter((x) => x && x !== "—");
    $("lecture-meta-resume").textContent = resume.join(" · ");
    bloc.hidden = false;
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
      }), API_TIMEOUT_LONG_MS);  // preflight Ollama + extraction des chapitres (piège 13)
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
      preparerLigneChapitre(ligne, chapitres, chap);
      if (chapActif && chap.index === chapActif.index) ligne.classList.add("is-active");

      // Case à cocher : sélectionne le chapitre pour la génération de fiche. Cocher une partie coche ses
      // sous-chapitres, comme dans « Nouveau document » ; les cases sont mises à jour sur place (focus gardé).
      const check = document.createElement("input");
      check.type = "checkbox";
      check.className = "chap-check";
      check.dataset.chap = chap.index;
      check.checked = chapitresCoches.has(chap.index);
      check.indeterminate = chapitrePartiel(chapitres, chap.index, chapitresCoches, new Set());
      check.title = "Inclure ce chapitre dans le résumé & quiz";
      check.addEventListener("change", () => {
        cocherChapitreEtSousChapitres(chapitres, chap.index, check.checked, chapitresCoches, new Set());
        zone.querySelectorAll(".chap-check").forEach((c) => {
          const i = Number(c.dataset.chap);
          c.checked = chapitresCoches.has(i);
          c.indeterminate = chapitrePartiel(chapitres, i, chapitresCoches, new Set());
        });
        majBoutonGenerer();
      });

      // Titre : cliquer lit le chapitre (comportement historique). Titre source en infobulle.
      const titre = document.createElement("button");
      titre.type = "button";
      titre.className = "sidebar-item chap-titre";
      titre.textContent = titreAffiche(chap);
      titre.title = chap.titre_traduit ? `${titre.textContent}\n(source : ${chap.titre})` : titre.textContent;
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
    $("lecture-titre").textContent = titreAffiche(chap);
    $("lecture-texte").textContent = "Chargement…";
    const doc = docActif;
    // Réponse périmée (autre chapitre ou autre document choisi pendant l'attente) : ignorée,
    // sinon le contenu du chapitre 1 s'affichait sous le titre du chapitre 2.
    const perime = () => chapActif !== chap || docActif !== doc;
    try {
      const data = await apiPost("/chapitres/contenu", {
        chemin_md: doc.chemin_sortie,
        index: chap.index,
      }, API_TIMEOUT_LONG_MS);
      if (perime()) return;
      rendreContenu(data.contenu);
    } catch (e) {
      if (perime()) return;
      $("lecture-texte").textContent = `Impossible de charger le chapitre : ${e.message}`;
    }
    chargerSourceComparee();
    rendreFiche();
  }

  // ── Relecture comparative (feature 297) ─────────────────────────────────────
  // L'origine à gauche, la traduction à droite, sur le MÊME chapitre.
  //
  // Les index concordent parce que la Bibliothèque tire ses chapitres des
  // marqueurs écrits par le moteur, qui portent l'index ET le titre de la
  // SOURCE (feature 327) : le chapitre n de la traduction est bien le chapitre n
  // de l'original. Sans cet alignement, comparer côte à côte afficherait deux
  // passages sans rapport — vérifié sur Chapter 9 avant d'écrire ce code.

  let comparaisonActive = false;

  function appliquerComparaison() {
    $("lecture-colonnes").classList.toggle("comparaison", comparaisonActive);
    $("lecture-source-panneau").hidden = !comparaisonActive;
    $("lecture-trad-langue").hidden = !comparaisonActive;
    const bouton = $("doc-comparer");
    bouton.setAttribute("aria-pressed", String(comparaisonActive));
    bouton.classList.toggle("is-active", comparaisonActive);
    if (docActif) {
      $("lecture-source-langue").textContent = `Version d'origine (${docActif.langue_source || "source"})`;
      $("lecture-trad-langue").textContent = `Traduction (${docActif.langue_cible || "cible"})`;
    }
  }

  async function chargerSourceComparee() {
    if (!comparaisonActive || !docActif || !chapActif) return;
    const zone = $("lecture-source");
    zone.textContent = "Chargement…";
    const doc = docActif, chap = chapActif;
    const perime = () => chapActif !== chap || docActif !== doc;
    try {
      // La source est le document D'ORIGINE (PDF ou Markdown), pas la sortie, lue avec
      // l'extracteur du travail : les index de chapitres n'ont de sens qu'avec lui.
      // Délai long : sans `_converti`, c'est une extraction complète du PDF (piège 13).
      const data = await apiPost("/chapitres/contenu", corpsSource(doc.chemin_source, {
        index: chap.index,
        ...(doc.extracteur_pdf ? { extracteur_pdf: doc.extracteur_pdf } : {}),
      }), API_TIMEOUT_LONG_MS);
      if (perime()) return;
      rendreContenu(data.contenu, zone);
    } catch (e) {
      if (perime()) return;
      zone.textContent = `Impossible de charger la version d'origine : ${e.message}`;
    }
  }

  // Bénéfice de chaque stratégie, en une phrase, au moment où l'utilisateur choisit (la logique est dans
  // services/study_runner.py : « sections » lit le texte réel section par section, « condensation » résume d'abord
  // un chapitre trop long puis travaille sur ce résumé). Le détail est dans « Comparer les deux stratégies ».
  const AIDE_STRATEGIE = {
    sections:
      "<strong>La plus fidèle :</strong> l'IA relève les points de chaque section dans le texte réel — " +
      "tout le chapitre est couvert, noms, dates et chiffres compris.",
    condensation:
      "<strong>La plus directe :</strong> l'IA résume d'abord le chapitre, puis en tire la fiche — " +
      "plus synthétique, mais peut perdre des détails de la fin.",
  };
  function majAideStrategie() {
    const aide = $("ia-strategie-aide");
    if (aide) aide.innerHTML = AIDE_STRATEGIE[strategieChoisie()] || "";   // texte statique, jamais de contenu utilisateur
  }

  // Choix de stratégie en deux boutons (radiogroup) : flèches gauche/droite comme un vrai groupe radio.
  function choisirStrategie(valeur) {
    $("ia-strategie").querySelectorAll("[data-strategie]").forEach((b) => {
      const actif = b.dataset.strategie === valeur;
      b.setAttribute("aria-checked", String(actif));
      b.tabIndex = actif ? 0 : -1;
    });
    enregistrerOptionsFiche();
    majAideStrategie();
    // Chaque stratégie a SA fiche : on recharge celle qui correspond.
    ficheParChapitre = {};
    arreterPollFiche();
    $("ia-statut").textContent = "";
    rendreFiche();
    if (docActif) chargerFicheExistante();
  }
  $("ia-strategie").addEventListener("click", (e) => {
    const b = e.target.closest("[data-strategie]");
    if (b && b.getAttribute("aria-checked") !== "true") choisirStrategie(b.dataset.strategie);
  });
  $("ia-strategie").addEventListener("keydown", (e) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    const autre = strategieChoisie() === "sections" ? "condensation" : "sections";
    choisirStrategie(autre);
    $("ia-strategie").querySelector(`[data-strategie="${autre}"]`).focus();
    e.preventDefault();
  });

  $("doc-comparer").addEventListener("click", () => {
    comparaisonActive = !comparaisonActive;
    appliquerComparaison();
    if (comparaisonActive) {
      // La colonne de lecture doit être visible pour qu'une comparaison ait un
      // sens — en mode avancé elle est masquée par défaut.
      montrerLecture();
      chargerSourceComparee();
    }
  });

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
  function rendreContenu(markdown, cible) {
    // `cible` permet de rendre dans la colonne d'origine sans dupliquer tout ce
    // moteur de rendu (feature 297). Par défaut : la colonne de traduction,
    // comme avant.
    const zone = cible || $("lecture-texte");
    zone.innerHTML = "";
    const lignes = markdown.split("\n");
    let paragraphe = [];
    let premierTitreSaute = false;
    // « <!-- non-traduit --> » : le paragraphe suivant est resté dans sa langue d'origine (la traduction dérivait vers
    // une autre langue, voir controle_langue.MARQUE_NON_TRADUIT) ; il est affiché sur fond ambré.
    let nonTraduit = false;
    // Extraction d'images = fonctionnalité de mode avancé — flag ET mode avancé requis.
    const imagesActives = featureFlags.extraction_images_pdf === true
      && document.documentElement.classList.contains("avance");

    const viderParagraphe = () => {
      if (paragraphe.length === 0) return;
      const p = document.createElement("p");
      // Les marques du Markdown sont RENDUES, plus affichées littéralement
      // (feature 315). Avant, `textContent` faisait lire « _Cyclosa
      // octotuberculata_ » avec ses tirets bas et « <sup>2</sup> » en toutes
      // lettres — l'export HTML avait été corrigé, le lecteur non, alors que
      // c'est ici qu'on passe le plus de temps.
      //
      // `innerHTML` est sûr ICI et seulement ici parce que `markdownEnLigne`
      // ÉCHAPPE d'abord la totalité du texte, puis ne réintroduit qu'une liste
      // blanche fermée de balises sans attribut (plus `href` restreint à
      // http(s)/#/mailto). Ne jamais y passer une chaîne qui n'est pas sortie
      // de cette fonction.
      p.innerHTML = markdownEnLigne(paragraphe.join(" "));
      if (nonTraduit) {
        p.classList.add("non-traduit");
        p.title = "Laissé en langue d'origine : la traduction dérivait vers une autre langue";
        nonTraduit = false;
      }
      zone.appendChild(p);
      paragraphe = [];
    };

    for (const ligne of lignes) {
      // Ligne de commentaire HTML (marques « <!-- conserver --> » d'un texte non traduit, marqueurs de
      // chapitre) : invisible, comme dans un vrai rendu Markdown. Elle ne coupe pas le paragraphe.
      if (/^\s*<!-- non-traduit -->\s*$/.test(ligne)) { viderParagraphe(); nonTraduit = true; continue; }
      if (/^\s*<!--.*-->\s*$/.test(ligne)) continue;
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
        // Même traitement pour les titres : un titre traduit porte souvent du
        // gras (`**Titre**`), qui s'affichait avec ses astérisques.
        h.innerHTML = markdownEnLigne(titre[2]);
        zone.appendChild(h);
      } else if (/^\|\s.*\|$/.test(ligne.trim())) {
        // Rangée d'un tableau de tirage du scan (« | 1–2 | Forêt | ☐ | ») : une ligne de tableau, pas du texte.
        viderParagraphe();
        let table = zone.lastElementChild;
        if (!table || !table.classList.contains("lecture-tirage")) {
          table = document.createElement("table");
          table.className = "lecture-tirage";
          zone.appendChild(table);
        }
        const tr = document.createElement("tr");
        const cellules = ligne.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
        for (const c of cellules) {
          const td = document.createElement("td");
          td.innerHTML = markdownEnLigne(c);   // échappe d'abord (voir viderParagraphe)
          tr.appendChild(td);
        }
        table.appendChild(tr);
      } else if (/^\s*[-*+]\s+\S/.test(ligne)) {
        // Puce (ex. table des matières imprimée, une entrée par ligne, deux espaces par niveau) :
        // avant, la liste entière se collait en un seul paragraphe avec ses tirets.
        viderParagraphe();
        const m = ligne.match(/^(\s*)[-*+]\s+(.*)/);
        let ul = zone.lastElementChild;
        if (!ul || ul.tagName !== "UL" || !ul.classList.contains("lecture-liste")) {
          ul = document.createElement("ul");
          ul.className = "lecture-liste";
          zone.appendChild(ul);
        }
        const li = document.createElement("li");
        li.style.marginLeft = `${Math.floor(m[1].length / 2) * 1.2}rem`;
        li.innerHTML = markdownEnLigne(m[2]);   // échappe d'abord (voir viderParagraphe)
        ul.appendChild(li);
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
  // Un chapitre s'affiche dès qu'il a des points OU un quiz : on peut ne générer que l'un des deux.
  function synchroniserFiches(etat) {
    ficheParChapitre = {};
    for (const chap of etat.chapitres) {
      if ((chap.points && chap.points.length) || (chap.questions && chap.questions.length)) {
        ficheParChapitre[chap.index] = chap;
      }
    }
  }

  // ── Options de génération (contenu, stratégie, quantités) ───────────────────
  // Mémorisées pour ce navigateur seulement (confort) : sans stockage, les défauts s'appliquent.
  const CLE_OPTIONS_FICHE = "toledo.options-fiche";

  function strategieChoisie() {
    const actif = $("ia-strategie")?.querySelector('[aria-checked="true"]');
    // Repli aligné sur le défaut du backend (« sections » depuis le 19/8) : un repli qui contredit
    // le backend produirait une fiche différente de celle qu'on affiche.
    return actif ? actif.dataset.strategie : "sections";
  }

  function nombreOuAuto(id) {
    const n = parseInt($(id).value, 10);
    return Number.isFinite(n) && n > 0 ? Math.min(n, 20) : 0;   // 0 = automatique (selon la longueur du chapitre)
  }

  function optionsFiche() {
    return {
      avec_points: $("ia-avec-points").checked,
      avec_quiz: $("ia-avec-quiz").checked,
      nb_points: nombreOuAuto("ia-nb-points"),
      nb_questions: nombreOuAuto("ia-nb-questions"),
      strategie: strategieChoisie(),
    };
  }

  function enregistrerOptionsFiche() {
    try { localStorage.setItem(CLE_OPTIONS_FICHE, JSON.stringify(optionsFiche())); } catch { /* stockage indisponible */ }
  }

  function restaurerOptionsFiche() {
    let o = null;
    try { o = JSON.parse(localStorage.getItem(CLE_OPTIONS_FICHE) || "null"); } catch { o = null; }
    if (!o) return;
    $("ia-avec-points").checked = o.avec_points !== false;
    $("ia-avec-quiz").checked = o.avec_quiz !== false;
    $("ia-nb-points").value = o.nb_points > 0 ? o.nb_points : "";
    $("ia-nb-questions").value = o.nb_questions > 0 ? o.nb_questions : "";
    if (o.strategie === "condensation" || o.strategie === "sections") {
      $("ia-strategie").querySelectorAll("[data-strategie]").forEach((b) => {
        const actif = b.dataset.strategie === o.strategie;
        b.setAttribute("aria-checked", String(actif));
        b.tabIndex = actif ? 0 : -1;
      });
    }
  }

  function majChampsQuantite() {
    // Une quantité sans objet (quiz décoché) est grisée, pas effacée : elle revient si on recoche.
    $("ia-nb-points").disabled = !$("ia-avec-points").checked;
    $("ia-nb-questions").disabled = !$("ia-avec-quiz").checked;
  }

  for (const id of ["ia-avec-points", "ia-avec-quiz"]) {
    $(id).addEventListener("change", () => {
      // Au moins un des deux : décocher le dernier recoche l'autre (une génération vide n'a pas de sens).
      if (!$("ia-avec-points").checked && !$("ia-avec-quiz").checked) {
        $(id === "ia-avec-points" ? "ia-avec-quiz" : "ia-avec-points").checked = true;
      }
      majChampsQuantite();
      enregistrerOptionsFiche();
      majBoutonGenerer();
    });
  }
  for (const id of ["ia-nb-points", "ia-nb-questions"]) {
    $(id).addEventListener("change", enregistrerOptionsFiche);
  }
  restaurerOptionsFiche();
  majChampsQuantite();
  majAideStrategie();

  function urlStatutFiche() {
    const params = new URLSearchParams({
      chemin_source: docActif.chemin_sortie,
      modele: docActif.modele || "",
      strategie: strategieChoisie(),
    });
    return `/etude/statut?${params}`;
  }

  async function chargerFicheExistante() {
    try {
      const etat = await apiGet(urlStatutFiche());
      if (etat) {
        synchroniserFiches(etat);
        if (etat.statut === "en_cours" || etat.statut === "en_attente") demarrerPollFiche();
      }
    } catch { /* pas de fiche */ }
    rendreFiche();
  }

  // Corps commun de /etude et /etude/estimation. Options ANCRÉES sur le document (modèle, langue), pas sur
  // les menus d'un autre module : la fiche suit le document.
  function corpsFiche(selection, surcharge = {}) {
    const o = optionsFiche();
    return {
      chemin_md: docActif.chemin_sortie,
      chapitres_selectionnes: selection,
      modele_ollama: docActif.modele,
      langue_fiche: docActif.langue_cible || "français",
      strategie: o.strategie,
      avec_points: o.avec_points,
      avec_quiz: o.avec_quiz,
      nb_points: o.nb_points,
      nb_questions: o.nb_questions,
      regenerer: false,
      ...surcharge,
    };
  }

  // Lance un passage. `surcharge` sert aux actions d'une fiche de chapitre (régénérer, ajouter le quiz…).
  async function genererFiche(selection = [...chapitresCoches], surcharge = {}) {
    if (!docActif || selection.length === 0) return;
    if (!(await exigerSante())) return;
    try {
      const rep = await apiPost("/etude", corpsFiche(selection, surcharge), API_TIMEOUT_LONG_MS);
      if (!rep.taches) {
        $("ia-statut").textContent = "Rien à générer : ces chapitres ont déjà ce contenu. « ↻ Régénérer » sur une fiche la refait.";
        return;
      }
      $("ia-statut").textContent = "⏳ Génération en file d'attente…";
      $("ia-generer").disabled = true;
      demarrerPollFiche();
    } catch (e) {
      $("ia-statut").textContent = `❌ ${e.message}`;
    }
  }

  // ── Durée : estimation avant de lancer, minutage pendant ────────────────────

  let dernierEtatFiche = null;
  let minuteurFiche = null;
  let estimationEnAttente = null;
  let numeroEstimation = 0;

  function dureeLisible(secondes) {
    if (secondes == null || !Number.isFinite(secondes)) return null;
    const s = Math.max(0, Math.round(secondes));
    if (s < 60) return `${s} s`;
    if (s < 3600) return `${Math.round(s / 60)} min`;
    return `${Math.floor(s / 3600)} h ${String(Math.round((s % 3600) / 60)).padStart(2, "0")}`;
  }

  function planifierEstimation() {
    clearTimeout(estimationEnAttente);
    estimationEnAttente = setTimeout(estimerFiche, 350);   // une seule requête après une rafale de clics
  }

  async function estimerFiche() {
    const zone = $("ia-estimation");
    if (!docActif || pollFiche || chapitresCoches.size === 0) {
      if (!pollFiche) zone.textContent = "";
      return;
    }
    const numero = ++numeroEstimation;
    try {
      const e = await apiPost("/etude/estimation", corpsFiche([...chapitresCoches]), API_TIMEOUT_LONG_MS);
      if (numero !== numeroEstimation || pollFiche) return;   // réponse périmée (autre sélection entre-temps)
      majBoutonGenerer(e);
      if (e.statut === "rien") {
        zone.textContent = "Déjà généré pour ces chapitres. « ↻ Régénérer » sur une fiche pour la refaire.";
        return;
      }
      const volume = `${e.chapitres} chapitre${e.chapitres > 1 ? "s" : ""} · ${e.taches} étape${e.taches > 1 ? "s" : ""}`;
      if (e.statut === "ok") {
        const fourchette = e.min != null && e.max != null && dureeLisible(e.min) !== dureeLisible(e.max)
          ? ` (${dureeLisible(e.min)} à ${dureeLisible(e.max)})` : "";
        zone.innerHTML = "<strong></strong><span></span>";
        zone.children[0].textContent = `≈ ${dureeLisible(e.secondes)}${fourchette}`;
        zone.children[1].textContent = ` · ${volume} · ${e.description_base}`;
      } else {
        zone.textContent = `Durée : pas encore estimable (${e.echantillons ?? 0} fiche${(e.echantillons ?? 0) > 1 ? "s" : ""} `
          + `mesurée${(e.echantillons ?? 0) > 1 ? "s" : ""} sur ${e.minimum ?? 3}) · ${volume}. La Chronique apprend de chaque fiche.`;
      }
    } catch {
      if (numero === numeroEstimation) zone.textContent = "";
    }
  }

  // Minutage en direct : le backend donne la durée restante et l'instant où il l'a calculée ; on décompte d'ici.
  function texteMinutageFiche(etat) {
    const morceaux = [`⏳ ${etat.etapes_completees}/${etat.total_etapes} étape${etat.total_etapes > 1 ? "s" : ""}`];
    if (etat.statut === "en_attente") return `${morceaux[0]} · en file d'attente`;
    const maintenant = Date.now() / 1000;
    if (etat.debut_session) morceaux.push(`⏱ ${formaterDuree(maintenant - etat.debut_session)}`);
    if (etat.estimation_restante_secondes != null && etat.estimation_maj_a) {
      const reste = etat.estimation_restante_secondes - (maintenant - etat.estimation_maj_a);
      const origine = etat.etapes_completees > 0 ? "rythme observé" : (etat.estimation_base ? "Chronique" : "");
      morceaux.push(reste > 0
        ? `reste ≈ ${dureeLisible(reste)}${origine ? ` (${origine})` : ""}`
        : "dépasse l'estimation");
    } else {
      morceaux.push("reste : estimé après la 1re étape");
    }
    return morceaux.join(" · ");
  }

  function armerMinuteurFiche() {
    if (minuteurFiche) return;
    minuteurFiche = setInterval(() => {
      if (dernierEtatFiche && pollFiche) $("ia-statut").textContent = texteMinutageFiche(dernierEtatFiche);
    }, 1000);
  }

  async function pollStatutFiche() {
    if (!docActif) { arreterPollFiche(); return; }
    try {
      const etat = await apiGet(urlStatutFiche());
      if (!etat) return;
      dernierEtatFiche = etat;
      synchroniserFiches(etat);
      // Échecs de CE passage seulement (un chapitre raté hier garde son ⚠ sur sa fiche, pas ici).
      const enErreur = (etat.echecs_passage || []).length;
      if (["termine", "erreur", "annule", "en_pause"].includes(etat.statut)) {
        arreterPollFiche();
        const duree = etat.temps_ecoule_secondes ? ` en ${dureeLisible(etat.temps_ecoule_secondes)}` : "";
        if (etat.statut === "annule") $("ia-statut").textContent = "✕ Annulé";
        else if (etat.statut === "en_pause") $("ia-statut").textContent = "⏸ En pause — « Générer » reprend là où elle s'est arrêtée.";
        else if (enErreur) $("ia-statut").textContent = etat.statut === "erreur"
          ? `❌ Échec de la génération (${enErreur} chapitre${enErreur > 1 ? "s" : ""}) — détail sur la fiche du chapitre.`
          : `⚠ ${enErreur} chapitre(s) en échec — les autres sont prêts.`;
        else $("ia-statut").textContent = `✓ Fiche prête${duree}.`;
        rendreFiche();
      } else {
        $("ia-statut").textContent = texteMinutageFiche(etat);
        $("ia-estimation").textContent = "";
        rendreFiche();
      }
    } catch { /* prochain tick */ }
  }

  function demarrerPollFiche() {
    if (pollFiche) return;
    pollFiche = setInterval(pollStatutFiche, 2000);
    pollStatutFiche();
    armerMinuteurFiche();
  }

  function arreterPollFiche() {
    clearInterval(pollFiche);
    pollFiche = null;
    clearInterval(minuteurFiche);
    minuteurFiche = null;
  }

  // `estimation` (facultative) : réponse de /etude/estimation — le bouton dit alors combien de chapitres
  // ont VRAIMENT quelque chose à générer, pas seulement combien sont cochés.
  function majBoutonGenerer(estimation) {
    const n = chapitresCoches.size;
    const btn = $("ia-generer");
    const o = optionsFiche();
    const quoi = o.avec_points && o.avec_quiz ? "points clés + quiz" : (o.avec_points ? "points clés" : "quiz");
    if (n === 0) {
      btn.disabled = true;
      btn.textContent = "Coche des chapitres à générer";
    } else if (estimation && estimation.statut === "rien") {
      btn.disabled = true;
      btn.textContent = "Déjà généré pour ces chapitres";
    } else {
      const k = estimation ? estimation.chapitres : n;
      btn.disabled = !!pollFiche;
      btn.textContent = `Générer ${quoi} · ${k} chapitre${k > 1 ? "s" : ""}`;
    }
    if (!estimation) planifierEstimation();
  }

  // ── Rendu des fiches, avec les actions de chaque chapitre ───────────────────

  function boutonAction(libelle, titre, action) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "bouton-lien ia-action";
    b.textContent = libelle;
    b.title = titre;
    b.disabled = !!pollFiche;
    b.addEventListener("click", action);
    return b;
  }

  function rendreBlocFiche(chap, fiche) {
    const bloc = document.createElement("div");
    bloc.className = "ia-bloc-chapitre";

    const tete = document.createElement("div");
    tete.className = "ia-bloc-tete";
    const titre = document.createElement("div");
    titre.className = "ia-bloc-titre";
    titre.textContent = titreAffiche(chap);
    const actions = document.createElement("div");
    actions.className = "ia-bloc-actions";
    const aPoints = fiche.points && fiche.points.length > 0;
    const aQuiz = fiche.questions && fiche.questions.length > 0;
    if (!aPoints) {
      actions.appendChild(boutonAction("＋ Points", "Générer les points clés de ce chapitre",
        () => genererFiche([chap.index], { avec_points: true, avec_quiz: false })));
    }
    if (!aQuiz) {
      actions.appendChild(boutonAction("＋ Quiz", "Générer le quiz de ce chapitre",
        () => genererFiche([chap.index], { avec_points: false, avec_quiz: true })));
    }
    actions.appendChild(boutonAction("↻ Régénérer",
      "Refaire ce chapitre avec les options choisies ci-dessus (contenu, quantités)",
      () => genererFiche([chap.index], { regenerer: true })));
    tete.append(titre, actions);
    bloc.appendChild(tete);

    if (fiche.etape === "erreur") {
      const alerte = document.createElement("div");
      alerte.className = "ia-bloc-alerte";
      alerte.textContent = "⚠ La dernière génération de ce chapitre a échoué ; ce qui est affiché vient d'avant.";
      bloc.appendChild(alerte);
    }

    if (aPoints) {
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
    }

    if (aQuiz) {
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
    }
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

  $("ia-generer").addEventListener("click", () => genererFiche());

  // ── Export HTML de la fiche d'étude (flag export_fiche_html) ────────────────

  function majBoutonExport() {
    const nbFiches = Object.keys(ficheParChapitre).length;
    const dispo = featureFlags.export_fiche_html !== false && nbFiches > 0;
    $("ia-export").hidden = !dispo;
    if (!dispo) return;
    const nbCoches = [...chapitresCoches].filter((i) => ficheParChapitre[i]).length;
    $("ia-export-coches").textContent = `Chapitres cochés (${nbCoches})`;
    $("ia-export-toutes").textContent = `Toutes les fiches (${nbFiches})`;
    const radioCoches = document.querySelector('input[name="ia-export-portee"][value="coches"]');
    radioCoches.disabled = nbCoches === 0;
    if (nbCoches === 0) document.querySelector('input[name="ia-export-portee"][value="toutes"]').checked = true;
  }

  function optionsExport() {
    return {
      points: $("ia-export-points").checked,
      quiz: $("ia-export-quiz").checked,
      reponsesDepliees: $("ia-export-reponses").checked,
      portee: document.querySelector('input[name="ia-export-portee"]:checked')?.value || "toutes",
    };
  }

  // `echapperHtml` vient de commun.js (échappe aussi les guillemets : les URL des
  // liens et les `alt` des images sont insérés dans des attributs).

  function indicesPourExport(portee) {
    const avecFiche = new Set(Object.keys(ficheParChapitre).map(Number));
    const coches = [...chapitresCoches].filter((i) => avecFiche.has(i));
    return new Set(portee === "coches" && coches.length ? coches : avecFiche);
  }

  function construireFicheHtml(opts) {
    const titre = echapperHtml(docActif.nom || "Document");
    const meta = [
      docActif.langue_source && docActif.langue_cible
        ? `${echapperHtml(docActif.langue_source)} → ${echapperHtml(docActif.langue_cible)}` : "",
      docActif.modele ? `Modèle : ${echapperHtml(docActif.modele)}` : "",
      docActif.maj_a ? `Généré le ${echapperHtml(new Date(docActif.maj_a).toLocaleString("fr-CA"))}` : "",
    ].filter(Boolean).join(" · ");

    const inclus = indicesPourExport(opts.portee);

    // Table des matières : tous les chapitres, indentés par niveau.
    const toc = chapitres.map((c) => {
      const lien = inclus.has(c.index)
        ? `<a href="#chap-${c.index}">${echapperHtml(titreAffiche(c))}</a>`
        : `<span class="sans-fiche">${echapperHtml(titreAffiche(c))}</span>`;
      return `<li style="margin-left:${(Math.max(c.niveau, 1) - 1) * 1.2}rem">${lien}</li>`;
    }).join("\n");

    // Sections détaillées : les chapitres inclus dans l'export, dans l'ordre ; seulement les parties choisies.
    const sections = chapitres
      .filter((c) => inclus.has(c.index))
      .map((c) => {
        const fiche = ficheParChapitre[c.index];
        const blocPoints = opts.points && fiche.points?.length
          ? `<h3>Points à retenir</h3>\n<ol>${fiche.points.map((p) => `<li>${echapperHtml(p)}</li>`).join("\n")}</ol>` : "";
        const blocQuiz = opts.quiz && fiche.questions?.length
          ? `<h3>Questions de compréhension</h3>\n` + fiche.questions.map((q, i) => `
          <div class="question">
            <p class="q">Q${i + 1}. ${echapperHtml(q.question)}</p>
            ${opts.reponsesDepliees
              ? `<p class="r">${echapperHtml(q.reponse)}</p>`
              : `<details><summary>Voir la réponse</summary><p>${echapperHtml(q.reponse)}</p></details>`}
          </div>`).join("\n") : "";
        if (!blocPoints && !blocQuiz) return "";
        return `
        <section id="chap-${c.index}">
          <h2>${echapperHtml(titreAffiche(c))}</h2>
          ${blocPoints}
          ${blocQuiz}
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
  .r { margin: 0; padding: .5rem .8rem; background: #f9fafb; border-left: 3px solid #2563eb; border-radius: 4px; }
  details { background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 6px; padding: .5rem .8rem; }
  summary { cursor: pointer; color: #2563eb; } details[open] summary { margin-bottom: .4rem; }
  details p { margin: 0; }
  @media (prefers-color-scheme: dark) {
    body { background: #16181c; color: #e5e7eb; } .meta { color: #9aa0a6; }
    nav { background: #22252b; } .question .q { color: #e5e7eb; }
    section { border-color: #33373e; } h3 { color: #b6bcc4; }
    details, .r { background: #1c1f24; border-color: #33373e; } nav a, summary { color: #6ea8fe; }
    .r { border-left-color: #6ea8fe; }
  }
  @media print { details { border: none; padding: 0; } summary { display: none; } details > p { display: block; } }
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
    const opts = optionsExport();
    if (!opts.points && !opts.quiz) {
      $("ia-statut").textContent = "Export : coche les points clés, le quiz, ou les deux.";
      return;
    }
    const html = construireFicheHtml(opts);
    const base = (docActif.nom || "document").replace(/\.[^.]+$/, "");
    const suffixe = opts.points && opts.quiz ? "fiche_etude" : (opts.points ? "points_cles" : "quiz");
    const nomFichier = `${base}_${suffixe}.html`;

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

  // ── Export HTML du document traduit complet (flag extraction_images_pdf,
  //    fonctionnalité de mode avancé) ─────────────────────────────────────────

  function majBoutonExportDocument() {
    const avance = document.documentElement.classList.contains("avance");
    $("doc-exporter").hidden = !(featureFlags.extraction_images_pdf === true && avance && !!docActif);
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
  // ── Markdown → HTML pour l'export (feature bilbao 315) ─────────────────────
  //
  // Avant : tout le contenu passait par `echapperHtml`, donc les marques du
  // Markdown ressortaient LITTÉRALEMENT dans le fichier exporté —
  // `_Cyclosa octotuberculata_` s'affichait avec ses tirets bas, et
  // `<sup>2</sup>` devenait le texte « <sup>2</sup> » au lieu d'un exposant.
  //
  // Principe de sécurité, à ne pas assouplir : on ÉCHAPPE D'ABORD tout, puis on
  // réintroduit une liste blanche stricte. Le contenu vient d'un LLM et le
  // fichier exporté est ouvert dans un navigateur : laisser passer du HTML brut
  // ferait de chaque traduction un vecteur d'injection. C'est pour ça qu'on ne
  // « désactive » jamais l'échappement — on le lève ponctuellement, balise par
  // balise connue.

  // Seules balises rendues telles quelles (celles qu'un texte scientifique
  // utilise réellement, et qui ne peuvent rien exécuter).
  const BALISES_AUTORISEES = ["sup", "sub", "em", "strong", "i", "b"];

  function markdownEnLigne(texte) {
    let s = echapperHtml(texte);

    // 1. Rétablit la liste blanche (ouvrantes et fermantes, sans attribut).
    for (const balise of BALISES_AUTORISEES) {
      s = s.replace(new RegExp(`&lt;(/?)${balise}&gt;`, "gi"), `<$1${balise}>`);
    }

    // 2. Code littéral EN PREMIER : son contenu ne doit subir aucune autre
    //    règle, sinon `**` dans un extrait de code deviendrait du gras.
    const codes = [];
    s = s.replace(/`([^`]+)`/g, (_, c) => {
      codes.push(c);
      return `\x00CODE${codes.length - 1}\x00`;
    });

    // 3. Liens — protocole restreint : `javascript:` dans un document traduit
    //    n'a aucune raison d'exister et serait exécutable au clic.
    s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (tout, libelle, url) =>
      /^(https?:\/\/|#|mailto:)/i.test(url)
        ? `<a href="${url}">${libelle}</a>`
        : tout
    );

    // 4. Marques d'emphase. Le gras avant l'italique : sinon `**x**` serait
    //    consommé comme deux italiques imbriqués.
    s = s.replace(/\*\*\*([^*]+)\*\*\*/g, "<strong><em>$1</em></strong>");
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>");
    // Tirets bas : bornés par une frontière de mot, pour ne pas transformer un
    // identifiant comme `nom_de_variable` en italique au milieu d'une phrase.
    s = s.replace(/(^|[\s(])__([^_\n]+)__(?=[\s).,;:!?]|$)/g, "$1<strong>$2</strong>");
    s = s.replace(/(^|[\s(])_([^_\n]+)_(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>");
    s = s.replace(/~~([^~]+)~~/g, "<del>$1</del>");

    // 5. Restaure le code littéral.
    s = s.replace(/\x00CODE(\d+)\x00/g, (_, i) => `<code>${codes[Number(i)]}</code>`);
    return s;
  }

  /** Une ligne est-elle un séparateur horizontal (`---`, `***`, `* * *`) ? */
  function estSeparateur(ligne) {
    const t = ligne.trim();
    return /^([-*_]\s*){3,}$/.test(t);
  }

  async function chapitreEnHtml(markdown) {
    const lignes = markdown.split("\n");
    let html = "";
    let paragraphe = [];
    let liste = null;      // "ul" | "ol" | null
    let citation = [];
    let tableau = [];

    const vider = () => {
      if (paragraphe.length) html += `<p>${markdownEnLigne(paragraphe.join(" "))}</p>\n`;
      paragraphe = [];
    };
    const fermerListe = () => {
      if (liste) { html += `</${liste}>\n`; liste = null; }
    };
    const fermerCitation = () => {
      if (citation.length) {
        html += `<blockquote><p>${markdownEnLigne(citation.join(" "))}</p></blockquote>\n`;
        citation = [];
      }
    };
    const fermerTableau = () => {
      if (!tableau.length) { return; }
      // Ligne de séparation (|---|---|) : elle marque l'en-tête, pas une donnée.
      const cellules = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const estSeparation = (l) => /^\|?[\s:|-]+\|[\s:|-]*$/.test(l.trim());
      const corps = tableau.filter((l) => !estSeparation(l));
      const avecEntete = tableau.length > 1 && estSeparation(tableau[1]);
      let out = "<table>\n";
      corps.forEach((ligne, i) => {
        const balise = avecEntete && i === 0 ? "th" : "td";
        const tds = cellules(ligne).map((c) => `<${balise}>${markdownEnLigne(c)}</${balise}>`).join("");
        out += `<tr>${tds}</tr>\n`;
      });
      html += out + "</table>\n";
      tableau = [];
    };
    const toutFermer = () => { vider(); fermerListe(); fermerCitation(); fermerTableau(); };

    for (const ligne of lignes) {
      const t = ligne.trim();
      if (/^<!--.*-->$/.test(t)) continue;      // commentaire HTML : invisible (voir rendreContenu)
      const image = t.match(/^!\[([^\]]*)\]\(([^)]+)\)$/);
      const titre = ligne.match(/^(#{1,6})\s+(.*)/);
      const puce = ligne.match(/^\s*[-*+]\s+(.*)/);
      const numero = ligne.match(/^\s*\d+[.)]\s+(.*)/);
      const cite = ligne.match(/^\s*>\s?(.*)/);

      if (image) {
        toutFermer();
        const src = await imageEnDataUri(image[2]);
        html += `<img src="${src}" alt="${echapperHtml(image[1])}" class="doc-image">\n`;
      } else if (t.startsWith("|") && t.endsWith("|")) {
        vider(); fermerListe(); fermerCitation();
        tableau.push(ligne);
      } else if (estSeparateur(t)) {
        // Un séparateur n'est ni un titre ni du texte : Ollama le préfixe
        // parfois d'un « # », d'où des faux chapitres « * * * ».
        toutFermer();
        html += "<hr>\n";
      } else if (titre) {
        toutFermer();
        const niveau = Math.min(titre[1].length + 2, 6); // h1 du doc = le chapitre
        html += `<h${niveau}>${markdownEnLigne(titre[2])}</h${niveau}>\n`;
      } else if (cite) {
        vider(); fermerListe(); fermerTableau();
        citation.push(cite[1]);
      } else if (puce || numero) {
        vider(); fermerCitation(); fermerTableau();
        const voulue = puce ? "ul" : "ol";
        if (liste !== voulue) { fermerListe(); html += `<${voulue}>\n`; liste = voulue; }
        // Retrait de la source (deux espaces par niveau, ex. table des matières) conservé à l'export.
        const retrait = Math.floor(ligne.match(/^\s*/)[0].length / 2);
        const style = retrait ? ` style="margin-left:${retrait * 1.2}rem"` : "";
        html += `<li${style}>${markdownEnLigne((puce || numero)[1])}</li>\n`;
      } else if (t === "") {
        toutFermer();
      } else {
        fermerListe(); fermerCitation(); fermerTableau();
        paragraphe.push(t);
      }
    }
    toutFermer();
    return html;
  }

  // Un chapitre (sous-titre) dont le contenu est déjà inclus dans un chapitre
  // parent précédent (niveau moins profond, plage de lignes englobante) ne doit
  // pas être exporté une seconde fois — même règle que le moteur de traduction
  // (_est_couvert_par_ancetre côté backend) pour ne jamais dupliquer un contenu
  // déjà présent ailleurs dans le document exporté.
  function estChapitreImbrique(chap, tous) {
    for (const autre of tous) {
      if (autre.index >= chap.index) break;
      if (
        autre.niveau < chap.niveau
        && autre.ligne_debut < chap.ligne_debut
        && chap.ligne_debut < autre.ligne_fin
      ) {
        return true;
      }
    }
    return false;
  }

  async function construireDocumentHtml() {
    const titre = echapperHtml(docActif.nom || "Document");
    const meta = [
      docActif.langue_source && docActif.langue_cible
        ? `${echapperHtml(docActif.langue_source)} → ${echapperHtml(docActif.langue_cible)}` : "",
      docActif.modele ? `Modèle : ${echapperHtml(docActif.modele)}` : "",
      docActif.maj_a ? `Traduit le ${echapperHtml(new Date(docActif.maj_a).toLocaleString("fr-CA"))}` : "",
    ].filter(Boolean).join(" · ");

    // Chapitres de sommet uniquement : un sous-titre imbriqué est déjà rendu
    // dans le contenu de son parent (voir chapitreEnHtml) — l'exporter aussi
    // comme section séparée dupliquerait sa traduction.
    const chapitresSommet = chapitres.filter((c) => !estChapitreImbrique(c, chapitres));

    const toc = chapitresSommet.map((c) =>
      `<li style="margin-left:${(Math.max(c.niveau, 1) - 1) * 1.2}rem"><a href="#chap-${c.index}">${echapperHtml(titreAffiche(c))}</a></li>`
    ).join("\n");

    const sections = [];
    for (const c of chapitresSommet) {
      const data = await apiPost("/chapitres/contenu", { chemin_md: docActif.chemin_sortie, index: c.index });
      const corps = await chapitreEnHtml(data.contenu);
      sections.push(`<section id="chap-${c.index}"><h2>${echapperHtml(titreAffiche(c))}</h2>${corps}</section>`);
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
  blockquote { margin: 1rem 0; padding: .2rem 0 .2rem 1rem; border-left: 3px solid #d1d5db; color: #4b5563; }
  code { background: #f4f5f7; padding: .1em .35em; border-radius: 4px; font-size: .9em; }
  hr { border: 0; border-top: 1px solid #e5e7eb; margin: 2rem 0; }
  ul, ol { padding-left: 1.4rem; } li { margin: .2rem 0; }
  /* Un tableau large doit défiler dans son cadre, pas élargir la page. */
  table { border-collapse: collapse; width: 100%; margin: 1rem 0; display: block; overflow-x: auto; }
  th, td { border: 1px solid #e5e7eb; padding: .4rem .6rem; text-align: left; vertical-align: top; }
  th { background: #f4f5f7; font-weight: 600; }
  sup, sub { line-height: 0; }
  @media (prefers-color-scheme: dark) {
    body { background: #16181c; color: #e5e7eb; } .meta { color: #9aa0a6; }
    nav { background: #22252b; } section { border-color: #33373e; } h3 { color: #b6bcc4; }
    nav a { color: #6ea8fe; }
    blockquote { border-color: #3a3f47; color: #b6bcc4; }
    code, th { background: #22252b; } th, td { border-color: #33373e; }
    hr { border-color: #33373e; }
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
  // Le mode avancé se règle maintenant par l'interrupteur de ⚙ Réglages (#avance-toggle, évènement
  // "change"). commun.js bascule la classe "avance" AVANT que ce listener ne s'exécute (script
  // chargé plus tôt, même évènement) — pas besoin de délai.
  $("avance-toggle").addEventListener("change", majBoutonExportDocument);

  // ── Rafraîchissements ───────────────────────────────────────────────────────

  document.addEventListener("module-affiche", (e) => {
    if (e.detail === "bibliotheque") chargerDocs();
  });
  document.addEventListener("traduction-terminee", chargerDocs);
  // Demandé par l'Accueil (js/parcours.js) : ouvrir un document précis, sans passer par la liste.
  document.addEventListener("ouvrir-document", async (e) => {
    await chargerDocs();
    const cible = docs.find((d) => d.chemin_sortie === (e.detail && e.detail.chemin_sortie));
    if (cible) selectionnerDoc(cible);
  });
  document.addEventListener("backend-connecte", chargerDocs);

  /**
   * Suppression en cascade (feature 320).
   *
   * `chargerDocs()` rafraîchit bien la LISTE, mais ne touchait pas à l'état de
   * lecture : `docActif`, ses chapitres, le chapitre ouvert, les cases cochées
   * et les fiches restaient en mémoire. Supprimer depuis « Vos traductions » le
   * document qu'on était en train de lire laissait donc une liste à jour d'un
   * côté et un texte fantôme de l'autre — avec des boutons qui appelaient un
   * document que le backend ne connaissait plus.
   */
  document.addEventListener("document-supprime", (e) => {
    const supprime = e.detail && e.detail.chemin_sortie;
    if (docActif && docActif.chemin_sortie === supprime) {
      arreterPollFiche();
      arreterPollAudio();
      docActif = null;
      document.dispatchEvent(new CustomEvent("doc-actif", { detail: null }));
      chapActif = null;
      chapitres = [];
      chapitresCoches = new Set();
      ficheParChapitre = {};
      audio.removeAttribute("src");
      $("lecture-titre").hidden = true;
      $("lecture-texte").textContent = "";
      rendreChapitres();
      rendreFiche();
    }
    chargerDocs();
  });
})();
