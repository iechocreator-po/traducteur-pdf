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
    /// Repères d'interface (Views/Reperes.swift) : OFF par défaut, réglable dans ⚙ Réglages.
    @AppStorage("reperesInterface") private var reperesInterface: Bool = false
    /// Cartes du Laboratoire désactivées dans Réglages, sous forme « C2,C4 » (Views/Reperes.swift).
    @AppStorage("labCartesMasquees") private var labCartesMasquees: String = ""
    @State private var reglagesOuverts = false
    @AppStorage(StyleVisuel.cleStockage) private var styleChoisi: StyleVisuel = .classique

    var body: some View {
        VStack(spacing: 0) {
            barreApp
            Divider()
            // .id(styleChoisi) : DS.* est lu à chaque rendu mais les sous-vues ne se
            // redessinent pas d'elles-mêmes quand le style change → on reconstruit le module.
            Group {
                switch moduleActif {
                case .nouveauDocument:
                    ImportModuleView(modeAvance: modeAvance)
                case .bibliotheque:
                    BibliothequeModuleView()
                case .laboratoire:
                    LaboratoireModuleView()
                }
            }
            .id(styleChoisi)
        }
        .background(styleChoisi.fondOuNatif)
        .fontDesign(styleChoisi.design)
        .tint(styleChoisi.accent)
        .environmentObject(env)
        .frame(minWidth: 860, minHeight: 560)
        .task { await env.rafraichir() }
        .preferredColorScheme(themeChoice.colorScheme)
        .environment(\.reperesActifs, reperesInterface)
        .environment(\.cartesMasquees, idsDepuisCSV(labCartesMasquees))
        .sheet(isPresented: $reglagesOuverts) {
            ReglagesView(
                modeAvance: $modeAvance,
                reperesInterface: $reperesInterface,
                cartesMasqueesCSV: $labCartesMasquees
            )
        }
    }

    private var barreApp: some View {
        HStack(spacing: 16) {
            HStack(spacing: 8) {
                RoundedRectangle(cornerRadius: min(6, DS.radiusSm))
                    .fill(DS.accent)
                    .frame(width: 18, height: 18)
                Text("toledo")
                    .font(.system(size: 15, weight: .heavy))
            }

            HStack(spacing: 4) {
                ForEach(Array(ModuleApp.allCases.enumerated()), id: \.element.id) { index, module in
                    Button { moduleActif = module } label: {
                        HStack(spacing: 6) {
                            if reperesInterface {
                                RepereOnglet(texte: lettreRepere(index), actif: moduleActif == module)
                            }
                            Text(module.libelle).textCase(styleChoisi.casse)
                        }
                    }
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

            Button { reglagesOuverts = true } label: {
                Image(systemName: "gearshape")
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
            .help("Réglages")
            .accessibilityLabel("Réglages")
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
