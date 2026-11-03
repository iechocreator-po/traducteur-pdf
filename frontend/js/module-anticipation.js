// Module E — « Anticipation » : ce qui est envisagé et pas encore construit, plus le rapport d'audit du code.
// Cartes lues dans anticipation/cards.json (format repris de castellana, cloud/public/js/anticipation-tab.js,
// et de bilbao) : épingles, filtre par statut avec compteurs, tri par date, dernière revue et dernier audit,
// sous-entrées, commits de livraison, références. Pas de votes : un seul utilisateur.
// Chaque carte ouvre une page autonome du frontend dans un nouvel onglet ; le chemin est restreint à docs/
// (sans « .. ») pour qu'une entrée de cards.json ne puisse pas viser autre chose.

const AnticipationRendu = (() => {
  const STATUTS = {
    rapport: ["Rapport", "ds-badge--accent"],
    maquette: ["Maquette", "ds-badge--warning"],
    planifiee: ["Planifiée", "ds-badge--accent"],
    en_construction: ["En construction", "ds-badge--warning"],
    livree: ["Livrée", "ds-badge--success"],
    rejetee: ["Rejetée", "ds-badge--danger"],
  };
  const REVUE_STATUTS = {
    corrigee: ["Constats corrigés", "ds-badge--success"],
    constats_ouverts: ["Constats ouverts", "ds-badge--warning"],
    en_cours: ["En cours", "ds-badge--warning"],
  };

  function esc(t) {
    return String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function statut(s) { return Object.hasOwn(STATUTS, s) ? STATUTS[s] : ["Statut inconnu", ""]; }
  function pageValide(p) { return typeof p === "string" && /^docs\/[a-z0-9][a-z0-9/-]*\.html$/.test(p) && !p.includes("//"); }

  // Préférence de CE navigateur ({ id: true|false }), qui l'emporte sur `epingle` de cards.json.
  function epinglee(c, epingles) { return typeof epingles[c?.id] === "boolean" ? epingles[c.id] : c?.epingle === true; }

  // Identifiants de commit seulement (hexadécimal) : le dépôt est privé, donc du texte, pas un lien.
  function commitsTexte(commits) {
    return (Array.isArray(commits) ? commits : []).filter((h) => /^[0-9a-f]{7,40}$/.test(String(h)))
      .map((h) => `<code>${esc(h.slice(0, 7))}</code>`).join(", ");
  }
  function commitsHTML(commits) {
    const cm = commitsTexte(commits);
    return cm ? `<p class="aide antic-commits">Livré dans ${cm}</p>` : "";
  }

  // Dernière revue et dernier audit, mis à jour à la main après chaque /revue-code. Absent = rien d'affiché.
  function revueHTML(c) {
    const r = c?.derniereRevue && typeof c.derniereRevue === "object" ? c.derniereRevue : null;
    const a = c?.dernierAudit && typeof c.dernierAudit === "object" ? c.dernierAudit : null;
    if (!r && !a) return "";
    const ligne = (titre, x, badge) => {
      const cm = commitsTexte(x.commits);
      return `<li><strong>${titre}</strong> : ${esc(x.date)}${badge}${x.portee ? ` <span class="aide">— ${esc(x.portee)}</span>` : ""}${cm ? ` <span class="aide">(${cm})</span>` : ""}${x.resultat ? `<div class="aide">${esc(x.resultat)}</div>` : ""}</li>`;
    };
    const st = r && Object.hasOwn(REVUE_STATUTS, r.statut) ? REVUE_STATUTS[r.statut] : null;
    return `<ul class="antic-revue">${r ? ligne("Dernière revue du code", r, st ? ` <span class="ds-badge ${st[1]}">${st[0]}</span>` : "") : ""}${a ? ligne("Dernier audit", a, "") : ""}</ul>`;
  }

  // Seuls les liens http(s) deviennent cliquables.
  function liensHTML(liens) {
    const ok = (Array.isArray(liens) ? liens : []).filter((l) => l && /^https?:\/\//i.test(l.url || ""));
    if (!ok.length) return "";
    return '<div class="antic-liens aide"><strong>Références</strong> (nouvel onglet)<ul>'
      + ok.map((l) => `<li><a href="${esc(l.url)}" target="_blank" rel="noopener noreferrer">${esc(l.titre || l.url)}</a>${l.note ? ` — ${esc(l.note)}` : ""}</li>`).join("")
      + "</ul></div>";
  }

  function elementsHTML(elements) {
    const liste = Array.isArray(elements) ? elements : [];
    if (!liste.length) return "";
    return '<ul class="antic-elements">' + liste.map((e) => {
      const [libelle, cls] = statut(e?.statut);
      const fid = Number.isInteger(e?.featureId) ? ` <span class="aide">#${e.featureId}</span>` : "";
      return `<li><div><span class="ds-badge ${cls}">${libelle}</span> <strong>${esc(e?.titre)}</strong>${fid}</div>
        ${e?.description ? `<div class="aide">${esc(e.description)}</div>` : ""}
        ${e?.statut === "livree" ? commitsHTML(e.commits) : ""}</li>`;
    }).join("") + "</ul>";
  }

  function carteHTML(c, estEpinglee) {
    const [libelle, cls] = statut(c?.statut);
    const bouton = pageValide(c?.page)
      ? `<a class="ds-btn ds-btn--primary ds-btn--sm" href="${esc(c.page)}" target="_blank" rel="noopener">${esc(c.libelleBouton || "Ouvrir la maquette")}</a>` : "";
    return `<article class="ds-card carte antic-carte${estEpinglee ? " is-pinned" : ""}" data-antic-id="${esc(c?.id)}">
      <div class="antic-tete">
        <h2>${esc(c?.titre)} <span class="ds-badge ${cls}">${libelle}</span></h2>
        <button type="button" class="antic-epingle${estEpinglee ? " is-on" : ""}" aria-pressed="${estEpinglee}" data-antic-epingle="${esc(c?.id)}"
          title="${estEpinglee ? "Désépingler : la carte retourne dans la liste" : "Épingler : la carte reste en haut de l'onglet"}">📌 ${estEpinglee ? "Épinglée" : "Épingler"}</button>
      </div>
      <p class="aide">${esc(c?.date)}${Number.isInteger(c?.featureId) ? ` · feature #${c.featureId}` : ""}${c?.onglet ? ` · onglet ${esc(c.onglet)}` : ""}</p>
      ${revueHTML(c)}
      <p>${esc(c?.description)}</p>
      ${elementsHTML(c?.elements)}
      ${liensHTML(c?.liens)}
      ${c?.statut === "livree" ? commitsHTML(c.commits) : ""}
      ${bouton ? `<div class="antic-actions">${bouton}</div>` : ""}
    </article>`;
  }

  // Cartes épinglées en haut, puis barre (filtre par statut avec compteurs, tri) et la liste.
  // Les compteurs ne comptent que les cartes non épinglées, qui seules sont filtrées.
  function listeHTML(cards, etat) {
    const epingles = etat.epingles || {};
    const tri = (a, b) => (etat.tri === "ancien"
      ? String(a.date).localeCompare(String(b.date))
      : String(b.date).localeCompare(String(a.date)));
    const hautes = cards.filter((c) => epinglee(c, epingles)).sort(tri);
    const listees = cards.filter((c) => !epinglee(c, epingles));
    const presents = Object.keys(STATUTS).filter((k) => listees.some((c) => c.statut === k));
    const filtre = etat.filtre === "toutes" || presents.includes(etat.filtre) ? etat.filtre : "toutes";
    const puces = ["toutes", ...presents].map((k) => {
      const n = k === "toutes" ? listees.length : listees.filter((c) => c.statut === k).length;
      const on = filtre === k;
      return `<button type="button" class="antic-puce${on ? " is-on" : ""}" aria-pressed="${on}" data-antic-filtre="${esc(k)}">${k === "toutes" ? "Toutes" : STATUTS[k][0]} (${n})</button>`;
    }).join("");
    const visibles = listees.filter((c) => filtre === "toutes" || c.statut === filtre).sort(tri);
    return `${hautes.map((c) => carteHTML(c, true)).join("")}
      ${listees.length ? `<div class="antic-barre">
        <div class="antic-puces" role="group" aria-label="Filtrer par statut">${puces}</div>
        <label class="aide">Trier par
          <select class="ds-select" data-antic-tri>
            <option value="date"${etat.tri !== "ancien" ? " selected" : ""}>Les plus récentes</option>
            <option value="ancien"${etat.tri === "ancien" ? " selected" : ""}>Les plus anciennes</option>
          </select></label>
      </div>` : ""}
      ${visibles.map((c) => carteHTML(c, false)).join("") || (listees.length ? '<p class="aide">Aucune carte dans ce statut.</p>' : "")}`;
  }

  return { esc, epinglee, pageValide, revueHTML, liensHTML, elementsHTML, carteHTML, listeHTML };
})();

(() => {
  const CLE_EPINGLES = "toledo-antic-epingles";
  let cards = null;
  let epingles = {};
  try { const v = JSON.parse(localStorage.getItem(CLE_EPINGLES) || "{}"); epingles = v && typeof v === "object" && !Array.isArray(v) ? v : {}; } catch { epingles = {}; }
  const etat = { filtre: "toutes", tri: "date", epingles };

  function rendre() {
    const boite = $("anticipation-liste");
    if (!boite || !cards) return;
    boite.innerHTML = cards.length ? AnticipationRendu.listeHTML(cards, etat) : '<p class="aide">Aucune idée en maquette pour l\'instant.</p>';
  }

  async function charger() {
    if (cards) return;
    const boite = $("anticipation-liste");
    try {
      const rep = await fetch("anticipation/cards.json", { cache: "no-store" });
      if (!rep.ok) throw new Error(`HTTP ${rep.status}`);
      const data = await rep.json();
      if (!Array.isArray(data?.cards)) throw new Error("cards.json invalide");
      cards = data.cards;
      rendre();
    } catch (err) {
      if (boite) boite.innerHTML = `<p class="aide">Échec du chargement des cartes : ${AnticipationRendu.esc(err.message)}</p>`;
    }
  }

  const boite = $("anticipation-liste");
  boite?.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-antic-epingle], button[data-antic-filtre]");
    if (!btn || !cards) return;
    if (btn.dataset.anticFiltre) {
      etat.filtre = btn.dataset.anticFiltre;
    } else {
      const c = cards.find((x) => x.id === btn.dataset.anticEpingle);
      if (!c) return;
      epingles[c.id] = !AnticipationRendu.epinglee(c, epingles);
      try { localStorage.setItem(CLE_EPINGLES, JSON.stringify(epingles)); } catch { /* l'épingle dure jusqu'au rechargement */ }
    }
    rendre();
  });
  boite?.addEventListener("change", (e) => {
    if (!e.target.matches("select[data-antic-tri]")) return;
    etat.tri = e.target.value;
    rendre();
  });

  document.addEventListener("module-affiche", (e) => { if (e.detail === "anticipation") charger(); });
  if (!$("module-anticipation")?.hidden) charger(); // onglet restauré au démarrage
})();
