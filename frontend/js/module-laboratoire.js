// Module C — « Laboratoire » : configuration technique isolée du flux principal.
// Glossaire, TTS (moteur/voix/extrait), outils document, launcher backend,
// et teasers des fonctionnalités futures avec capture d'intérêt.

// ── Glossaire ────────────────────────────────────────────────────────────────
// (fonction globale : appelée par reconnecter() dans commun.js)

async function chargerGlossaire() {
  try {
    const data = await apiGet("/glossaire");
    $("glossaire-termes").value = data.termes.join("\n");
  } catch { /* rechargé à la reconnexion */ }
}

$("bouton-sauver-glossaire").addEventListener("click", async () => {
  const termes = $("glossaire-termes").value.split("\n").map(t => t.trim()).filter(Boolean);
  try {
    const rep = await fetch(`${API_BASE}/glossaire`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ termes }),
    });
    const data = await rep.json();
    $("glossaire-termes").value = data.termes.join("\n");
    $("glossaire-statut").textContent = `✅ ${data.termes.length} terme(s) enregistré(s)`;
    setTimeout(() => { $("glossaire-statut").textContent = ""; }, 4000);
  } catch {
    $("glossaire-statut").textContent = "❌ Sauvegarde impossible (backend hors ligne ?)";
  }
});

// ── TTS : moteurs, voix, extrait ─────────────────────────────────────────────
// (fonction globale : appelée par reconnecter() dans commun.js ; les selects
//  moteur/voix sont aussi utilisés par la barre audio de la Bibliothèque)

let ttsMoteurs = [];
let audioExtrait = null;

async function chargerMoteursTts() {
  try {
    const data = await apiGet("/tts/moteurs");
    ttsMoteurs = data.moteurs;
    $("tts-moteur").innerHTML = "";
    for (const m of ttsMoteurs) {
      const opt = document.createElement("option");
      opt.value = m.id;
      opt.textContent = m.disponible ? m.nom : `${m.nom} (indisponible)`;
      opt.disabled = !m.disponible;
      $("tts-moteur").appendChild(opt);
    }
    const premierDispo = ttsMoteurs.find(m => m.disponible);
    if (premierDispo) $("tts-moteur").value = premierDispo.id;
    majVoixTts();
  } catch {
    $("tts-moteur").innerHTML = '<option value="">Erreur de chargement</option>';
  }
}

function majVoixTts() {
  const moteur = ttsMoteurs.find(m => m.id === $("tts-moteur").value);
  $("tts-voix").innerHTML = "";
  if (!moteur) return;
  for (const v of moteur.voix) {
    const opt = document.createElement("option");
    opt.value = v;
    opt.textContent = v;
    $("tts-voix").appendChild(opt);
  }
  $("tts-aide").textContent = moteur.aide || "";
  $("bouton-ecouter").disabled = !(moteur.disponible && moteur.voix.length > 0);
}

$("tts-moteur").addEventListener("change", majVoixTts);

$("bouton-ecouter").addEventListener("click", async () => {
  const texte = $("tts-extrait").value.trim();
  if (!texte) { alert("Colle un court texte à écouter d'abord."); return; }
  if (audioExtrait) { audioExtrait.pause(); audioExtrait = null; }
  const btn = $("bouton-ecouter");
  btn.disabled = true;
  btn.textContent = "⏳ Synthèse…";
  try {
    const rep = await fetch(`${API_BASE}/tts/extrait`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ texte, moteur: $("tts-moteur").value, voix: $("tts-voix").value }),
    });
    if (!rep.ok) {
      const data = await rep.json();
      alert(`Erreur : ${data.detail}`);
      return;
    }
    const blob = await rep.blob();
    audioExtrait = new Audio(URL.createObjectURL(blob));
    audioExtrait.play();
  } catch (e) {
    alert(`Impossible de générer l'extrait : ${e}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "▶ Écouter l'extrait";
  }
});

// ── Launcher backend ─────────────────────────────────────────────────────────

async function majBoutonLauncher() {
  const btn = $("bouton-lancer-backend");
  try {
    const rep = await fetch(`${LAUNCHER_BASE}/status`);
    const data = await rep.json();
    btn.disabled = false;
    btn.textContent = data.en_cours ? "Arrêter" : "Lancer";
    btn.classList.toggle("launcher-stop", data.en_cours);
  } catch {
    btn.textContent = "Launcher inactif";
    btn.disabled = true;
    btn.title = "Lance d'abord : python3 launcher.py";
    btn.classList.remove("launcher-stop");
  }
}

$("bouton-lancer-backend").addEventListener("click", async () => {
  const btn = $("bouton-lancer-backend");
  btn.disabled = true;
  try {
    const rep = await fetch(`${LAUNCHER_BASE}/status`);
    const data = await rep.json();
    await fetch(`${LAUNCHER_BASE}${data.en_cours ? "/stop" : "/start"}`, { method: "POST" });
    setTimeout(() => { reconnecter(); majBoutonLauncher(); }, 2000);
  } catch {
    btn.disabled = false;
  }
});

$("bouton-reconnecter").addEventListener("click", async () => {
  const btn = $("bouton-reconnecter");
  btn.disabled = true;
  btn.textContent = "🔄 Vérification…";
  try {
    await reconnecter();
    await majBoutonLauncher();
  } finally {
    btn.disabled = false;
    btn.textContent = "🔄 Reconnecter";
  }
});

document.addEventListener("backend-connecte", majBoutonLauncher);
window.addEventListener("DOMContentLoaded", majBoutonLauncher);

// ── Outils document (analyse, conversion, reprise) ───────────────────────────

function cheminOutil() {
  const chemin = $("outil-chemin").value.trim();
  if (!chemin) alert("Indique d'abord le chemin du fichier.");
  return chemin;
}

$("bouton-analyser").addEventListener("click", async () => {
  const chemin = cheminOutil();
  if (!chemin) return;
  if (estMarkdown(chemin)) { alert("L'analyse préliminaire ne concerne que les PDF."); return; }
  const zone = $("outil-resultat");
  zone.innerHTML = "<em>Analyse en cours…</em>";
  try {
    const d = await apiPost("/analyser", { chemin_pdf: chemin, modele_ollama: $("modele").value || "llama3.1" });
    zone.innerHTML = `
      <table class="tableau-analyse">
        <tr><th>Pages analysées</th><td>${d.nb_pages_analysees}</td></tr>
        <tr><th>Texte extractible</th><td>${d.texte_extractible ? "✅ Oui" : "❌ Non"}</td></tr>
        <tr><th>Langue détectée</th><td>${d.langue_detectee || "—"}</td></tr>
        <tr><th>Chapitres</th><td>${d.nb_chapitres}</td></tr>
        <tr><th>Sections (chunks)</th><td>${d.estimation_nb_chunks}</td></tr>
        <tr><th>Durée estimée</th><td>⏱ ~${formaterDuree(d.estimation_temps_secondes)}</td></tr>
        <tr><th>Recommandation</th><td>${d.recommandation}</td></tr>
      </table>`;
  } catch (e) {
    zone.innerHTML = `<span class="erreur">Erreur : ${e.message}</span>`;
  }
});

$("bouton-convertir").addEventListener("click", async () => {
  const chemin = cheminOutil();
  if (!chemin) return;
  if (estMarkdown(chemin)) { alert("Ce fichier est déjà en Markdown."); return; }
  const zone = $("outil-resultat");
  zone.innerHTML = "<em>Conversion en cours…</em>";
  try {
    const d = await apiPost("/convert", { chemin_pdf: chemin, extracteur_pdf: $("extracteur-pdf").value });
    zone.innerHTML = `<p>✅ Conversion terminée — ${d.nb_caracteres.toLocaleString()} caractères<br>Fichier : <code>${d.chemin_sortie}</code></p>`;
  } catch (e) {
    zone.innerHTML = `<span class="erreur">Erreur : ${e.message}</span>`;
  }
});

$("bouton-verifier-reprise").addEventListener("click", async () => {
  const chemin = cheminOutil();
  if (!chemin) return;
  const zone = $("outil-resultat");
  try {
    const etat = await apiPost("/check-resume", corpsSource(chemin));
    if (etat && etat.derniere_section_completee > 0 && etat.statut !== "termine") {
      $("reprise-progression").textContent = `section ${etat.derniere_section_completee}/${etat.total_sections}`;
      $("bouton-reprendre").hidden = false;
      zone.innerHTML = `<p>⏸ Job interrompu trouvé — ${etat.derniere_section_completee}/${etat.total_sections} sections déjà traduites.</p>`;
    } else {
      $("bouton-reprendre").hidden = true;
      zone.innerHTML = "<p>Aucun job interrompu pour ce fichier.</p>";
    }
    const erreurs = [...(etat?.erreurs || []), ...(etat?.avertissements || [])];
    $("section-erreurs").hidden = erreurs.length === 0;
    $("contenu-erreurs").textContent = erreurs.join("\n");
  } catch (e) {
    zone.innerHTML = `<span class="erreur">Erreur : ${e.message}</span>`;
  }
});

$("bouton-reprendre").addEventListener("click", async () => {
  const chemin = cheminOutil();
  if (!chemin) return;
  if (!(await exigerSante())) return;
  try {
    await apiPost("/translate", corpsSource(chemin, {
      langue_source: $("langue-source").value,
      langue_cible: $("langue-cible").value,
      modele_ollama: $("modele").value,
      extracteur_pdf: $("extracteur-pdf").value,
      resume: true,
    }));
    $("bouton-reprendre").hidden = true;
    $("outil-resultat").innerHTML = "<p>▶ Traduction reprise — suivi dans « Nouveau document » ou dans la Bibliothèque une fois terminée.</p>";
  } catch (e) {
    $("outil-resultat").innerHTML = `<span class="erreur">Erreur : ${e.message}</span>`;
  }
});

// ── Teasers : fonctionnalités en développement (capture d'intérêt) ───────────

async function capturerInteret(fonctionnalite, elStatut) {
  const ok = confirm(
    "La fonctionnalité est en développement.\n\n" +
    "Veux-tu nous partager ton intérêt pour cette fonctionnalité ?"
  );
  if (!ok) return;
  const email = prompt("Ton adresse email :");
  if (email == null) return;
  try {
    await apiPost("/interet", { fonctionnalite, email: email.trim() });
    elStatut.textContent = "✅ Merci ! Ton intérêt a été enregistré.";
    setTimeout(() => { elStatut.textContent = ""; }, 5000);
  } catch (e) {
    elStatut.textContent = `❌ ${e.message}`;
  }
}

$("bouton-creer-voix").addEventListener("click", () => capturerInteret("voix_personnalisees", $("voix-statut")));
$("bouton-export-pdf").addEventListener("click", () => capturerInteret("export_pdf", $("export-statut")));

document.addEventListener("flags-charges", () => {
  $("carte-voix").hidden = !featureFlags.teaser_voix_personnalisees;
  $("carte-export").hidden = !featureFlags.teaser_export_pdf;
});
