import SwiftUI
import AVFoundation

// ============================================================================
// Module C — « Laboratoire » : configuration technique isolée du flux principal.
// État système, glossaire, TTS (moteur/voix partagés avec la Bibliothèque via
// @AppStorage), outils document (analyse, conversion, reprise) et teasers des
// fonctionnalités futures avec capture d'intérêt (POST /api/interet).
// ============================================================================

struct LaboratoireModuleView: View {
    @EnvironmentObject private var env: AppEnvironment

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Laboratoire")
                        .font(.title2.bold())
                    Text("Configuration technique — ces réglages s'appliquent en arrière-plan, sans encombrer le flux principal.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                etatSysteme
                GlossaireView()
                TTSConfigView()
                OutilsDocumentView()

                if env.flags["teaser_voix_personnalisees"] == true {
                    TeaserView(
                        titre: "Voix personnalisées",
                        description: "Voix clonées disponibles pour la lecture audio.",
                        bouton: "＋ Créer une voix",
                        fonctionnalite: "voix_personnalisees"
                    )
                }
                if env.flags["teaser_export_pdf"] == true {
                    TeaserView(
                        titre: "Export PDF",
                        description: "Exporter un document traduit en PDF mis en page.",
                        bouton: "📄 Exporter en PDF",
                        fonctionnalite: "export_pdf"
                    )
                }
            }
            .padding(24)
            .frame(maxWidth: 680)
            .frame(maxWidth: .infinity)
        }
    }

    private var etatSysteme: some View {
        GroupBox("État du système") {
            VStack(spacing: 8) {
                ligne(label: "Backend FastAPI (lancé par l'app)", ok: env.apiEnLigne)
                Divider()
                HStack {
                    ligne(label: "Ollama", ok: env.ollamaOk)
                    Spacer()
                    Button("🔄 Reconnecter") {
                        Task { await env.rafraichir() }
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                }
            }
            .padding(.top, 4)
        }
    }

    private func ligne(label: String, ok: Bool?) -> some View {
        HStack(spacing: 8) {
            Circle()
                .fill(ok == true ? DS.green : ok == false ? DS.red : DS.text3)
                .frame(width: 10, height: 10)
            Text(label)
                .font(.subheadline)
            Text(ok == true ? "✓" : ok == false ? "✗" : "…")
                .font(.subheadline)
                .foregroundStyle(.secondary)
            Spacer()
        }
    }
}

// MARK: - TTS (moteur / voix / extrait)

/// Choix du moteur et de la voix TTS, partagés avec la barre audio de la
/// Bibliothèque via @AppStorage. Extrait de test lu avec AVAudioPlayer.
struct TTSConfigView: View {
    @AppStorage("ttsMoteur") private var moteurChoisi: String = ""
    @AppStorage("ttsVoix") private var voixChoisie: String = ""
    @State private var moteurs: [MoteurTTS] = []
    @State private var texteExtrait: String = ""
    @State private var statut: String? = nil
    @State private var enEcoute = false
    @State private var lecteur: AVAudioPlayer? = nil

    private var moteurActuel: MoteurTTS? { moteurs.first { $0.id == moteurChoisi } }
    private var pret: Bool { (moteurActuel?.disponible ?? false) && !(moteurActuel?.voix.isEmpty ?? true) }

    var body: some View {
        GroupBox("Lecture audio (TTS local)") {
            VStack(alignment: .leading, spacing: 8) {
                Text("Le moteur et la voix choisis ici sont utilisés par la barre audio de la Bibliothèque.")
                    .font(.caption)
                    .foregroundStyle(.secondary)

                HStack {
                    Picker("Moteur :", selection: $moteurChoisi) {
                        ForEach(moteurs) { m in
                            Text(m.disponible ? m.nom : "\(m.nom) (indisponible)").tag(m.id)
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
                    if !(moteurActuel?.voix.contains(voixChoisie) ?? false) {
                        voixChoisie = moteurActuel?.voix.first ?? ""
                    }
                }

                if let aide = moteurActuel?.aide {
                    Text(aide)
                        .font(.caption)
                        .foregroundStyle(DS.amber)
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
                    if let statut {
                        Text(statut)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.top, 4)
        }
        .task { await charger() }
    }

    private func charger() async {
        guard let liste = try? await APIService.shared.moteursTts() else { return }
        moteurs = liste
        // Conserve le choix mémorisé s'il est toujours valide, sinon premier disponible
        if !liste.contains(where: { $0.id == moteurChoisi && $0.disponible }) {
            moteurChoisi = liste.first(where: { $0.disponible })?.id ?? ""
        }
        if !(moteurActuel?.voix.contains(voixChoisie) ?? false) {
            voixChoisie = moteurActuel?.voix.first ?? ""
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
}

// MARK: - Outils document

/// Analyse préliminaire, conversion Markdown et reprise d'un job interrompu.
struct OutilsDocumentView: View {
    @EnvironmentObject private var env: AppEnvironment
    @State private var chemin: String = ""
    @State private var resultat: String? = nil
    @State private var erreurs: [String] = []
    @State private var repriseDisponible: String? = nil
    @State private var enCours = false

    var body: some View {
        GroupBox("Outils document") {
            VStack(alignment: .leading, spacing: 8) {
                Text("Analyse, conversion Markdown et reprise d'un job interrompu — pour un fichier donné.")
                    .font(.caption)
                    .foregroundStyle(.secondary)

                TextField("/Users/…/document.pdf ou .md", text: $chemin)
                    .textFieldStyle(.roundedBorder)

                HStack {
                    Button("🔍 Analyser") { analyser() }
                        .buttonStyle(.bordered)
                        .disabled(chemin.isEmpty || estMarkdown(chemin) || enCours)
                    Button("📄 Convertir en Markdown") { convertir() }
                        .buttonStyle(.bordered)
                        .disabled(chemin.isEmpty || estMarkdown(chemin) || enCours)
                    Button("⏯ Vérifier la reprise") { verifierReprise() }
                        .buttonStyle(.bordered)
                        .disabled(chemin.isEmpty || enCours)
                    if let info = repriseDisponible {
                        Button("⏯ Reprendre (\(info))") { reprendre() }
                            .buttonStyle(.borderedProminent)
                    }
                }

                if let resultat {
                    Text(resultat)
                        .font(.system(size: 12))
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }

                if !erreurs.isEmpty {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("⚠ Journal d'erreurs")
                            .font(.caption.bold())
                            .foregroundStyle(DS.amber)
                        Text(erreurs.joined(separator: "\n"))
                            .font(.system(size: 11))
                            .foregroundStyle(.secondary)
                            .textSelection(.enabled)
                    }
                    .padding(8)
                    .background(DS.amber.opacity(0.1), in: RoundedRectangle(cornerRadius: DS.radiusSm))
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.top, 4)
        }
    }

    private var cheminNet: String { chemin.trimmingCharacters(in: .whitespaces) }

    private func analyser() {
        enCours = true
        resultat = "Analyse en cours…"
        Task {
            defer { enCours = false }
            do {
                let r = try await APIService.shared.analyser(
                    cheminPdf: cheminNet, modele: env.modeleChoisi,
                    langueSource: env.langueSource, langueCible: env.langueCible)
                resultat = """
                    Pages analysées : \(r.nbPagesAnalysees)
                    Texte extractible : \(r.texteExtractible ? "oui" : "non")
                    Langue détectée : \(r.langueDetectee ?? "—")
                    Chapitres : \(r.nbChapitres)
                    Sections (chunks) : \(r.estimationNbChunks)
                    Durée estimée : ~\(formaterDuree(r.estimationTempsSecondes))
                    Recommandation : \(r.recommandation)
                    """
            } catch {
                resultat = "❌ \(error.localizedDescription)"
            }
        }
    }

    private func convertir() {
        enCours = true
        resultat = "Conversion en cours…"
        Task {
            defer { enCours = false }
            do {
                let r = try await APIService.shared.convertir(cheminPdf: cheminNet, extracteur: env.extracteurChoisi)
                resultat = "✅ Conversion terminée — \(r.nbCaracteres.formatted()) caractères\nFichier : \(r.cheminSortie)"
            } catch {
                resultat = "❌ \(error.localizedDescription)"
            }
        }
    }

    private func verifierReprise() {
        enCours = true
        Task {
            defer { enCours = false }
            let etat = estMarkdown(cheminNet)
                ? try? await APIService.shared.checkResume(cheminMd: cheminNet)
                : try? await APIService.shared.checkResume(cheminPdf: cheminNet)
            if let etat, etat.derniereSectionCompletee > 0, etat.statut != "termine" {
                repriseDisponible = "section \(etat.derniereSectionCompletee)/\(etat.totalSections)"
                resultat = "⏸ Job interrompu trouvé — \(etat.derniereSectionCompletee)/\(etat.totalSections) sections déjà traduites."
            } else {
                repriseDisponible = nil
                resultat = "Aucun job interrompu pour ce fichier."
            }
            erreurs = (etat?.erreurs ?? []) + (etat?.avertissements ?? [])
        }
    }

    private func reprendre() {
        Task {
            guard await env.santeOk() else { return }
            do {
                _ = try await APIService.shared.traduire(
                    cheminPdf: estMarkdown(cheminNet) ? nil : cheminNet,
                    cheminMd: estMarkdown(cheminNet) ? cheminNet : nil,
                    modele: env.modeleChoisi,
                    langueSource: env.langueSource, langueCible: env.langueCible,
                    extracteur: env.extracteurChoisi, resume: true)
                repriseDisponible = nil
                resultat = "▶ Traduction reprise — suivi dans « Nouveau document » ou en Bibliothèque une fois terminée."
            } catch {
                resultat = "❌ \(error.localizedDescription)"
            }
        }
    }
}

// MARK: - Teasers (fonctionnalités en développement)

/// Carte d'une fonctionnalité future : le clic explique qu'elle est en
/// développement et propose de partager son intérêt (email → log backend).
struct TeaserView: View {
    let titre: String
    let description: String
    let bouton: String
    let fonctionnalite: String

    @State private var afficherDialogue = false
    @State private var afficherSaisieEmail = false
    @State private var email: String = ""
    @State private var statut: String? = nil

    var body: some View {
        GroupBox(titre) {
            VStack(alignment: .leading, spacing: 8) {
                Text(description)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                HStack {
                    Button(bouton) { afficherDialogue = true }
                        .buttonStyle(.bordered)
                        .tint(DS.accent)
                    if let statut {
                        Text(statut)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.top, 4)
        }
        .confirmationDialog(
            "La fonctionnalité est en développement.",
            isPresented: $afficherDialogue,
            titleVisibility: .visible
        ) {
            Button("Partager mon intérêt") { afficherSaisieEmail = true }
            Button("Annuler", role: .cancel) {}
        } message: {
            Text("Veux-tu nous partager ton intérêt pour cette fonctionnalité ?")
        }
        .sheet(isPresented: $afficherSaisieEmail) {
            VStack(alignment: .leading, spacing: 14) {
                Text("Partager mon intérêt — \(titre)")
                    .font(.headline)
                TextField("Ton adresse email", text: $email)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 300)
                HStack {
                    Spacer()
                    Button("Annuler") { afficherSaisieEmail = false }
                        .keyboardShortcut(.cancelAction)
                    Button("Envoyer") { envoyer() }
                        .buttonStyle(.borderedProminent)
                        .keyboardShortcut(.defaultAction)
                        .disabled(email.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
            .padding(20)
        }
    }

    private func envoyer() {
        Task {
            do {
                try await APIService.shared.manifesterInteret(
                    fonctionnalite: fonctionnalite,
                    email: email.trimmingCharacters(in: .whitespaces))
                statut = "✅ Merci ! Ton intérêt a été enregistré."
                afficherSaisieEmail = false
            } catch {
                statut = "❌ \(error.localizedDescription)"
                afficherSaisieEmail = false
            }
        }
    }
}
