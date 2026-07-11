// Socle partagé de l'interface Workflow — appelle uniquement l'API locale FastAPI.
// Aucune logique métier ici : tout est délégué au backend.

const API_BASE = "http://localhost:8000/api";
const LAUNCHER_BASE = "http://localhost:5501";

const $ = (id) => document.getElementById(id);

// ── Utilitaires ──────────────────────────────────────────────────────────────

function formaterDuree(secondes) {
  if (secondes == null || secondes < 0) return "—";
  const s = Math.round(secondes);
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}

function nomFichier(chemin) {
  return chemin.split("/").pop();
}

function estMarkdown(chemin) {
  const c = chemin.trim().toLowerCase();
  return c.endsWith(".md") || c.endsWith(".markdown");
}

function corpsSource(chemin, extra = {}) {
  return estMarkdown(chemin)
    ? { chemin_md: chemin, ...extra }
    : { chemin_pdf: chemin, ...extra };
}

async function apiPost(route, body) {
  const rep = await fetch(`${API_BASE}${route}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await rep.json().catch(() => ({}));
  if (!rep.ok) throw new Error(data.detail || `Erreur HTTP ${rep.status}`);
  return data;
}

async function apiGet(route) {
  const rep = await fetch(`${API_BASE}${route}`);
  if (!rep.ok) throw new Error(`Erreur HTTP ${rep.status}`);
  return rep.json();
}

// ── Navigation entre modules ─────────────────────────────────────────────────
// L'événement "module-affiche" permet à chaque module de se rafraîchir à l'affichage.

function activerModule(nom) {
  for (const m of ["import", "bibliotheque", "laboratoire"]) {
    $(`module-${m}`).hidden = m !== nom;
  }
  document.querySelectorAll(".modules-nav .onglet-module").forEach((b) => {
    b.classList.toggle("is-active", b.dataset.module === nom);
    b.setAttribute("aria-selected", b.dataset.module === nom ? "true" : "false");
  });
  localStorage.setItem("module", nom);
  document.dispatchEvent(new CustomEvent("module-affiche", { detail: nom }));
}

document.querySelectorAll(".modules-nav .onglet-module").forEach((b) => {
  b.addEventListener("click", () => activerModule(b.dataset.module));
});

// ── Thème (Auto / Clair / Sombre) ────────────────────────────────────────────

function appliquerTheme(choix) {
  if (choix === "light" || choix === "dark") {
    document.documentElement.setAttribute("data-theme", choix);
  } else {
    document.documentElement.removeAttribute("data-theme");
    choix = "auto";
  }
  localStorage.setItem("theme", choix);
  document.querySelectorAll("#theme-switch button").forEach((b) => {
    b.classList.toggle("is-active", b.dataset.themeChoice === choix);
  });
}

document.querySelectorAll("#theme-switch button").forEach((b) => {
  b.addEventListener("click", () => appliquerTheme(b.dataset.themeChoice));
});

// ── Mode avancé ──────────────────────────────────────────────────────────────
// Révèle les réglages techniques (moteur de conversion, modèle IA) dans l'import.

function appliquerModeAvance(actif) {
  document.querySelectorAll("[data-avance]").forEach((el) => { el.hidden = !actif; });
  $("switch-avance").classList.toggle("is-on", actif);
  $("switch-avance").setAttribute("aria-checked", actif ? "true" : "false");
  localStorage.setItem("modeAvance", actif ? "1" : "0");
}

$("switch-avance").addEventListener("click", () => {
  appliquerModeAvance(localStorage.getItem("modeAvance") !== "1");
});

// ── Santé backend / Ollama ───────────────────────────────────────────────────

let timerReconnexionAuto = null;

function majDot(dot, ok) {
  dot.classList.toggle("dot-ok", ok === true);
  dot.classList.toggle("dot-erreur", ok === false);
}

async function verifierStatut() {
  let backendEnLigne = false;
  let ollamaOk = false;
  try {
    const data = await apiGet("/health");
    backendEnLigne = true;
    ollamaOk = data.ollama_accessible === "oui";
  } catch { /* backend hors ligne */ }

  majDot($("dot-api"), backendEnLigne);
  majDot($("dot-ollama"), backendEnLigne ? ollamaOk : false);
  $("dot-api").title = backendEnLigne ? "Backend en ligne" : "Backend hors ligne";
  $("dot-ollama").title = ollamaOk ? "Ollama accessible" : "Ollama inaccessible";

  // Reflet dans le Laboratoire
  $("statut-api").textContent = backendEnLigne ? "En ligne ✅" : "Hors ligne ❌";
  $("statut-api").className = `statut-badge ${backendEnLigne ? "badge-ok" : "badge-erreur"}`;
  $("statut-ollama").textContent = !backendEnLigne ? "" : ollamaOk
    ? "Accessible ✅"
    : "Inaccessible ⚠️ (vérifie qu'il est lancé)";

  clearTimeout(timerReconnexionAuto);
  if (!backendEnLigne || !ollamaOk) {
    timerReconnexionAuto = setTimeout(reconnecter, 30000);
  }
  return { backendEnLigne, ollamaOk };
}

async function reconnecter() {
  const { backendEnLigne } = await verifierStatut();
  if (backendEnLigne) {
    await Promise.all([chargerModeles(), chargerExtracteurs(), chargerGlossaire(), chargerMoteursTts(), chargerFlags()]);
    document.dispatchEvent(new CustomEvent("backend-connecte"));
  }
}

async function exigerSante() {
  const sante = await verifierStatut();
  if (!sante.backendEnLigne) {
    alert("Le backend est hors ligne. Lance-le depuis le Laboratoire, puis réessaie.");
    return false;
  }
  if (!sante.ollamaOk) {
    alert("Ollama est inaccessible. Vérifie qu'il est lancé, puis réessaie.");
    return false;
  }
  return true;
}

// ── Chargement des listes (modèles, extracteurs, flags) ──────────────────────

let featureFlags = {};

async function chargerModeles() {
  try {
    const data = await apiGet("/modeles");
    $("modele").innerHTML = "";
    for (const nom of data.modeles) {
      const opt = document.createElement("option");
      opt.value = nom;
      opt.textContent = nom;
      $("modele").appendChild(opt);
    }
    if (data.modeles.length === 0) {
      $("modele").innerHTML = '<option value="">Aucun modèle trouvé</option>';
    }
  } catch {
    $("modele").innerHTML = '<option value="">Erreur de chargement</option>';
  }
}

async function chargerExtracteurs() {
  try {
    const data = await apiGet("/config/extracteurs");
    $("extracteur-pdf").innerHTML = "";
    for (const ext of data.extracteurs) {
      const opt = document.createElement("option");
      opt.value = ext.id;
      opt.textContent = ext.disponible ? ext.nom : `${ext.nom} (bientôt disponible)`;
      opt.disabled = !ext.disponible;
      if (ext.id === data.defaut) opt.selected = true;
      $("extracteur-pdf").appendChild(opt);
    }
  } catch {
    $("extracteur-pdf").innerHTML = '<option value="pymupdf4llm">PyMuPDF4LLM</option>';
  }
}

async function chargerFlags() {
  try {
    featureFlags = await apiGet("/feature-flags");
  } catch {
    featureFlags = {};
  }
  document.dispatchEvent(new CustomEvent("flags-charges"));
}

// ── Init ─────────────────────────────────────────────────────────────────────
// chargerGlossaire / chargerMoteursTts sont définis dans module-laboratoire.js
// (chargés avant l'appel différé ci-dessous).

window.addEventListener("DOMContentLoaded", () => {
  appliquerTheme(localStorage.getItem("theme") || "auto");
  appliquerModeAvance(localStorage.getItem("modeAvance") === "1");
  activerModule(localStorage.getItem("module") || "import");
  reconnecter();
});
