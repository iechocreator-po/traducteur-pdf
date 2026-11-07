// Laboratoire › « Découper un PDF » : sections de N pages écrites à côté de la source (services/decoupe.py).
// Le calcul des sections vit côté backend (GET /labo/decoupe/plan) : l'aperçu et le lancement ne peuvent pas diverger.

(() => {
  let planCourant = null;
  let demandeNo = 0;       // une réponse d'aperçu arrivée après une saisie plus récente est ignorée
  let minuteur = null;
  let enCours = false;

  document.addEventListener("flags-charges", () => {
    $("carte-decoupe").hidden = featureFlags.decoupe_pdf !== true;
  });

  function majBouton() {
    const attendConfirmation = planCourant && planCourant.existants.length && !$("decoupe-remplacer").checked;
    $("bouton-decoupe-lancer").disabled = enCours || !planCourant || !!attendConfirmation;
  }

  function resumePlan(p) {
    const derniere = p.sections[p.sections.length - 1].pages;
    const courte = derniere < p.pages_par_section ? ` La dernière en compte ${derniere}.` : "";
    return `<strong>${p.pages} pages → ${p.nb_sections} sections de ${p.pages_par_section} page(s).</strong>${courte}`;
  }

  function rendreApercu(p) {
    let html = `<p>${resumePlan(p)}</p>`;
    if (p.existants.length) {
      html += `<p class="erreur">⚠ ${p.existants.length} fichier(s) de cette découpe existent déjà : ils ne seront remplacés que si vous cochez la case.</p>`;
    }
    if (p.autres_decoupes.length) {
      html += `<p class="aide">${p.autres_decoupes.length} fichier(s) d'une autre découpe de ce PDF sont à côté : ils restent en place.</p>`;
    }
    $("decoupe-apercu").innerHTML = html;
    $("decoupe-remplacer-ligne").hidden = !p.existants.length;
    if (!p.existants.length) $("decoupe-remplacer").checked = false;
  }

  function effacerPlan(message) {
    planCourant = null;
    $("decoupe-apercu").innerHTML = message ? `<span class="erreur">${_echapperHtml(message)}</span>` : "";
    $("decoupe-remplacer-ligne").hidden = true;
    $("decoupe-remplacer").checked = false;
    majBouton();
  }

  async function chargerPlan() {
    const no = ++demandeNo;
    const chemin = $("decoupe-chemin").value.trim();
    const saisie = Number($("decoupe-pages").value);
    if (!chemin) { effacerPlan(""); return; }
    if (!Number.isInteger(saisie) || saisie < 1) {
      effacerPlan("Pages par section : indiquez un nombre entier, 1 ou plus.");
      return;
    }
    // Le plan affiché ne vaut plus pour cette saisie : pas de lancement tant que le nouveau n'est pas là.
    planCourant = null;
    majBouton();
    try {
      const plan = await apiGet(
        `/labo/decoupe/plan?chemin_pdf=${encodeURIComponent(chemin)}&pages_par_section=${saisie}`,
        API_TIMEOUT_LONG_MS,
      );
      if (no !== demandeNo) return;
      planCourant = plan;
      rendreApercu(plan);
      majBouton();
    } catch (e) {
      if (no === demandeNo) effacerPlan(e.message);
    }
  }

  function planifierApercu() {
    clearTimeout(minuteur);
    minuteur = setTimeout(chargerPlan, 250);
  }

  $("decoupe-pages").addEventListener("input", planifierApercu);
  $("decoupe-chemin").addEventListener("change", () => { $("decoupe-resultat").innerHTML = ""; chargerPlan(); });
  $("decoupe-remplacer").addEventListener("change", majBouton);

  $("bouton-decoupe-parcourir").addEventListener("click", () => $("decoupe-fichier").click());
  $("decoupe-fichier").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    e.target.value = "";    // permet de re-choisir le même fichier
    if (!file) return;
    const statut = $("decoupe-upload-statut");
    statut.textContent = `⏳ Envoi de ${file.name}…`;
    const form = new FormData();
    form.append("fichier", file);
    try {
      const rep = await fetch(`${API_BASE}/upload`, { method: "POST", body: form });
      const data = await rep.json().catch(() => ({}));
      if (!rep.ok) throw new Error(data.detail || `Erreur HTTP ${rep.status}`);
      $("decoupe-chemin").value = data.chemin;
      statut.textContent = `✅ ${file.name} envoyé.`;
      $("decoupe-resultat").innerHTML = "";
      chargerPlan();
    } catch (err) {
      statut.textContent = `❌ ${file.name} : ${err.message}`;
    }
  });

  function tailleLisible(octets) {
    if (octets < 1024 * 1024) return `${Math.max(1, Math.round(octets / 1024))} Ko`;
    return `${(octets / (1024 * 1024)).toFixed(1)} Mo`;
  }

  function rendreResultat(r) {
    const lignes = r.sections.map(s => `<tr>
      <td><code>${_echapperHtml(s.nom)}</code></td>
      <td>${s.page_debut} à ${s.page_fin}</td>
      <td>${tailleLisible(s.octets)}</td>
      <td class="decoupe-actions">
        <a class="ds-btn ds-btn--sm" href="${API_BASE}/telecharger?chemin=${encodeURIComponent(s.chemin)}">Télécharger</a>
        <button type="button" class="ds-btn ds-btn--sm" data-section="${s.numero - 1}">Ajouter au lot</button>
      </td>
    </tr>`).join("");
    const remplaces = r.remplaces ? ` Dont ${r.remplaces} remplacé(s).` : "";
    const dossier = r.sections[0].chemin.slice(0, -r.sections[0].nom.length);
    $("decoupe-resultat").innerHTML = `
      <p>✅ ${r.nb_sections} fichiers écrits, ${r.pages} pages au total.${remplaces} Dossier : <code>${_echapperHtml(dossier)}</code></p>
      <div class="decoupe-liste"><table class="ds-table">
        <thead><tr><th>Fichier</th><th>Pages</th><th>Taille</th><th></th></tr></thead>
        <tbody>${lignes}</tbody>
      </table></div>
      <p class="aide">« Ajouter au lot » place la section dans « Nouveau document », où elle est analysée puis traduite comme tout PDF.</p>`;
    // Section par section, pas de « tout envoyer » : chaque ajout au lot déclenche une analyse Ollama (/analyser).
    $("decoupe-resultat").querySelectorAll("button[data-section]").forEach(b => {
      b.addEventListener("click", () => {
        document.dispatchEvent(new CustomEvent("ajouter-au-lot", { detail: r.sections[Number(b.dataset.section)].chemin }));
        b.disabled = true;
        b.textContent = "✓ Dans le lot";
      });
    });
  }

  $("bouton-decoupe-lancer").addEventListener("click", async () => {
    if (!planCourant) return;
    const corps = {
      chemin_pdf: $("decoupe-chemin").value.trim(),
      pages_par_section: planCourant.pages_par_section,
      remplacer: $("decoupe-remplacer").checked,
    };
    enCours = true;
    majBouton();
    $("decoupe-resultat").innerHTML = "<em>⏳ Découpe en cours…</em>";
    try {
      rendreResultat(await apiPost("/labo/decoupe", corps, API_TIMEOUT_LONG_MS));
    } catch (e) {
      $("decoupe-resultat").innerHTML = `<span class="erreur">Erreur : ${_echapperHtml(e.message)}</span>`;
    } finally {
      enCours = false;
    }
    await chargerPlan();    // les fichiers viennent d'apparaître : l'aperçu doit le dire avant une relance
  });
})();
