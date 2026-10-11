// Module D — « Chronique » : historique de ce que chaque traitement a réellement coûté
// (taille du texte, modèle, jetons, durées) et estimation des durées futures.
// Données : GET /api/stats/traitements, GET /api/stats/estimation, POST /api/stats/reconstituer
// (backend : services/stats.py). Une valeur « non mesurée » arrive en null et s'affiche « — »,
// jamais 0 : un faux zéro laisserait croire à une mesure.

(() => {
  const ND = "—";

  function nombre(n) { return n == null ? ND : Math.round(n).toLocaleString("fr-CA"); }

  function duree(s) {
    if (s == null) return ND;
    s = Math.round(s);
    if (s < 60) return `${s} s`;
    if (s < 3600) return `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")} s`;
    return `${Math.floor(s / 3600)} h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")} min`;
  }

  function cellule(ligne, texte, classe) {
    const td = document.createElement("td");
    td.textContent = texte;
    if (classe) td.className = classe;
    if (texte === ND) td.classList.add("chronique-nd");
    ligne.appendChild(td);
    return td;
  }

  function entete(table, colonnes) {
    table.innerHTML = "";
    const tr = document.createElement("tr");
    for (const [titre, num] of colonnes) {
      const th = document.createElement("th");
      th.textContent = titre;
      if (num) th.className = "is-num";
      tr.appendChild(th);
    }
    const thead = document.createElement("thead");
    thead.appendChild(tr);
    table.appendChild(thead);
    return table.appendChild(document.createElement("tbody"));
  }

  const STATUTS = {
    termine: ["Terminé", "ds-badge--success"], erreur: ["Erreur", "ds-badge--danger"],
    en_pause: ["En pause", "ds-badge--warning"], annule: ["Annulé", "ds-badge--warning"],
  };

  function metrique(libelle, valeur, sous) {
    const d = document.createElement("div");
    d.className = "ds-metric";
    d.innerHTML = '<div class="ds-metric__label"></div><div class="ds-metric__value"></div><div class="ds-metric__sub"></div>';
    d.children[0].textContent = libelle;
    d.children[1].textContent = valeur;
    d.children[2].textContent = sous || "";
    return d;
  }

  function rendreVueEnsemble(t) {
    const zone = $("chronique-metriques");
    zone.innerHTML = "";
    zone.append(
      metrique("Passages", nombre(t.passages), "traductions et fiches"),
      metrique("Temps cumulé", duree(t.duree_secondes)),
      metrique("Jetons générés", nombre(t.jetons_sortie), `${nombre(t.jetons_entree)} lus`),
      metrique("Texte traité", nombre(t.caracteres_traites), "caractères (cache exclu)"),
      metrique("Réponses tronquées", nombre(t.reponses_tronquees), "plafond atteint"),
    );
  }

  function rendreModeles(modeles) {
    const corps = entete($("chronique-table-modeles"), [
      ["Type"], ["Modèle"], ["Passages", true], ["Mesurés", true], ["Jetons / s", true],
      ["s / 1 000 car.", true], ["s / 1 000 mots", true], ["Chargement", true],
    ]);
    for (const m of modeles) {
      const tr = document.createElement("tr");
      cellule(tr, m.type === "etude" ? "Fiche" : "Traduction");
      cellule(tr, m.modele || ND);
      cellule(tr, nombre(m.passages), "is-num");
      cellule(tr, nombre(m.mesures), "is-num");
      cellule(tr, m.jetons_par_seconde == null ? ND : m.jetons_par_seconde.toFixed(1), "is-num");
      cellule(tr, m.secondes_par_1000_car == null ? ND : m.secondes_par_1000_car.toFixed(1), "is-num");
      cellule(tr, m.secondes_par_1000_mots == null ? ND : m.secondes_par_1000_mots.toFixed(1), "is-num");
      cellule(tr, m.chargement_moyen_s == null ? ND : duree(m.chargement_moyen_s), "is-num");
      corps.appendChild(tr);
    }
  }

  function rendreRuns(runs) {
    $("chronique-vide").hidden = runs.length > 0;
    const corps = entete($("chronique-table-runs"), [
      ["Date"], ["Fichier"], ["Type"], ["Modèle"], ["Texte (car.)", true], ["Jetons générés", true],
      ["Durée", true], ["Jetons / s", true], ["Cache", true], ["Statut"],
    ]);
    for (const r of runs) {
      const tr = document.createElement("tr");
      cellule(tr, new Date(r.demarre_a * 1000).toLocaleString("fr-CA", { dateStyle: "short", timeStyle: "short" }));
      const nom = cellule(tr, r.nom || ND, "nom");
      nom.title = r.chemin_source || "";
      cellule(tr, (r.type === "etude" ? `Fiche${r.strategie ? " · " + r.strategie : ""}` : "Traduction") + (r.reconstitue ? " · reconstitué" : ""));
      cellule(tr, r.modele || ND);
      cellule(tr, nombre(r.caracteres_traites), "is-num");
      cellule(tr, nombre(r.jetons_sortie), "is-num");
      cellule(tr, duree(r.duree_secondes), "is-num");
      const vitesse = r.jetons_sortie && r.duree_generation_s ? (r.jetons_sortie / r.duree_generation_s).toFixed(1) : ND;
      cellule(tr, vitesse, "is-num");
      cellule(tr, r.morceaux_cache == null ? ND : `${r.morceaux_cache} / ${r.morceaux_total ?? "?"}`, "is-num");
      const td = document.createElement("td");
      const [lib, classe] = STATUTS[r.statut] || [r.statut || ND, ""];
      const b = document.createElement("span");
      b.className = `ds-badge ${classe}`;
      b.textContent = lib;
      td.appendChild(b);
      tr.appendChild(td);
      corps.appendChild(tr);
    }
  }

  function rendreChoixModeles(modeles) {
    const select = $("chronique-modele");
    const courant = select.value;
    const noms = [...new Set(modeles.filter((m) => m.type === "traduction" && m.modele).map((m) => m.modele))];
    // Les modèles installés complètent la liste : on peut vouloir estimer un modèle jamais utilisé
    // (réponse « insuffisant », honnête, plutôt que d'omettre le modèle).
    for (const m of (window.modelesDisponibles || [])) if (!noms.includes(m)) noms.push(m);
    select.innerHTML = "";
    for (const n of noms) {
      const o = document.createElement("option");
      o.value = o.textContent = n;
      select.appendChild(o);
    }
    if (noms.includes(courant)) select.value = courant;
  }

  async function charger() {
    try {
      const d = await apiGet("/stats/traitements?limite=300");
      rendreVueEnsemble(d.agregats.totaux);
      rendreModeles(d.agregats.modeles);
      rendreRuns(d.runs);
      rendreChoixModeles(d.agregats.modeles);
    } catch (e) {
      $("chronique-metriques").textContent = `Impossible de charger l'historique : ${e.message}`;
    }
  }

  $("chronique-estimer").addEventListener("click", async () => {
    const modele = $("chronique-modele").value;
    const taille = parseInt($("chronique-taille").value, 10);
    const zone = $("chronique-estimation");
    if (!modele || !taille || taille < 1) { zone.textContent = "Choisissez un modèle et une taille de texte."; return; }
    const unite = $("chronique-unite").value;
    try {
      const e = await apiGet(`/stats/estimation?modele=${encodeURIComponent(modele)}&${unite}=${taille}`);
      if (e.statut !== "ok") {
        zone.textContent = `Pas assez de mesures pour « ${modele} » (${e.echantillons} sur ${e.minimum} minimum). Traduisez quelques documents, ou reconstituez l'historique ci-dessous.`;
        return;
      }
      const base = e.source === "reconstitue" ? "lignes reconstituées (durée cumulée, cache compris)" : "mesures réelles";
      zone.innerHTML = "";
      const p = document.createElement("p");
      p.innerHTML = "<strong></strong> <span class='aide'></span>";
      p.children[0].textContent = `≈ ${duree(e.secondes)}`;
      p.children[1].textContent = `fourchette ${duree(e.min)} à ${duree(e.max)} · ${e.echantillons} passages · base : ${base}`;
      zone.appendChild(p);
    } catch (err) {
      zone.textContent = `Erreur : ${err.message}`;
    }
  });

  $("chronique-reconstituer").addEventListener("click", async () => {
    const statut = $("chronique-reconstituer-statut");
    statut.textContent = "Reconstitution…";
    try {
      const r = await apiPost("/stats/reconstituer", {});
      statut.textContent = r.ajoutes ? `${r.ajoutes} document(s) ajouté(s) à l'historique.` : "Rien à ajouter : tous les documents sont déjà dans l'historique.";
      charger();
    } catch (e) {
      statut.textContent = `Erreur : ${e.message}`;
    }
  });

  document.addEventListener("module-affiche", (e) => { if (e.detail === "chronique") charger(); });
  if (localStorage.getItem("module") === "chronique") charger();
})();
