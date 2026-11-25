// Laboratoire › Documents complexes, nouvelle version en trois étapes (essai UX du 18/11/2026).
// Seule carte « Documents complexes » depuis le 19/11/2026 (l'ancienne, js/labo-scan-colonnes.js, ne garde que ses
// champs cachés et son éditeur de régions). En étapes : choisir le PDF,
// vérifier les colonnes (facultatif), nettoyer. Mêmes routes, aucun moteur à part :
//   - import : POST /upload ; infos : GET /labo/scan-colonnes/info ;
//   - aperçu : chargerApercuScan() de labo-scan-colonnes.js (partagé, comme le parcours de l'Accueil) ;
//   - nettoyage : POST /labo/scan-colonnes, suivi par GET …/statut, arrêt par POST …/annuler ;
//   - correction manuelle : l'éditeur de régions de labo-scan-colonnes.js, affiché dans l'étape 2.
// « Nettoyer puis traduire » reprend les règles de l'ancienne carte (enchainerScanTermine) : la traduction ne
// part que sur une fin de scan OBSERVÉE par cette page, sans page en erreur, avec les langues et le modèle figés
// au lancement. Rien n'est gardé en localStorage : recharger la page oublie le choix, le bilan garde son bouton.

(() => {
  const etat = { chemin: null, pages: null, page: 1, minuteur: null, vuActif: false, lancement: false, reglages: null, corrige: false };
  const etapes = [$("se-e1"), $("se-e2"), $("se-e3")];
  const esc = (s) => _echapperHtml(String(s ?? ""));
  const nomFichier = (c) => (c || "").split("/").pop();

  function aller(n) {
    if (n !== 1) fermerEditeur();
    etapes.forEach((e, i) => {
      e.classList.toggle("fait", i < n);
      e.classList.toggle("cours", i === n);
    });
    if (n === 1) afficherApercu(parseInt($("se-debut").value, 10) || 1);
    if (n === 2) suivre(false);
  }

  // ── Étape 1 : le PDF ──────────────────────────────────────────────────────

  $("se-parcourir").addEventListener("click", () => $("se-fichier").click());

  $("se-fichier").addEventListener("change", async (e) => {
    const fichier = e.target.files[0];
    e.target.value = "";
    if (!fichier) return;
    info(`⏳ Envoi de ${fichier.name}… (un gros scan peut prendre un moment)`);
    const form = new FormData();
    form.append("fichier", fichier);
    try {
      const rep = await fetch(`${API_BASE}/upload`, { method: "POST", body: form });
      const data = await rep.json().catch(() => ({}));
      if (!rep.ok) throw new Error(data.detail || `Erreur HTTP ${rep.status}`);
      $("se-chemin").value = data.chemin;
      choisir(data.chemin, fichier.name);
    } catch (err) {
      info(`${fichier.name} : ${err.message}`, true);
    }
  });

  $("se-chemin").addEventListener("change", () => {
    const chemin = $("se-chemin").value.trim();
    if (chemin) choisir(chemin, nomFichier(chemin));
  });

  function info(texte, erreur = false) {
    $("se-info").textContent = texte;
    $("se-info").classList.toggle("erreur", erreur);
  }

  async function choisir(chemin, nom) {
    clearTimeout(etat.minuteur);    // le suivi d'un autre PDF s'arrête
    etat.vuActif = false;
    $("se-bilan").hidden = true;
    etat.chemin = null;
    etat.pages = null;
    etat.corrige = false;
    $("se-suivant1").disabled = true;
    info("Lecture du PDF…");
    try {
      const d = await apiGet(`/labo/scan-colonnes/info?chemin_pdf=${encodeURIComponent(chemin)}`, API_TIMEOUT_LONG_MS);
      if (!d.tesseract) { info("Tesseract n'est pas installé : brew install tesseract.", true); return; }
      const noms = { eng: "Anglais", fra: "Français", spa: "Espagnol", deu: "Allemand", ita: "Italien", por: "Portugais" };
      const utiles = d.langues.filter((l) => !["osd", "snum"].includes(l));
      const select = $("se-langue");
      select.innerHTML = utiles.map((l) => `<option value="${esc(l)}">${esc(noms[l] || l)}</option>`).join("");
      if (utiles.includes("eng")) select.value = "eng";
      $("se-fin").max = d.pages;
      etat.chemin = chemin;
      etat.pages = d.pages;
      info(`✓ ${nom} : ${d.pages} page${d.pages > 1 ? "s" : ""}.`);
      $("se-suivant1").disabled = false;
    } catch (err) {
      info(err.message, true);
    }
  }

  function plage() {
    const debut = parseInt($("se-debut").value, 10) || 1;
    const fin = parseInt($("se-fin").value, 10) || etat.pages;
    return [debut, fin];
  }

  $("se-suivant1").addEventListener("click", () => {
    const [debut, fin] = plage();
    const langue = $("se-langue").selectedOptions[0]?.textContent || "";
    const pages = debut === 1 && fin === etat.pages ? "toutes les pages" : `pages ${debut} à ${fin}`;
    $("se-r1").textContent = `${nomFichier(etat.chemin)} · ${langue.toLowerCase()} · ${pages}`;
    aller(1);
  });

  // ── Étape 2 : l'aperçu ────────────────────────────────────────────────────

  async function afficherApercu(page) {
    if (!etat.chemin) return;
    const [debut, fin] = plage();
    etat.page = Math.min(Math.max(debut, page), fin);
    $("se-page").textContent = etat.page;
    $("se-total").textContent = `/ ${fin}`;
    const nav = [$("se-prec"), $("se-suiv")];
    nav.forEach((b) => { b.disabled = true; });
    // En cas d'échec, chargerApercuScan a écrit l'erreur dans le statut : on ne la vide qu'après un aperçu réussi.
    if (await chargerApercuScan(etat.chemin, etat.page, $("se-double-page").value, $("se-apercu"), $("se-apercu-statut"))) {
      $("se-apercu-statut").textContent = "";
    }
    $("se-prec").disabled = etat.page <= debut;
    $("se-suiv").disabled = etat.page >= fin;
  }

  $("se-prec").addEventListener("click", () => afficherApercu(etat.page - 1));
  $("se-suiv").addEventListener("click", () => afficherApercu(etat.page + 1));

  // L'éditeur de régions est dans l'étape 2 (#se-editeur) ; sa logique reste dans js/labo-scan-colonnes.js (tout par
  // id), qui lit le PDF et la mise en page dans les champs cachés #scan-chemin / #scan-double-page : on les lui passe.
  // « Continuer » le replie ; l'aperçu de l'étape est alors redessiné à la page de l'éditeur, avec les corrections.
  const editeurOuvert = () => !$("se-editeur").hidden;

  async function ouvrirEditeur() {
    $("scan-chemin").value = etat.chemin;
    $("scan-double-page").value = $("se-double-page").value;
    $("se-vue").hidden = true;
    $("se-editeur").hidden = false;
    etat.corrige = true;
    await chargerInfoScan(etat.chemin);
    await afficherApercuLabo(etat.page);
    $("se-editeur").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function fermerEditeur() {
    if (!editeurOuvert()) return;
    const page = parseInt($("scan-apercu-page").value, 10) || etat.page;
    $("se-editeur").hidden = true;
    $("se-vue").hidden = false;
    if (etat.chemin) afficherApercu(page);
  }

  $("se-corriger").addEventListener("click", ouvrirEditeur);

  $("se-suivant2").addEventListener("click", () => {
    $("se-r2").textContent = etat.corrige ? "Colonnes corrigées à la main." : "Colonnes vérifiées.";
    aller(2);
    // L'éditeur replié raccourcit la page : on amène l'étape 3 à l'écran, et le focus sur son bouton.
    $("se-e3").scrollIntoView({ behavior: "smooth", block: "start" });
    $("se-lancer").focus({ preventScroll: true });
  });
  document.querySelectorAll("[data-se-retour]").forEach((b) => {
    b.addEventListener("click", () => aller(Number(b.dataset.seRetour)));
  });

  // ── Étape 3 : le nettoyage ────────────────────────────────────────────────

  const traduireEnsuite = () => document.querySelector("input[name=se-suite]:checked")?.value === "traduire";

  $("se-lancer").addEventListener("click", async () => {
    if (!etat.chemin || etat.lancement) return;   // verrou synchrone : un double clic lançait deux scans
    etat.lancement = true;
    const [debut, fin] = plage();
    const corps = { chemin_pdf: etat.chemin, langue_ocr: $("se-langue").value || "eng",
                    double_page: $("se-double-page").value, page_debut: debut, page_fin: fin };
    $("se-bilan").hidden = true;
    $("se-lancer").disabled = true;
    try {
      await apiPost("/labo/scan-colonnes", corps, API_TIMEOUT_LONG_MS);
      // langues et modèle du LANCEMENT, comme l'ancienne carte
      etat.reglages = { langue_source: $("langue-source").value, langue_cible: $("langue-cible").value, modele: $("modele").value };
      etat.vuActif = true;
      suivre(true);
    } catch (err) {
      $("se-lancer").disabled = false;
      bilanErreur(err.message);
    } finally {
      etat.lancement = false;
    }
  });

  $("se-arreter").addEventListener("click", async () => {
    $("se-arreter").disabled = true;
    try {
      await apiPost(`/labo/scan-colonnes/annuler?chemin_pdf=${encodeURIComponent(etat.chemin)}`, {});
    } catch (err) {
      bilanErreur(err.message);
    }
  });

  // `apresLancement` : faux quand on arrive à l'étape 3 sur un PDF déjà nettoyé (on montre son dernier bilan,
  // sans jamais déclencher de traduction).
  async function suivre(apresLancement) {
    clearTimeout(etat.minuteur);
    if (!etat.chemin) return;
    let s;
    try {
      s = await apiGet(`/labo/scan-colonnes/statut?chemin_pdf=${encodeURIComponent(etat.chemin)}`);
    } catch {
      etat.minuteur = setTimeout(() => suivre(apresLancement), 5000);   // backend injoignable : pas de faux « terminé »
      return;
    }
    const actif = !!s && ["en_attente", "en_cours"].includes(s.statut);
    $("se-progres").hidden = !actif;
    $("se-arreter").hidden = !actif;
    $("se-arreter").disabled = false;
    $("se-lancer").disabled = actif;
    $("se-note-lancer").hidden = actif;
    if (actif) {
      etat.vuActif = true;
      const pct = s.pages_total ? Math.round((s.pages_faites / s.pages_total) * 100) : 0;
      $("se-progres-rempli").style.width = `${pct}%`;
      const reste = s.pages_faites > 0 && s.temps_ecoule_secondes
        ? ` · reste ≈ ${formaterDuree((s.temps_ecoule_secondes / s.pages_faites) * (s.pages_total - s.pages_faites))}` : "";
      $("se-progres-texte").textContent = s.statut === "en_attente"
        ? "En attente dans la file…" : `Page ${s.pages_faites} sur ${s.pages_total}${reste}`;
      etat.minuteur = setTimeout(() => suivre(true), 2000);
      return;
    }
    const finObservee = etat.vuActif;
    etat.vuActif = false;
    if (s) bilan(s, finObservee && apresLancement);
  }

  function bilanErreur(message) {
    const b = $("se-bilan");
    b.className = "se-bilan partiel";
    b.innerHTML = `<span class="se-bilan-titre">Le nettoyage n'a pas pu démarrer</span><ul><li>${esc(message)}</li></ul>`;
    b.hidden = false;
  }

  function bilan(s, finObservee) {
    const b = $("se-bilan");
    const erreurs = s.pages_en_erreur || [];
    const redressees = s.pages_redressees || [];
    const ok = s.statut === "termine" && !s.erreur && !erreurs.length;
    const faites = s.pages_faites ?? 0;
    let titre;
    if (s.statut === "annule") titre = `Arrêté après ${faites} page${faites > 1 ? "s" : ""}. Reprendre ne refait pas les pages déjà nettoyées.`;
    else if (ok) titre = `✓ ${faites} page${faites > 1 ? "s" : ""} nettoyée${faites > 1 ? "s" : ""}, aucune en erreur.`;
    else titre = s.erreur ? `Le nettoyage a échoué : ${s.erreur}` : `${erreurs.length} page(s) en erreur sur ${faites}.`;

    const lignes = [];
    if (redressees.length) {
      const angle = (a) => `${Math.abs(a).toFixed(1).replace(".", ",")}°`;
      const pages = [...redressees].sort((x, y) => x.page - y.page)
        .map((r) => `p. ${Number(r.page)} (${r.angles.filter(Boolean).map(angle).join(" / ")})`).join(", ");
      lignes.push(`${redressees.length} page(s) photographiée(s) de biais, redressée(s) : ${pages}. Une photo plus droite et plus nette donnerait un texte plus sûr.`);
    }
    erreurs.slice(0, 5).forEach((p) => lignes.push(`Page ${Number(p.page)} : ${esc(p.erreur)}`));
    if (s.statut !== "annule" && !s.erreur) lignes.push(`Fichier : <code>${esc(nomFichier(s.chemin_sortie))}</code>`);
    const reparations = s.reparations || [];
    const detailReparations = reparations.length
      ? `<details class="se-plus"><summary>${reparations.length} titre(s) ou numéro(s) réparé(s) automatiquement, à vérifier</summary>` +
        `<ul>${reparations.map((r) => `<li>${esc(r)}</li>`).join("")}</ul></details>` : "";

    let actions = "";
    if (s.statut !== "annule" && !s.erreur) {
      if (ok && finObservee && traduireEnsuite()) {
        document.dispatchEvent(new CustomEvent("ajouter-au-lot-et-traduire", { detail: { chemin: s.chemin_sortie, reglages: etat.reglages } }));
        document.dispatchEvent(new CustomEvent("retirer-du-lot", { detail: s.chemin_pdf }));
        actions = `<p class="se-note">Traduction lancée : suivez-la dans « Vos traductions ».</p>` +
          `<div class="se-actions"><button type="button" class="ds-btn ds-btn--primary" id="se-autre">Traiter un autre document</button></div>`;
      } else {
        actions = `<div class="se-actions"><button type="button" class="ds-btn ds-btn--primary" id="se-envoyer">Envoyer vers Nouveau document</button>` +
          `<button type="button" class="ds-btn" id="se-autre">Traiter un autre document</button></div>`;
      }
    } else {
      // Arrêt ou échec : relancer = le même « Traiter le document » (mêmes réglages, cache par page côté backend),
      // avec le choix « seulement / puis traduire » de l'étape tel qu'il est au moment du clic.
      const libelle = s.statut === "annule" ? "Reprendre le nettoyage" : "Réessayer";
      actions = `<div class="se-actions"><button type="button" class="ds-btn ds-btn--primary" id="se-reprendre">${libelle}</button>` +
        `<button type="button" class="ds-btn" id="se-autre">Traiter un autre document</button></div>`;
    }
    b.className = `se-bilan${ok ? "" : " partiel"}`;
    b.innerHTML = `<span class="se-bilan-titre">${esc(titre)}</span>` +
      (lignes.length ? `<ul>${lignes.map((l) => `<li>${l}</li>`).join("")}</ul>` : "") + detailReparations + actions;
    b.hidden = false;
    const envoyer = $("se-envoyer");
    if (envoyer) envoyer.addEventListener("click", () => {
      document.dispatchEvent(new CustomEvent("ajouter-au-lot", { detail: s.chemin_sortie }));
      activerModule("import");
    });
    $("se-autre")?.addEventListener("click", recommencer);
    $("se-reprendre")?.addEventListener("click", () => $("se-lancer").click());
  }

  // Revenir à l'étape 1, carte vide. Le scan déjà lancé n'est pas touché (il continue côté backend, et la
  // traduction enchaînée vit dans « Vos traductions »). Le choix « nettoyer seulement / puis traduire » est
  // gardé : on traite souvent plusieurs documents de la même façon.
  function recommencer() {
    clearTimeout(etat.minuteur);
    Object.assign(etat, { chemin: null, pages: null, page: 1, minuteur: null, vuActif: false, reglages: null, corrige: false });
    $("se-chemin").value = "";
    info("");
    $("se-debut").value = 1;
    $("se-fin").value = "";
    $("se-fin").removeAttribute("max");
    $("se-double-page").value = "auto";
    $("se-suivant1").disabled = true;
    $("se-apercu").hidden = true;
    $("se-apercu").removeAttribute("src");
    $("se-page").textContent = "1";
    $("se-total").textContent = "";
    $("se-apercu-statut").textContent = "";
    $("se-bilan").hidden = true;
    $("se-bilan").innerHTML = "";
    $("se-progres").hidden = true;
    $("se-arreter").hidden = true;
    $("se-lancer").disabled = false;
    $("se-note-lancer").hidden = false;
    aller(0);
    $("carte-scan-etapes").scrollIntoView({ behavior: "smooth", block: "start" });
    $("se-parcourir").focus({ preventScroll: true });
  }
})();
