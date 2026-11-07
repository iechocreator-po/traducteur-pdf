// Module E — « Anticipation » : ce qui est envisagé, construit ou livré, plus le rapport d'audit du code.
// À l'image de l'onglet de castellana dans sa version locale en lecture seule (js/anticipation-tab.js de
// trading-assistant) : mêmes cartes, mêmes statuts, maquette ouverte dans la carte. Pas de votes ni de propositions :
// toledo est local et n'a qu'un utilisateur.
// Cartes lues dans anticipation/cards.json. Le chemin d'une page est restreint à docs/ (sans « .. ») pour qu'une
// entrée de cards.json ne puisse pas viser autre chose. Comme chez castellana, le HTML est lu puis placé dans le
// `srcdoc` d'une iframe `sandbox`. Sans allow-same-origin : ses scripts tournent (les maquettes sont cliquables), sans
// accès à l'application. Une telle iframe ne charge pas toujours ses feuilles de style : elles sont donc intégrées à la
// page (les maquettes de castellana portent déjà leur CSS), avec le thème choisi dans l'application.

const AnticipationRendu = (() => {
  const STATUTS = {
    idee: ["Idée", ""],
    maquette: ["Maquette", ""],
    planifiee: ["Planifiée", "ds-badge--warning"],
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
  function dateFr(iso) {
    const d = new Date(`${iso}T12:00:00`);
    return Number.isNaN(d.getTime()) ? String(iso ?? "") : d.toLocaleDateString("fr-CA", { day: "numeric", month: "short", year: "numeric" });
  }

  // Préférence de CE navigateur ({ id: true|false }), qui l'emporte sur `epingle` de cards.json.
  function epinglee(c, epingles) { return typeof epingles[c?.id] === "boolean" ? epingles[c.id] : c?.epingle === true; }

  // Identifiants de commit seulement (hexadécimal) : le dépôt est privé, donc du texte, pas un lien.
  function commitsValides(commits) {
    return (Array.isArray(commits) ? commits : []).filter((h) => /^[0-9a-f]{7,40}$/.test(String(h)));
  }
  function commitsTexte(commits) {
    return commitsValides(commits).map((h) => `<code>${esc(h.slice(0, 7))}</code>`).join(", ");
  }
  function commitsHTML(commits) {
    const ok = commitsValides(commits);
    return ok.length ? `<div class="aide antic-commits">Livré ${ok.length > 1 ? "dans les commits" : "dans le commit"} ${commitsTexte(ok)}</div>` : "";
  }

  // Dernière revue et dernier audit, mis à jour à la main après chaque /revue-code. Absent = rien d'affiché.
  function revueHTML(c) {
    const r = c?.derniereRevue && typeof c.derniereRevue === "object" ? c.derniereRevue : null;
    const a = c?.dernierAudit && typeof c.dernierAudit === "object" ? c.dernierAudit : null;
    if (!r && !a) return "";
    const ligne = (titre, x, badge) => {
      const cm = commitsTexte(x.commits);
      return `<li><strong>${titre}</strong> : ${esc(dateFr(x.date))}${badge}${x.portee ? ` <span class="aide">— ${esc(x.portee)}</span>` : ""}${cm ? ` <span class="aide">(${cm})</span>` : ""}${x.resultat ? `<div class="aide">${esc(x.resultat)}</div>` : ""}</li>`;
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
        ${e?.description ? `<p class="antic-desc">${esc(e.description)}</p>` : ""}
        ${e?.statut === "livree" ? commitsHTML(e.commits) : ""}</li>`;
    }).join("") + "</ul>";
  }

  // La date d'une carte livrée est celle de sa livraison ; une carte de suivi (audit) dit sa dernière mise à jour.
  function libelleDate(c) {
    if (c?.statut === "livree") return "Livrée le";
    return c?.derniereRevue || c?.dernierAudit ? "Mise à jour le" : "Proposée le";
  }

  function cadreHTML(c) {
    const maquette = c.page.includes("/maquettes-flux/");
    return `<div class="antic-frame-wrap">
      <p class="aide">${maquette ? "Maquette non fonctionnelle, chiffres d'exemple. " : ""}<a href="${esc(c.page)}" target="_blank" rel="noopener">Ouvrir dans un nouvel onglet</a></p>
      <iframe class="antic-frame" sandbox="allow-scripts" data-antic-cadre="${esc(c.page)}" title="${esc(c.titre)}"></iframe></div>`;
  }

  function carteHTML(c, estEpinglee, ouverte) {
    const [libelle, cls] = statut(c?.statut);
    const avecPage = pageValide(c?.page);
    const ouvrir = c?.libelleBouton || "Ouvrir la maquette";
    const onglet = c?.onglet ? ` <span class="aide">Onglet concerné : ${esc(c.onglet)}</span>` : "";
    return `<article class="antic-card${estEpinglee ? " is-pinned" : ""}" data-antic-id="${esc(c?.id)}">
      <div class="antic-main">
        <div class="antic-head"><span><span class="ds-badge ${cls}">${libelle}</span>${onglet}</span>
          <button type="button" class="ds-btn ds-btn--sm antic-pin${estEpinglee ? " is-on" : ""}" aria-pressed="${estEpinglee}" data-antic-epingle="${esc(c?.id)}"
            title="${estEpinglee ? "Désépingler : la carte retourne dans la liste" : "Épingler : la carte reste en haut de l'onglet"}">📌 ${estEpinglee ? "Épinglée" : "Épingler"}</button></div>
        <h3 class="antic-title">${esc(c?.titre)}</h3>
        ${revueHTML(c)}
        <p class="antic-desc">${esc(c?.description)}</p>
        ${elementsHTML(c?.elements)}
        ${liensHTML(c?.liens)}
        ${c?.statut === "livree" ? commitsHTML(c.commits) : ""}
        <div class="aide antic-pied">
          ${avecPage ? `<button type="button" class="ds-btn ds-btn--sm" aria-expanded="${ouverte}" data-antic-page="${esc(c.id)}">${esc(ouverte ? ouvrir.replace(/^Ouvrir/, "Fermer") : ouvrir)}</button>` : ""}
          <span>${libelleDate(c)} ${esc(dateFr(c?.date))}</span>${Number.isInteger(c?.featureId) ? ` <span>bilbao #${c.featureId}</span>` : ""}
        </div>
        ${avecPage && ouverte ? cadreHTML(c) : ""}
      </div>
    </article>`;
  }

  // Cartes épinglées en haut, puis barre (filtre par statut avec compteurs, tri) et la liste.
  // Les compteurs ne comptent que les cartes non épinglées, qui seules sont filtrées.
  function listeHTML(cards, etat) {
    const epingles = etat.epingles || {};
    const carte = (c, haut) => carteHTML(c, haut, etat.ouverte === c.id);
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
      return `<button type="button" class="ds-btn ds-btn--sm antic-chip${on ? " is-on" : ""}" aria-pressed="${on}" data-antic-filtre="${esc(k)}">${k === "toutes" ? "Toutes" : STATUTS[k][0]} (${n})</button>`;
    }).join("");
    const visibles = listees.filter((c) => filtre === "toutes" || c.statut === filtre).sort(tri);
    return `${hautes.length ? `<div class="antic-list antic-pinned">${hautes.map((c) => carte(c, true)).join("")}</div>` : ""}
      ${listees.length ? `<div class="antic-toolbar">
        <div class="antic-chips" role="group" aria-label="Filtrer par statut">${puces}</div>
        <label class="aide">Trier par
          <select class="ds-select" data-antic-tri>
            <option value="date"${etat.tri !== "ancien" ? " selected" : ""}>Les plus récentes</option>
            <option value="ancien"${etat.tri === "ancien" ? " selected" : ""}>Les plus anciennes</option>
          </select></label>
      </div>
      <p class="aide antic-remaining">Lecture seule : toledo est local et n'a qu'un utilisateur, donc ni votes ni propositions. Les cartes et les maquettes sont celles du dossier du projet.</p>
      <div class="antic-list">${visibles.map((c) => carte(c, false)).join("") || '<p class="aide">Aucune carte dans ce statut.</p>'}</div>` : ""}`;
  }

  return { esc, epinglee, pageValide, dateFr, revueHTML, liensHTML, elementsHTML, carteHTML, listeHTML };
})();

(() => {
  const CLE_EPINGLES = "toledo-antic-epingles";
  let cards = null;
  let epingles = {};
  try { const v = JSON.parse(localStorage.getItem(CLE_EPINGLES) || "{}"); epingles = v && typeof v === "object" && !Array.isArray(v) ? v : {}; } catch { epingles = {}; }
  const etat = { filtre: "toutes", tri: "date", epingles, ouverte: null };

  function rendre() {
    const boite = $("anticipation-liste");
    if (!boite || !cards) return;
    boite.innerHTML = cards.length ? AnticipationRendu.listeHTML(cards, etat) : '<p class="aide">Aucune idée en maquette pour l\'instant.</p>';
    remplirCadre(boite.querySelector("iframe[data-antic-cadre]"));
  }

  async function remplirCadre(cadre) {
    if (!cadre) return;
    const lire = async (url) => {
      const rep = await fetch(url, { cache: "no-store" });
      if (!rep.ok) throw new Error(`HTTP ${rep.status}`);
      return rep.text();
    };
    try {
      const url = new URL(cadre.dataset.anticCadre, location.href);
      let html = await lire(url);
      for (const [lien, href] of html.matchAll(/<link\b[^>]*rel="stylesheet"[^>]*href="([^"]+)"[^>]*>/gi)) {
        const css = await lire(new URL(href, url));
        html = html.replace(lien, () => `<style>${css}</style>`);
      }
      const theme = document.documentElement.dataset.theme;
      if (theme) html = html.replace(/<html\b/i, () => `<html data-theme="${AnticipationRendu.esc(theme)}"`);
      cadre.srcdoc = html.replace(/<head[^>]*>/i, (tete) => `${tete}<base href="${AnticipationRendu.esc(url.href)}">`);
    } catch (err) {
      cadre.srcdoc = `<p style="font:14px sans-serif;padding:16px;">Page indisponible : ${AnticipationRendu.esc(err.message)}</p>`;
    }
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
    const btn = e.target.closest("button[data-antic-epingle], button[data-antic-filtre], button[data-antic-page]");
    if (!btn || !cards) return;
    if (btn.dataset.anticFiltre) {
      etat.filtre = btn.dataset.anticFiltre;
    } else if (btn.dataset.anticPage) {
      etat.ouverte = etat.ouverte === btn.dataset.anticPage ? null : btn.dataset.anticPage;
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
