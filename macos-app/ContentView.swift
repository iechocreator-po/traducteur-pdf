import SwiftUI
import Combine

// ============================================================================
// REFONTE « WORKFLOW » — 3 modules organisés par flux de travail
// (design toledo_v2/handoff_iTraducteur, décisions dans
//  docs/refonte-workflow-decisions.md) :
//   • Nouveau document — lot multi-fichiers, analyse auto, lancement en lot
//   • Bibliothèque     — lecture par chapitre, audio TTS, panneau IA
//   • Laboratoire      — configuration technique, outils, teasers
// ============================================================================

/// Module actif de la barre de navigation.
enum ModuleApp: String, CaseIterable, Identifiable {
    case nouveauDocument, bibliotheque, laboratoire

    var id: String { rawValue }

    var libelle: String {
        switch self {
        case .nouveauDocument: return "Nouveau document"
        case .bibliotheque: return "Bibliothèque"
        case .laboratoire: return "Laboratoire"
        }
    }
}

/// État partagé entre les modules : santé du backend, listes de configuration
/// et réglages du lot (langues, extracteur, modèle). Aucune logique métier.
@MainActor
final class AppEnvironment: ObservableObject {
    @Published var apiEnLigne: Bool? = nil
    @Published var ollamaOk: Bool? = nil
    @Published var modeles: [String] = []
    @Published var modeleChoisi: String = ""
    @Published var extracteurs: [ExtracteurConfig] = []
    @Published var extracteurChoisi: String = "pymupdf4llm"
    @Published var langueSource: Langue = .anglais
    @Published var langueCible: Langue = .francais
    @Published var flags: [String: Bool] = [:]

    func rafraichir() async {
        do {
            let health = try await APIService.shared.health()
            apiEnLigne = true
            ollamaOk = health.ollamaAccessible == "oui"
        } catch {
            apiEnLigne = false
            ollamaOk = nil
        }
        if let rep = try? await APIService.shared.modeles() {
            modeles = rep.modeles
            if modeleChoisi.isEmpty || !rep.modeles.contains(modeleChoisi) {
                modeleChoisi = rep.modeles.first ?? ""
            }
        }
        if let rep = try? await APIService.shared.extracteurs() {
            extracteurs = rep.extracteurs
            if !rep.extracteurs.contains(where: { $0.id == extracteurChoisi }) {
                extracteurChoisi = rep.defaut
            }
        }
        flags = (try? await APIService.shared.featureFlags()) ?? [:]
    }

    /// Vérifie que backend + Ollama répondent avant de lancer un job LLM.
    func santeOk() async -> Bool {
        await rafraichir()
        return apiEnLigne == true && ollamaOk == true
    }
}

// MARK: - Helpers partagés

func formaterDuree(_ secondes: Int) -> String {
    let m = secondes / 60
    let s = secondes % 60
    return "\(m):\(String(format: "%02d", s))"
}

func formaterDureeDouble(_ secondes: Double) -> String {
    guard secondes >= 0, secondes.isFinite else { return "—" }
    return formaterDuree(Int(secondes))
}

func nomFichier(_ chemin: String) -> String {
    (chemin as NSString).lastPathComponent
}

func estMarkdown(_ chemin: String) -> Bool {
    let c = chemin.lowercased()
    return c.hasSuffix(".md") || c.hasSuffix(".markdown")
}

// MARK: - ContentView

struct ContentView: View {
    @StateObject private var env = AppEnvironment()
    @AppStorage("themeChoice") private var themeChoice: ThemeChoice = .auto
    @AppStorage("moduleActif") private var moduleActif: ModuleApp = .nouveauDocument
    @AppStorage("modeAvance") private var modeAvance: Bool = false

    var body: some View {
        VStack(spacing: 0) {
            barreApp
            Divider()
            switch moduleActif {
            case .nouveauDocument:
                ImportModuleView(modeAvance: modeAvance)
            case .bibliotheque:
                BibliothequeModuleView()
            case .laboratoire:
                LaboratoireModuleView()
            }
        }
        .environmentObject(env)
        .frame(minWidth: 860, minHeight: 560)
        .task { await env.rafraichir() }
        .preferredColorScheme(themeChoice.colorScheme)
    }

    private var barreApp: some View {
        HStack(spacing: 16) {
            HStack(spacing: 8) {
                RoundedRectangle(cornerRadius: 6)
                    .fill(DS.accent)
                    .frame(width: 18, height: 18)
                Text("toledo")
                    .font(.system(size: 15, weight: .heavy))
            }

            HStack(spacing: 4) {
                ForEach(ModuleApp.allCases) { module in
                    Button(module.libelle) { moduleActif = module }
                        .buttonStyle(.plain)
                        .font(.system(size: 12.5, weight: .semibold))
                        .padding(.horizontal, 12)
                        .padding(.vertical, 7)
                        .background(
                            RoundedRectangle(cornerRadius: DS.radiusSm)
                                .fill(moduleActif == module ? DS.accent.opacity(0.14) : .clear)
                        )
                        .foregroundStyle(moduleActif == module ? DS.accent : .secondary)
                }
            }

            Spacer()

            HStack(spacing: 6) {
                pastille(env.apiEnLigne).help(env.apiEnLigne == true ? "Backend en ligne" : "Backend hors ligne")
                pastille(env.ollamaOk).help(env.ollamaOk == true ? "Ollama accessible" : "Ollama inaccessible")
            }

            Picker("Thème", selection: $themeChoice) {
                ForEach(ThemeChoice.allCases) { choix in
                    Text(choix.libelle).tag(choix)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .fixedSize()

            Toggle("Mode avancé", isOn: $modeAvance)
                .toggleStyle(.switch)
                .controlSize(.small)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(.secondary)
        }
        .padding(.horizontal, 18)
        .frame(height: 52)
        .background(.bar)
    }

    private func pastille(_ ok: Bool?) -> some View {
        Circle()
            .fill(ok == true ? DS.green : ok == false ? DS.red : DS.text3)
            .frame(width: 9, height: 9)
    }
}
