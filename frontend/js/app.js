// Frontend minimal — appelle uniquement l'API locale FastAPI (http://localhost:8000).
// Aucune logique métier ici : on délègue tout au backend.

const API_BASE = "http://localhost:8000/api";

const elStatutApi = document.getElementById("statut-api");
const elStatutOllama = document.getElementById("statut-ollama");
const elModele = document.getElementById("modele");
const elCheminPdf = document.getElementById("chemin-pdf");
const elLangueSource = document.getElementById("langue-source");
const elLangueCible = document.getElementById("langue-cible");
const elResultatAnalyse = document.getElementById("resultat-analyse");
const elContenuAnalyse = document.getElementById("contenu-analyse");

async function verifierStatut() {
  try {
    const reponse = await fetch(`${API_BASE}/health`);
    const data = await reponse.json();
    elStatutApi.textContent = "API backend : en ligne ✅";
    elStatutOllama.textContent =
      data.ollama_accessible === "oui"
        ? "Ollama : accessible ✅"
        : "Ollama : inaccessible ⚠️ (vérifie qu'il est lancé)";
  } catch (e) {
    elStatutApi.textContent = "API backend : hors ligne ❌ (lance le serveur FastAPI)";
  }
}

async function chargerModeles() {
  try {
    const reponse = await fetch(`${API_BASE}/modeles`);
    const data = await reponse.json();
    elModele.innerHTML = "";
    for (const nom of data.modeles) {
      const option = document.createElement("option");
      option.value = nom;
      option.textContent = nom;
      elModele.appendChild(option);
    }
    if (data.modeles.length === 0) {
      elModele.innerHTML = '<option value="">Aucun modèle trouvé</option>';
    }
  } catch (e) {
    elModele.innerHTML = '<option value="">Erreur de chargement</option>';
  }
}

document.getElementById("bouton-analyser").addEventListener("click", async () => {
  const chemin = elCheminPdf.value.trim();
  if (!chemin) {
    alert("Indique le chemin du PDF d'abord.");
    return;
  }

  elContenuAnalyse.textContent = "Analyse en cours...";
  elResultatAnalyse.hidden = false;

  try {
    const reponse = await fetch(`${API_BASE}/analyser`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        chemin_pdf: chemin,
        modele: elModele.value,
        langue_source: elLangueSource.value,
        langue_cible: elLangueCible.value,
      }),
    });

    if (!reponse.ok) {
      const erreur = await reponse.json();
      elContenuAnalyse.textContent = `Erreur : ${erreur.detail}`;
      return;
    }

    const data = await reponse.json();
    elContenuAnalyse.textContent = JSON.stringify(data, null, 2);
  } catch (e) {
    elContenuAnalyse.textContent = `Erreur de connexion à l'API : ${e}`;
  }
});

document.getElementById("bouton-traduire").addEventListener("click", () => {
  alert("La traduction complète sera branchée à l'API dans une prochaine itération.");
});

verifierStatut();
chargerModeles();
