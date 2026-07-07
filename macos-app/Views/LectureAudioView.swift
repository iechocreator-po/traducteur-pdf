import SwiftUI
import AVFoundation

/// Lecture audio (Text-to-Speech local) — mêmes moteurs que le web :
/// Piper (rapide) / Kokoro (qualité), choix par menu déroulant.
/// Extrait à écouter + génération audio d'un fichier Markdown complet.
struct LectureAudioView: View {
    /// Chemin du fichier source actif (déterminé par la section Document).
    let cheminSource: String

    @State private var moteurs: [MoteurTTS] = []
    @State private var moteurChoisi: String = ""
    @State private var voixChoisie: String = ""
    @State private var texteExtrait: String = ""
    @State private var statut: String? = nil
    @State private var enEcoute = false

    @State private var jobAudio: (jobId: String, source: String)? = nil
    @State private var pollTask: Task<Void, Never>? = nil
    @State private var lecteur: AVAudioPlayer? = nil

    private var moteurActuel: MoteurTTS? { moteurs.first { $0.id == moteurChoisi } }
    private var pret: Bool { (moteurActuel?.disponible ?? false) && !(moteurActuel?.voix.isEmpty ?? true) }

    var body: some View {
        GroupBox("Lecture audio (optionnel)") {
            VStack(alignment: .leading, spacing: 8) {
                Text("Text-to-Speech 100 % local — génère un fichier audio à partir d'un Markdown (traduit ou non). Le moteur et la voix se choisissent comme le modèle Ollama.")
                    .font(.caption)
                    .foregroundStyle(.secondary)

                HStack {
                    Picker("Moteur :", selection: $moteurChoisi) {
                        ForEach(moteurs) { m in
                            Text(m.disponible ? m.nom : "\(m.nom) (indisponible)")
                                .tag(m.id)
                        }
                    }
                    .disabled(moteurs.isEmpty)

                    Picker("Voix :", selection: $voixChoisie) {
                        ForEach(moteurActuel?.voix ?? [], id: \.self) { v in
                            Text(v).tag(v)
                        }
                    }
                    .disabled(!pret)
                }
                .onChange(of: moteurChoisi) { _, _ in
                    voixChoisie = moteurActuel?.voix.first ?? ""
                }

                if let aide = moteurActuel?.aide {
                    Text(aide)
                        .font(.caption)
                        .foregroundStyle(.orange)
                }

                Text("Extrait à écouter (test de la voix) :")
                    .font(.caption)
                TextEditor(text: $texteExtrait)
                    .font(.system(size: 12))
                    .frame(height: 44)
                    .overlay(
                        RoundedRectangle(cornerRadius: 6)
                            .stroke(Color(nsColor: .separatorColor), lineWidth: 1)
                    )

                HStack {
                    Button(enEcoute ? "⏳ Synthèse…" : "▶ Écouter l'extrait") { ecouter() }
                        .buttonStyle(.bordered)
                        .disabled(!pret || enEcoute)

                    Button("🔊 Générer l'audio du fichier") { generer() }
                        .buttonStyle(.bordered)
                        .disabled(!pret || jobAudio != nil)

                    if jobAudio != nil {
                        Button("✕ Annuler", role: .destructive) { annuler() }
                            .buttonStyle(.bordered)
                    }
                }

                if let statut {
                    Text(statut)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.top, 4)
        }
        .task { await charger() }
        .onDisappear { pollTask?.cancel() }
    }

    private func charger() async {
        guard let liste = try? await APIService.shared.moteursTts() else { return }
        moteurs = liste
        if let premierDispo = liste.first(where: { $0.disponible }) {
            moteurChoisi = premierDispo.id
            voixChoisie = premierDispo.voix.first ?? ""
        }
    }

    private func ecouter() {
        let texte = texteExtrait.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !texte.isEmpty else { statut = "Colle un court texte à écouter d'abord."; return }
        enEcoute = true
        statut = nil
        Task {
            do {
                let wav = try await APIService.shared.ecouterExtrait(
                    texte: texte, moteur: moteurChoisi, voix: voixChoisie)
                lecteur = try AVAudioPlayer(data: wav)
                lecteur?.play()
            } catch {
                statut = "❌ \(error.localizedDescription)"
            }
            enEcoute = false
        }
    }

    private func generer() {
        let chemin = cheminSource.trimmingCharacters(in: .whitespaces)
        guard !chemin.isEmpty else { statut = "Indique d'abord un fichier dans la section Document."; return }
        guard chemin.lowercased().hasSuffix(".md") else {
            statut = "La génération audio attend un fichier Markdown (.md). Convertis d'abord le PDF."
            return
        }
        Task {
            do {
                let rep = try await APIService.shared.genererAudio(
                    cheminMd: chemin, moteur: moteurChoisi, voix: voixChoisie)
                jobAudio = (jobId: rep.jobId, source: chemin)
                demarrerPolling()
            } catch {
                statut = "❌ \(error.localizedDescription)"
            }
        }
    }

    private func annuler() {
        guard let job = jobAudio else { return }
        Task { try? await APIService.shared.annulerJob(jobId: job.jobId) }
    }

    private func demarrerPolling() {
        pollTask?.cancel()
        pollTask = Task {
            while !Task.isCancelled, let job = jobAudio {
                if let etat = try? await APIService.shared.statutAudio(cheminMd: job.source) {
                    let pct = etat.totalSections > 0
                        ? Int(Double(etat.sectionsCompletees) / Double(etat.totalSections) * 100)
                        : 0
                    switch etat.statut {
                    case "en_attente":
                        statut = "⏳ En file d'attente…"
                    case "en_cours":
                        statut = "🔊 Génération audio — \(etat.sectionsCompletees)/\(etat.totalSections) sections (\(pct)%)"
                    case "termine":
                        statut = "✅ Audio généré — \(etat.cheminSortie)"
                        jobAudio = nil
                    case "annule":
                        statut = "✕ Génération annulée — \(etat.sectionsCompletees)/\(etat.totalSections) sections"
                        jobAudio = nil
                    case "erreur":
                        statut = "❌ Erreur : \(etat.erreur ?? "inconnue")"
                        jobAudio = nil
                    default:
                        break
                    }
                }
                if jobAudio == nil { break }
                try? await Task.sleep(for: .seconds(2))
            }
        }
    }
}
