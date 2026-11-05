/**
 * Bibliothèque — exports d'un document et signature (bandeau de lecture).
 *
 *  - « ⬇ PDF mis en page » / « ⬇ HTML interactif » : seulement pour une traduction issue du
 *    Scan multi-colonnes (GET /reconstruction/info). Le backend recrée le document au format
 *    d'origine avec ses illustrations (services/reconstruction.py) ; le fichier est écrit à côté
 *    de la traduction, puis téléchargé par /telecharger.
 *  - « ✍ Signer un PDF… » : signature VISUELLE (dessinée, tapée ou importée) placée à la souris
 *    sur une ou plusieurs pages ; le backend écrit une copie `_signe.pdf` (services/signature.py).
 *
 * Le document actif arrive par l'évènement "doc-actif" émis par module-bibliotheque.js.
 */
(function () {
  let doc = null;
  let infoReconstruction = null;
  const TIMEOUT_GENERATION_MS = 5 * 60 * 1000;   // un long scan peut demander plusieurs dizaines de secondes

  const statut = (t) => { $("doc-export-statut").textContent = t || ""; };

  async function majBoutons() {
    const r = featureFlags.reconstruction_scan === true;
    const s = featureFlags.signature_pdf === true;
    $("doc-pdf-mise-en-page").hidden = true;
    $("doc-html-interactif").hidden = true;
    $("doc-signer").hidden = !(s && doc);
    infoReconstruction = null;
    if (!doc || !r) return;
    const courant = doc;
    try {
      const info = await apiGet(`/reconstruction/info?chemin_sortie=${encodeURIComponent(doc.chemin_sortie)}`);
      if (courant !== doc) return;   // un autre document a été choisi entre-temps
      infoReconstruction = info;
      $("doc-pdf-mise-en-page").hidden = !info.disponible;
      $("doc-html-interactif").hidden = !info.disponible;
    } catch { /* backend absent : boutons masqués */ }
  }

  async function telecharger(chemin) {
    // Passage par un blob : l'attribut download est ignoré d'une origine à l'autre (5500 → 8000).
    const rep = await fetch(`${API_BASE}/telecharger?chemin=${encodeURIComponent(chemin)}`);
    if (!rep.ok) throw new Error(`Téléchargement impossible (${rep.status}).`);
    const url = URL.createObjectURL(await rep.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = nomFichier(chemin);
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }

  async function generer(format) {
    if (!doc) return;
    const bouton = $(format === "pdf" ? "doc-pdf-mise-en-page" : "doc-html-interactif");
    const libelle = bouton.textContent;
    bouton.disabled = true;
    bouton.textContent = "Préparation…";
    statut(format === "pdf" ? "⏳ Reconstruction du PDF (illustrations + colonnes)…" : "⏳ Construction de la page HTML…");
    try {
      const titre = (doc.nom || "").replace(/_scan_colonnes\.md$|\.md$/i, "");
      const r = await apiPost(
        `/reconstruction/${format}?chemin_sortie=${encodeURIComponent(doc.chemin_sortie)}&titre=${encodeURIComponent(titre)}`,
        {}, TIMEOUT_GENERATION_MS);
      await telecharger(r.chemin);
      let msg = `✅ ${r.pages} page(s), ${r.illustrations} illustration(s) — enregistré aussi à côté de la traduction : ${nomFichier(r.chemin)}`;
      if (r.pages_suite && r.pages_suite.length) {
        msg += ` · ⚠ texte trop long pour la page d'origine, continué sur une page « suite » : p. ${r.pages_suite.join(", ")}`;
      }
      statut(msg);
    } catch (e) {
      statut(`❌ ${e.message}`);
    } finally {
      bouton.disabled = false;
      bouton.textContent = libelle;
    }
  }

  $("doc-pdf-mise-en-page").addEventListener("click", () => generer("pdf"));
  $("doc-html-interactif").addEventListener("click", () => generer("html"));
  document.addEventListener("doc-actif", (e) => { doc = e.detail; statut(""); majBoutons(); });
  document.addEventListener("flags-charges", majBoutons);

  // ══ Signature ═══════════════════════════════════════════════════════════════

  const CLE_MEMO = "toledo-signature";
  const dlg = $("sig-dialog");
  const canvas = $("sig-canvas");
  const ctx = canvas.getContext("2d");
  let mode = "dessiner";
  let traits = false;              // le canevas contient-il un tracé ?
  let imageImportee = null;        // data-URI PNG (fond blanc rendu transparent)
  let pdfInfo = null;              // { pages, formats }
  let pageCourante = 1;
  let placements = {};             // page → { x, y, largeur, hauteur } (fractions de la page)
  let signatureUrl = null;         // data-URI PNG de la signature courante (rognée)

  const sigStatut = (t) => { $("sig-statut").textContent = t || ""; };

  // ── Création de la signature ──────────────────────────────────────────────

  function choisirMode(m) {
    mode = m;
    document.querySelectorAll(".sig-onglet").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.sigMode === m)));
    document.querySelectorAll(".sig-panneau").forEach((p) => { p.hidden = p.dataset.sigPanneau !== m; });
    majSignature();
  }
  document.querySelectorAll(".sig-onglet").forEach((b) => b.addEventListener("click", () => choisirMode(b.dataset.sigMode)));

  function effacerCanevas() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    traits = false;
    majSignature();
  }
  $("sig-effacer").addEventListener("click", effacerCanevas);

  let dessin = null;
  function pointCanevas(e) {
    const r = canvas.getBoundingClientRect();
    return [(e.clientX - r.left) * canvas.width / r.width, (e.clientY - r.top) * canvas.height / r.height];
  }
  canvas.addEventListener("pointerdown", (e) => {
    canvas.setPointerCapture(e.pointerId);
    dessin = pointCanevas(e);
    ctx.strokeStyle = $("sig-couleur").value;
    ctx.lineWidth = 3.2;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
  });
  canvas.addEventListener("pointermove", (e) => {
    if (!dessin) return;
    const p = pointCanevas(e);
    // épaisseur légèrement modulée par la pression (stylet), sinon constante
    ctx.lineWidth = e.pressure && e.pointerType === "pen" ? 1.5 + e.pressure * 3.5 : 3.2;
    ctx.beginPath();
    ctx.moveTo(dessin[0], dessin[1]);
    ctx.lineTo(p[0], p[1]);
    ctx.stroke();
    dessin = p;
    traits = true;
  });
  const finTrait = () => { if (dessin) { dessin = null; majSignature(); } };
  canvas.addEventListener("pointerup", finTrait);
  canvas.addEventListener("pointercancel", finTrait);

  $("sig-texte").addEventListener("input", majSignature);
  $("sig-police").addEventListener("change", majSignature);
  $("sig-couleur").addEventListener("change", () => {
    if (mode === "taper") majSignature();
  });

  $("sig-fichier").addEventListener("change", async () => {
    const f = $("sig-fichier").files[0];
    imageImportee = null;
    if (!f) { majSignature(); return; }
    if (f.size > 2 * 1024 * 1024) { sigStatut("❌ Image trop lourde (2 Mo au plus)."); majSignature(); return; }
    const img = new Image();
    img.onload = () => {
      const c = document.createElement("canvas");
      const ech = Math.min(1, 1200 / Math.max(img.width, img.height));
      c.width = Math.round(img.width * ech);
      c.height = Math.round(img.height * ech);
      const g = c.getContext("2d");
      g.drawImage(img, 0, 0, c.width, c.height);
      // fond clair → transparent (photo d'une signature sur papier)
      const d = g.getImageData(0, 0, c.width, c.height);
      for (let i = 0; i < d.data.length; i += 4) {
        const lum = (d.data[i] + d.data[i + 1] + d.data[i + 2]) / 3;
        if (lum > 225) d.data[i + 3] = 0;
      }
      g.putImageData(d, 0, 0);
      imageImportee = c.toDataURL("image/png");
      URL.revokeObjectURL(img.src);
      majSignature();
    };
    img.onerror = () => { sigStatut("❌ Image illisible."); };
    img.src = URL.createObjectURL(f);
  });

  /** Rogne un canevas à son contenu non transparent (+ marge). null s'il est vide. */
  function rogner(source) {
    const g = source.getContext("2d");
    const { width: w, height: h } = source;
    const d = g.getImageData(0, 0, w, h).data;
    let x0 = w, y0 = h, x1 = -1, y1 = -1;
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        if (d[(y * w + x) * 4 + 3] > 8) {
          if (x < x0) x0 = x; if (x > x1) x1 = x;
          if (y < y0) y0 = y; if (y > y1) y1 = y;
        }
      }
    }
    if (x1 < 0) return null;
    const m = 6;
    x0 = Math.max(0, x0 - m); y0 = Math.max(0, y0 - m);
    x1 = Math.min(w - 1, x1 + m); y1 = Math.min(h - 1, y1 + m);
    const c = document.createElement("canvas");
    c.width = x1 - x0 + 1;
    c.height = y1 - y0 + 1;
    c.getContext("2d").drawImage(source, x0, y0, c.width, c.height, 0, 0, c.width, c.height);
    return c;
  }

  function signatureTapee() {
    const texte = $("sig-texte").value.trim();
    if (!texte) return null;
    const c = document.createElement("canvas");
    c.width = 1200;
    c.height = 300;
    const g = c.getContext("2d");
    g.fillStyle = $("sig-couleur").value;
    g.textBaseline = "middle";
    let taille = 150;
    g.font = `${taille}px ${$("sig-police").value}`;
    while (g.measureText(texte).width > c.width - 40 && taille > 40) {
      taille -= 10;
      g.font = `${taille}px ${$("sig-police").value}`;
    }
    g.fillText(texte, 20, c.height / 2);
    return rogner(c);
  }

  function majSignature() {
    let c = null;
    if (mode === "dessiner" && traits) c = rogner(canvas);
    else if (mode === "taper") c = signatureTapee();
    if (c) signatureUrl = c.toDataURL("image/png");
    else signatureUrl = mode === "importer" ? imageImportee : null;
    if (signatureUrl && $("sig-memoriser").checked) memoriser();
    $("sig-boite-img").src = signatureUrl || "";
    if (mode === "taper" && !$("sig-nom").value && $("sig-texte").value) $("sig-nom").placeholder = $("sig-texte").value;
    majValidation();
  }

  function memoriser() {
    try { localStorage.setItem(CLE_MEMO, signatureUrl); } catch { /* stockage indisponible */ }
  }
  $("sig-memoriser").addEventListener("change", () => {
    if ($("sig-memoriser").checked) { if (signatureUrl) memoriser(); }
    else { try { localStorage.removeItem(CLE_MEMO); } catch { /* idem */ } }
  });

  function restaurerMemoire() {
    let memo = null;
    try { memo = localStorage.getItem(CLE_MEMO); } catch { /* idem */ }
    $("sig-memoriser").checked = !!memo;
    if (memo) {
      imageImportee = memo;
      choisirMode("importer");
    }
  }

  // ── Document et pages ─────────────────────────────────────────────────────

  async function chargerPdfs() {
    const sel = $("sig-pdf");
    sel.innerHTML = "";
    const params = new URLSearchParams({ chemin_sortie: doc.chemin_sortie });
    if (doc.chemin_source) params.set("chemin_source", doc.chemin_source);
    const { pdfs } = await apiGet(`/signature/pdfs?${params}`);
    for (const p of pdfs) {
      const o = document.createElement("option");
      o.value = p.chemin;
      o.textContent = `${p.libelle} — ${p.nom}`;
      sel.appendChild(o);
    }
    if (!pdfs.length) {
      const o = document.createElement("option");
      o.value = "";
      o.textContent = "Aucun PDF pour ce document (générez d'abord le « PDF mis en page »)";
      sel.appendChild(o);
    }
    // de préférence la traduction mise en page : c'est elle qu'on signe le plus souvent
    const prefere = pdfs.find((p) => /_mise_en_page\.pdf$/.test(p.chemin));
    if (prefere) sel.value = prefere.chemin;
    await changerPdf();
  }

  async function changerPdf() {
    placements = {};
    pdfInfo = null;
    pageCourante = 1;
    $("sig-page-img").removeAttribute("src");
    const chemin = $("sig-pdf").value;
    if (!chemin) { majPage(); return; }
    try {
      pdfInfo = await apiGet(`/signature/info?chemin_pdf=${encodeURIComponent(chemin)}`);
      // La dernière page est le plus souvent celle qu'on signe.
      pageCourante = pdfInfo.pages;
    } catch (e) {
      sigStatut(`❌ ${e.message}`);
    }
    majPage();
  }
  $("sig-pdf").addEventListener("change", changerPdf);

  function majPage() {
    const n = pdfInfo ? pdfInfo.pages : 0;
    $("sig-page-num").textContent = n ? `Page ${pageCourante} / ${n}` : "—";
    $("sig-prec").disabled = !n || pageCourante <= 1;
    $("sig-suiv").disabled = !n || pageCourante >= n;
    if (n) {
      const largeur = Math.min(1400, Math.round(($("sig-apercu").parentElement.clientWidth || 700) * (window.devicePixelRatio || 1)));
      $("sig-page-img").src = `${API_BASE}/signature/apercu?chemin_pdf=${encodeURIComponent($("sig-pdf").value)}&page=${pageCourante}&largeur=${largeur}`;
    }
    afficherBoite();
    majListe();
  }
  $("sig-prec").addEventListener("click", () => { pageCourante--; majPage(); });
  $("sig-suiv").addEventListener("click", () => { pageCourante++; majPage(); });

  // ── Placement : clic pour poser, glisser pour déplacer, poignée pour redimensionner ──

  const apercu = $("sig-apercu");
  const boite = $("sig-boite");

  function afficherBoite() {
    const p = placements[pageCourante];
    boite.hidden = !p;
    $("sig-retirer").hidden = !p;
    if (!p) return;
    boite.style.left = `${p.x * 100}%`;
    boite.style.top = `${p.y * 100}%`;
    boite.style.width = `${p.largeur * 100}%`;
    boite.style.height = `${p.hauteur * 100}%`;
  }

  function ratioSignature() {
    const img = $("sig-boite-img");
    return img.naturalWidth && img.naturalHeight ? img.naturalHeight / img.naturalWidth : 0.35;
  }

  const borne = (v, a, b) => Math.max(a, Math.min(b, v));

  apercu.addEventListener("click", (e) => {
    if (e.target !== $("sig-page-img") || !pdfInfo) return;
    if (!signatureUrl) { sigStatut("Créez d'abord votre signature (étape 2)."); return; }
    const r = apercu.getBoundingClientRect();
    const [lp, hp] = pdfInfo.formats[pageCourante - 1] || [595, 842];
    const largeur = Math.min(0.35, 170 / lp);                  // ~6 cm sur une page A4
    const hauteur = largeur * ratioSignature() * lp / hp;       // garde les proportions de l'image
    placements[pageCourante] = {
      x: borne((e.clientX - r.left) / r.width - largeur / 2, 0, 1 - largeur),
      y: borne((e.clientY - r.top) / r.height - hauteur / 2, 0, 1 - hauteur),
      largeur, hauteur,
    };
    afficherBoite();
    majListe();
  });

  let geste = null;
  boite.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    boite.setPointerCapture(e.pointerId);
    geste = { type: e.target.id === "sig-poignee" ? "taille" : "deplacer", x: e.clientX, y: e.clientY, depart: { ...placements[pageCourante] } };
  });
  boite.addEventListener("pointermove", (e) => {
    if (!geste) return;
    const r = apercu.getBoundingClientRect();
    const dx = (e.clientX - geste.x) / r.width;
    const dy = (e.clientY - geste.y) / r.height;
    const p = placements[pageCourante];
    const d = geste.depart;
    if (geste.type === "deplacer") {
      p.x = borne(d.x + dx, 0, 1 - p.largeur);
      p.y = borne(d.y + dy, 0, 1 - p.hauteur);
    } else {
      const [lp, hp] = pdfInfo.formats[pageCourante - 1] || [595, 842];
      p.largeur = borne(d.largeur + dx, 0.04, 1 - p.x);
      p.hauteur = borne(p.largeur * ratioSignature() * lp / hp, 0.01, 1 - p.y);
    }
    afficherBoite();
  });
  const finGeste = () => { geste = null; };
  boite.addEventListener("pointerup", finGeste);
  boite.addEventListener("pointercancel", finGeste);

  $("sig-retirer").addEventListener("click", () => { delete placements[pageCourante]; afficherBoite(); majListe(); });

  function majListe() {
    const pages = Object.keys(placements).map(Number).sort((a, b) => a - b);
    $("sig-placements").textContent = pages.length
      ? `Signature placée sur ${pages.length > 1 ? "les pages" : "la page"} ${pages.join(", ")}.`
      : "Aucun emplacement pour l'instant.";
    majValidation();
  }

  function majValidation() {
    $("sig-valider").disabled = !(signatureUrl && Object.keys(placements).length && $("sig-pdf").value);
  }

  // ── Envoi ─────────────────────────────────────────────────────────────────

  function mention() {
    if (!$("sig-mention-active").checked) return null;
    const nom = $("sig-nom").value.trim() || $("sig-texte").value.trim();
    const date = new Date().toLocaleString("fr-CA", { dateStyle: "long", timeStyle: "short" });
    return nom ? `Signé électroniquement par ${nom} le ${date}` : `Signé électroniquement le ${date}`;
  }

  async function signer() {
    const bouton = $("sig-valider");
    bouton.disabled = true;
    sigStatut("⏳ Signature en cours…");
    try {
      const r = await apiPost("/signature", {
        chemin_pdf: $("sig-pdf").value,
        signature_png_base64: signatureUrl,
        placements: Object.entries(placements).map(([page, p]) => ({ page: Number(page), ...p })),
        mention: mention(),
      }, 120000);
      await telecharger(r.chemin);
      sigStatut("");
      statut(`✅ PDF signé (p. ${r.pages.join(", ")}) — enregistré à côté de l'original : ${r.nom}`);
      dlg.close();
    } catch (e) {
      sigStatut(`❌ ${e.message}`);
      bouton.disabled = false;
    }
  }
  $("sig-valider").addEventListener("click", signer);

  // ── Ouverture / fermeture ─────────────────────────────────────────────────

  async function ouvrir() {
    if (!doc) return;
    sigStatut("");
    placements = {};
    restaurerMemoire();
    dlg.showModal();
    try {
      await chargerPdfs();
    } catch (e) {
      sigStatut(`❌ ${e.message}`);
    }
    majSignature();
  }
  $("doc-signer").addEventListener("click", ouvrir);
  $("sig-fermer").addEventListener("click", () => dlg.close());
  $("sig-annuler").addEventListener("click", () => dlg.close());
})();
