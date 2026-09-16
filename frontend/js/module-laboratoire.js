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
  // La langue de synthèse ne concerne que les voix clonées (MeloTTS) ; Piper et
  // Kokoro déduisent la langue de la voix choisie.
  $("tts-langue-ligne").hidden = moteur.id !== "openvoice";
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
      body: JSON.stringify({ texte, moteur: $("tts-moteur").value, voix: $("tts-voix").value, langue: $("tts-langue").value }),
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
    const d = await apiPost("/analyser", { chemin_pdf: chemin, modele_ollama: $("modele").value || "llama3.1" }, API_TIMEOUT_LONG_MS);
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
    const d = await apiPost("/convert", { chemin_pdf: chemin, extracteur_pdf: $("extracteur-pdf").value }, API_TIMEOUT_LONG_MS);
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
    }), API_TIMEOUT_LONG_MS);
    $("bouton-reprendre").hidden = true;
    $("outil-resultat").innerHTML = "<p>▶ Traduction reprise — suivi dans « Nouveau document » ou dans la Bibliothèque une fois terminée.</p>";
  } catch (e) {
    $("outil-resultat").innerHTML = `<span class="erreur">Erreur : ${e.message}</span>`;
  }
});

// ── Segmentation automatique (prototype) ─────────────────────────────────────
// Deux passes Ollama, séquentielles, sur un document sans structure — peut
// prendre plusieurs minutes (une fenêtre de 12k caractères par appel).
// Chaque appel backend est maintenant borné à 90s SANS retry (voir
// SEGMENTATION_TIMEOUT_SECONDES, segmentation.py) : ce délai côté frontend
// doit rester au-dessus du pire cas (fenêtres + 1 consolidation), pas
// couvrir une attente potentiellement illimitée comme avant.
const API_TIMEOUT_SEGMENTATION_MS = 15 * 60 * 1000;

function _echapperHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

function _rendreListeSegments(titre, segments, avecCitation) {
  if (!segments.length) return `<p class="aide">${titre} : aucun.</p>`;
  const items = segments.map((s, i) => `
    <li>
      <strong>${i + 1}. ${_echapperHtml(s.titre)}</strong>
      <span class="aide"> — caractère ${s.position.toLocaleString()}</span>
      ${avecCitation ? `<br><span class="aide">« ${_echapperHtml(s.citation)} »</span>` : ""}
    </li>`).join("");
  return `<p><strong>${titre} (${segments.length})</strong></p><ul class="liste-segmentation" style="list-style:none;padding-left:0;">${items}</ul>`;
}

// Même mécanisme que « Nouveau document » (js/module-import.js) : le
// navigateur ne révèle jamais le chemin disque d'un fichier choisi via
// <input type="file"> — on envoie ses octets à /api/upload, qui écrit le
// fichier dans backend/uploads/<hash>/ et retourne le chemin absolu réel.
$("bouton-segmentation-parcourir").addEventListener("click", () => $("segmentation-fichier").click());

$("segmentation-fichier").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = ""; // permet de re-choisir le même fichier une 2e fois
  if (!file) return;
  const statut = $("segmentation-upload-statut");
  statut.textContent = `⏳ Envoi de ${file.name}…`;
  const form = new FormData();
  form.append("fichier", file);
  try {
    const rep = await fetch(`${API_BASE}/upload`, { method: "POST", body: form });
    const data = await rep.json().catch(() => ({}));
    if (!rep.ok) throw new Error(data.detail || `Erreur HTTP ${rep.status}`);
    $("segmentation-chemin").value = data.chemin;
    statut.textContent = `✅ ${file.name} envoyé.`;
    setTimeout(() => { statut.textContent = ""; }, 4000);
    estimerCibleSegmentation(data.chemin);
  } catch (err) {
    statut.textContent = `❌ ${file.name} : ${err.message}`;
  }
});

// Chemin saisi/collé à la main (mode avancé) : même estimation, déclenchée à
// la sortie du champ plutôt qu'à chaque frappe.
$("segmentation-chemin").addEventListener("change", () => {
  const chemin = $("segmentation-chemin").value.trim();
  if (chemin) estimerCibleSegmentation(chemin);
});

// ── T2 (15/9/2026) : « Chapitres visés » n'est plus un nombre à deviner à
// l'aveugle — une estimation RAPIDE (aucun appel Ollama, seulement la
// longueur du texte) préremplit le champ dès qu'un fichier est choisi.
// Explicitement une approximation, pas une analyse de contenu — voir
// segmentation.py, SEGMENTATION_CARACTERES_PAR_CHAPITRE_ESTIME.
async function estimerCibleSegmentation(chemin) {
  const aide = $("segmentation-cible-aide");
  if (aide) aide.textContent = "⏳ Estimation…";
  try {
    const corps = corpsSource(chemin, { extracteur_pdf: $("extracteur-pdf")?.value || "pymupdf4llm" });
    const d = await apiPost("/labo/segmentation/estimation", corps, API_TIMEOUT_LONG_MS);
    $("segmentation-cible").value = d.cible_suggeree;
    if (aide) {
      aide.textContent = `💡 ${d.cible_suggeree} suggéré pour ${d.nb_caracteres.toLocaleString()} caractères (estimation par longueur, pas une analyse de contenu — ajuste librement).`;
    }
  } catch (e) {
    if (aide) aide.textContent = `❌ Estimation impossible : ${e.message}`;
  }
}

// ── Recommandation de moteur — MESURÉE, pas supposée (deux passages réels sur
// le même document, 15/9/2026, voir CLAUDE.md du produit section
// « Comparaison qwen2.5 vs llama3.1 pour la segmentation ») :
// qwen2.5:latest est ~2x plus rapide (11-25s contre 44-56s) mais détecte
// nettement MOINS de candidats (5-6 contre 19-37) et laisse de larges pans du
// document (début, fin) sans aucun chapitre — llama3.1 est donc préféré ici,
// à l'inverse de sa rapidité qui aurait pu le faire choisir sans vérifier.
const SEGMENTATION_MODELE_RECOMMANDE = "llama3.1:latest";
// T1 (15/9/2026) : le sélecteur indique vitesse/couverture pour les deux
// moteurs déjà mesurés (voir tableau CLAUDE.md) — même patron que le select
// TTS (js/module-laboratoire.js, chargerMoteursTts) qui annote déjà ses
// options plutôt que de laisser un nom de modèle nu. Match sur le nom de
// base (avant le ':tag') : un futur modèle non mesuré reste affiché tel quel,
// jamais une caractérisation inventée.
const SEGMENTATION_MODELE_INFO = {
  "llama3.1": "plus lent, couverture complète — recommandé",
  "qwen2.5": "rapide, couverture partielle",
};
document.addEventListener("modeles-charges", (e) => {
  const select = $("segmentation-modele");
  const aide = $("segmentation-modele-aide");
  if (!select) return;
  for (const opt of select.options) {
    const base = opt.value.split(":")[0];
    if (SEGMENTATION_MODELE_INFO[base]) {
      opt.textContent = `${opt.value} — ${SEGMENTATION_MODELE_INFO[base]}`;
    }
  }
  if (e.detail.modeles.includes(SEGMENTATION_MODELE_RECOMMANDE)) {
    select.value = SEGMENTATION_MODELE_RECOMMANDE;
  }
  if (aide) {
    aide.textContent = "💡 llama3.1 recommandé pour cette tâche — plus lent que qwen2.5, mais couvre tout le document (qwen2.5 en a manqué de larges pans en test réel). Voir CLAUDE.md.";
  }
});

// Résultat du dernier test réussi, pour « Générer le .md » — pas de nouvel
// appel Ollama, on insère juste ce que l'utilisateur a déjà sous les yeux.
let dernierResultatSegmentation = null;

$("bouton-segmenter").addEventListener("click", async () => {
  const chemin = $("segmentation-chemin").value.trim();
  if (!chemin) { alert("Indique d'abord le chemin du fichier (.md ou .pdf), ou utilise Parcourir…"); return; }
  const modele = $("segmentation-modele").value || SEGMENTATION_MODELE_RECOMMANDE;
  const cible = parseInt($("segmentation-cible").value, 10) || 8;

  const zone = $("segmentation-resultat");
  const btn = $("bouton-segmenter");
  btn.disabled = true;
  btn.textContent = "⏳ Segmentation en cours (peut prendre plusieurs minutes)…";
  zone.innerHTML = "<em>Détection des candidats, fenêtre par fenêtre…</em>";
  $("bouton-segmentation-generer").hidden = true;
  $("segmentation-generer-resultat").innerHTML = "";
  dernierResultatSegmentation = null;

  try {
    const corps = corpsSource(chemin, { modele_ollama: modele, cible_max_chapitres: cible });
    const d = await apiPost("/labo/segmentation", corps, API_TIMEOUT_SEGMENTATION_MS);

    let html = `<p class="aide">${d.nb_caracteres.toLocaleString()} caractères, ${d.nb_fenetres} fenêtre(s) analysée(s), ${d.nb_candidats_bruts} candidat(s) brut(s).</p>`;
    if (d.consolidation_repli) {
      html += `<p class="aide">⚠ La consolidation sémantique n'a rien renvoyé d'exploitable — repli sur les ${cible} premiers candidats dans l'ordre chronologique (pas une sélection par importance).</p>`;
    }
    html += _rendreListeSegments("Chapitres proposés", d.chapitres, true);
    zone.innerHTML = html;

    if (d.chapitres.length) {
      dernierResultatSegmentation = { chemin, chapitres: d.chapitres };
      $("bouton-segmentation-generer").hidden = false;
    }
  } catch (e) {
    zone.innerHTML = `<span class="erreur">Erreur : ${e.message}</span>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "🧩 Tester la segmentation";
  }
});

$("bouton-segmentation-generer").addEventListener("click", async () => {
  if (!dernierResultatSegmentation) return;
  const { chemin, chapitres } = dernierResultatSegmentation;
  const zone = $("segmentation-generer-resultat");
  const btn = $("bouton-segmentation-generer");
  btn.disabled = true;
  btn.textContent = "⏳ Génération…";
  zone.innerHTML = "";
  try {
    const corps = corpsSource(chemin, { chapitres });
    const d = await apiPost("/labo/segmentation/generer", corps, API_TIMEOUT_LONG_MS);
    zone.innerHTML = `<p>✅ Fichier généré — ${d.nb_caracteres.toLocaleString()} caractères<br>Fichier : <code>${d.chemin_sortie}</code></p>`;
  } catch (e) {
    zone.innerHTML = `<span class="erreur">Erreur : ${e.message}</span>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "💾 Générer le .md avec ces chapitres";
  }
});

// ── Voix clonées : capture micro → clonage OpenVoice ─────────────────────────
// (fonction chargerVoixClonees globale : appelée par reconnecter() dans commun.js)

let contexteAudioEnr = null;
let noeudSourceEnr = null;
let noeudProcesseurEnr = null;
let fluxMicroEnr = null;
let morceauxPcmEnr = [];
let blobEnregistre = null;
let minuteurEnregistrement = null;

// Encode des échantillons PCM float32 en WAV 16 bits mono — le backend valide
// la durée avec le module wave (RIFF strict), et MediaRecorder ne produit que
// du webm/ogg compressé selon les navigateurs : on capture le PCM brut via
// Web Audio API et on encode le WAV nous-mêmes (aucune dépendance externe).
function encoderWav(morceaux, frequence) {
  const longueur = morceaux.reduce((n, m) => n + m.length, 0);
  const pcm = new Int16Array(longueur);
  let offset = 0;
  for (const morceau of morceaux) {
    for (let i = 0; i < morceau.length; i++) {
      const s = Math.max(-1, Math.min(1, morceau[i]));
      pcm[offset++] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
  }

  const tampon = new ArrayBuffer(44 + pcm.length * 2);
  const vue = new DataView(tampon);
  const ecrireChaine = (pos, str) => { for (let i = 0; i < str.length; i++) vue.setUint8(pos + i, str.charCodeAt(i)); };

  ecrireChaine(0, "RIFF");
  vue.setUint32(4, 36 + pcm.length * 2, true);
  ecrireChaine(8, "WAVE");
  ecrireChaine(12, "fmt ");
  vue.setUint32(16, 16, true);
  vue.setUint16(20, 1, true);         // PCM
  vue.setUint16(22, 1, true);         // mono
  vue.setUint32(24, frequence, true);
  vue.setUint32(28, frequence * 2, true);
  vue.setUint16(32, 2, true);
  vue.setUint16(34, 16, true);
  ecrireChaine(36, "data");
  vue.setUint32(40, pcm.length * 2, true);
  for (let i = 0; i < pcm.length; i++) vue.setInt16(44 + i * 2, pcm[i], true);

  return new Blob([tampon], { type: "audio/wav" });
}

async function chargerVoixClonees() {
  try {
    const data = await apiGet("/voix-clonees");
    const zone = $("liste-voix-clonees");
    if (!data.voix.length) {
      zone.innerHTML = '<p class="teaser-vide" id="voix-clonees-vide">Aucune voix clonée pour l\'instant.</p>';
      return;
    }
    zone.innerHTML = "";
    for (const v of data.voix) {
      const ligne = document.createElement("div");
      ligne.className = "ligne-voix-clonee";
      const libelleStatut = {
        en_attente: "⏳ En attente…",
        en_cours: "⏳ Traitement en cours…",
        termine: "✅ Prête",
        erreur: `❌ ${v.erreur || "Échec du traitement"}`,
      }[v.statut] || v.statut;
      ligne.innerHTML = `
        <span class="voix-nom">${v.nom}</span>
        <span class="aide">${libelleStatut}</span>
        <button class="bouton-lien" data-action="renommer" data-id="${v.id}">Renommer</button>
        <button class="bouton-lien" data-action="supprimer" data-id="${v.id}">Supprimer</button>
      `;
      zone.appendChild(ligne);
      if (v.statut === "en_attente" || v.statut === "en_cours") pollerStatutTraitement(v.id);
    }
  } catch { /* rechargé à la reconnexion */ }
}

$("liste-voix-clonees").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-action]");
  if (!btn) return;
  const id = btn.dataset.id;
  if (btn.dataset.action === "renommer") {
    const nom = prompt("Nouveau nom :");
    if (nom == null || !nom.trim()) return;
    try {
      await fetch(`${API_BASE}/voix-clonees/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nom: nom.trim() }),
      });
      await chargerVoixClonees();
      await chargerMoteursTts();
    } catch (err) {
      alert(`Impossible de renommer : ${err}`);
    }
  } else if (btn.dataset.action === "supprimer") {
    if (!confirm("Supprimer cette voix clonée ?")) return;
    try {
      await fetch(`${API_BASE}/voix-clonees/${id}`, { method: "DELETE" });
      await chargerVoixClonees();
      await chargerMoteursTts();
    } catch (err) {
      alert(`Impossible de supprimer : ${err}`);
    }
  }
});

async function pollerStatutTraitement(idVoix) {
  try {
    const etat = await apiGet(`/voix-clonees/statut?id_voix=${idVoix}`);
    if (!etat || etat.statut === "en_attente" || etat.statut === "en_cours") {
      setTimeout(() => pollerStatutTraitement(idVoix), 3000);
      return;
    }
    await chargerVoixClonees();
    if (etat.statut === "termine") await chargerMoteursTts();
  } catch {
    setTimeout(() => pollerStatutTraitement(idVoix), 5000);
  }
}

function reinitialiserZoneEnregistrement() {
  $("zone-enregistrement").hidden = true;
  $("bouton-demarrer-enr").hidden = false;
  $("bouton-arreter-enr").hidden = true;
  $("pastille-enregistrement").hidden = true;
  $("enr-minuteur").textContent = "";
  $("enr-relecture").hidden = true;
  $("enr-relecture").src = "";
  $("enr-validation").hidden = true;
  $("enr-nom").value = "";
  clearInterval(minuteurEnregistrement);
  blobEnregistre = null;

  if (contexteAudioEnr && contexteAudioEnr.state !== "closed") {
    noeudSourceEnr?.disconnect();
    noeudProcesseurEnr?.disconnect();
    fluxMicroEnr?.getTracks().forEach((t) => t.stop());
    contexteAudioEnr.close();
  }
}

$("bouton-creer-voix").addEventListener("click", () => {
  reinitialiserZoneEnregistrement();
  $("zone-enregistrement").hidden = false;
});

$("bouton-demarrer-enr").addEventListener("click", async () => {
  const elStatut = $("voix-statut");
  elStatut.textContent = "";
  try {
    fluxMicroEnr = await navigator.mediaDevices.getUserMedia({ audio: true });
    contexteAudioEnr = new (window.AudioContext || window.webkitAudioContext)();
    // AudioWorkletNode plutôt que ScriptProcessorNode (déprécié par le W3C) :
    // le traitement tourne sur le thread audio dédié, jamais sur le thread
    // principal. Le module doit être (ré)ajouté à chaque nouveau contexte —
    // l'enregistrement d'un processeur ne survit pas d'un AudioContext à l'autre.
    await contexteAudioEnr.audioWorklet.addModule("js/enregistreur-processor.js?v=1");
    noeudSourceEnr = contexteAudioEnr.createMediaStreamSource(fluxMicroEnr);
    noeudProcesseurEnr = new AudioWorkletNode(contexteAudioEnr, "enregistreur-pcm");
    morceauxPcmEnr = [];
    noeudProcesseurEnr.port.onmessage = (e) => {
      morceauxPcmEnr.push(e.data);
    };
    noeudSourceEnr.connect(noeudProcesseurEnr);
    // Connecté à destination pour que le graphe sollicite le nœud (sinon un
    // nœud non relié à la sortie n'est jamais traité) — sans écho : le
    // processeur n'écrit jamais dans ses sorties, qui restent silencieuses.
    noeudProcesseurEnr.connect(contexteAudioEnr.destination);

    $("bouton-demarrer-enr").hidden = true;
    $("bouton-arreter-enr").hidden = false;
    $("pastille-enregistrement").hidden = false;

    let secondes = 0;
    $("enr-minuteur").textContent = "0s";
    minuteurEnregistrement = setInterval(() => {
      secondes += 1;
      $("enr-minuteur").textContent = `${secondes}s`;
    }, 1000);
  } catch (e) {
    if (e.name === "NotAllowedError") {
      elStatut.textContent = "❌ Permission micro refusée — autorise l'accès au micro dans ton navigateur.";
    } else {
      elStatut.textContent = `❌ Micro indisponible : ${e.message}`;
    }
  }
});

$("bouton-arreter-enr").addEventListener("click", () => {
  clearInterval(minuteurEnregistrement);
  $("bouton-arreter-enr").hidden = true;
  $("pastille-enregistrement").hidden = true;

  noeudSourceEnr?.disconnect();
  noeudProcesseurEnr?.disconnect();
  fluxMicroEnr?.getTracks().forEach((t) => t.stop());
  const frequence = contexteAudioEnr ? contexteAudioEnr.sampleRate : 44100;
  contexteAudioEnr?.close();

  blobEnregistre = encoderWav(morceauxPcmEnr, frequence);
  $("enr-relecture").src = URL.createObjectURL(blobEnregistre);
  $("enr-relecture").hidden = false;
  $("enr-validation").hidden = false;
});

$("bouton-recommencer-voix").addEventListener("click", () => {
  reinitialiserZoneEnregistrement();
  $("zone-enregistrement").hidden = false;
});

$("bouton-annuler-voix").addEventListener("click", () => {
  reinitialiserZoneEnregistrement();
});

$("bouton-valider-voix").addEventListener("click", async () => {
  const nom = $("enr-nom").value.trim();
  const elStatut = $("voix-statut");
  if (!nom) { alert("Donne un nom à cette voix."); return; }
  if (!blobEnregistre) { elStatut.textContent = "❌ Aucun enregistrement à envoyer."; return; }

  const btn = $("bouton-valider-voix");
  btn.disabled = true;
  btn.textContent = "⏳ Envoi…";
  try {
    const formulaire = new FormData();
    formulaire.append("nom", nom);
    formulaire.append("fichier", blobEnregistre, "echantillon.wav");
    const rep = await fetch(`${API_BASE}/voix-clonees/capturer`, { method: "POST", body: formulaire });
    const data = await rep.json();
    if (!rep.ok) throw new Error(data.detail || `Erreur HTTP ${rep.status}`);
    reinitialiserZoneEnregistrement();
    elStatut.textContent = "✅ Échantillon envoyé — traitement en cours…";
    await chargerVoixClonees();
  } catch (e) {
    elStatut.textContent = `❌ ${e.message}`;
  } finally {
    btn.disabled = false;
    btn.textContent = "✅ Valider";
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

$("bouton-export-pdf").addEventListener("click", () => capturerInteret("export_pdf", $("export-statut")));

document.addEventListener("flags-charges", () => {
  $("carte-export").hidden = !featureFlags.teaser_export_pdf;
});
