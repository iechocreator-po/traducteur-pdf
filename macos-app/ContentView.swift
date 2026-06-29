import SwiftUI

final class AppViewModel: ObservableObject {
    @Published var apiEnLigne: Bool? = nil
    @Published var ollamaOk: Bool? = nil
    @Published var modeles: [String] = []
    @Published var modeleChoisi: String = ""
    @Published var langueSource: Langue = .anglais
    @Published var langueCible: Langue = .francais
    @Published var cheminPdf: String = ""
    @Published var etat: EtatOperation = .vide
    @Published var repriseInfo: String? = nil

    func verifierStatut() {
        Task {
            do {
                let health = try await APIService.shared.health()
                apiEnLigne = true
                ollamaOk = health.ollamaAccessible == "oui"
            } catch {
                apiEnLigne = false
                ollamaOk = nil
            }
        }
        Task {
            do {
                let rep = try await APIService.shared.modeles()
                modeles = rep.modeles
                if modeleChoisi.isEmpty, let premier = rep.modeles.first {
                    modeleChoisi = premier
                }
            } catch {}
        }
    }

    func checkResume() {
        let chemin = cheminPdf.trimmingCharacters(in: .whitespaces)
        guard !chemin.isEmpty else { repriseInfo = nil; return }
        Task {
            let etatJob = try? await APIService.shared.checkResume(cheminPdf: chemin)
            if let j = etatJob, j.derniereSectionCompletee > 0 {
                repriseInfo = "section \(j.derniereSectionCompletee)/\(j.totalSections)"
            } else {
                repriseInfo = nil
            }
        }
    }

    func analyser() {
        let chemin = cheminPdf.trimmingCharacters(in: .whitespaces)
        guard !chemin.isEmpty else { etat = .erreur("Indique le chemin du PDF."); return }
        etat = .enCours("Analyse en cours…")
        Task {
            do {
                let res = try await APIService.shared.analyser(
                    cheminPdf: chemin, modele: modeleChoisi,
                    langueSource: langueSource, langueCible: langueCible)
                let texte = """
                Pages analysées : \(res.nbPagesAnalysees)
                Texte extractible : \(res.texteExtractible ? "oui" : "non")
                Langue détectée : \(res.langueDetectee ?? "—")
                Recommandation : \(res.recommandation)
                Avertissements : \(res.avertissements.isEmpty ? "aucun" : res.avertissements.joined(separator: ", "))
                """
                etat = .succes(texte)
            } catch {
                etat = .erreur(error.localizedDescription)
            }
        }
    }

    func traduire(resume: Bool = false) {
        let chemin = cheminPdf.trimmingCharacters(in: .whitespaces)
        guard !chemin.isEmpty else { etat = .erreur("Indique le chemin du PDF."); return }
        etat = .enCours(resume ? "Reprise en cours…" : "Traduction en cours…")
        Task {
            do {
                let res = try await APIService.shared.traduire(
                    cheminPdf: chemin, modele: modeleChoisi,
                    langueSource: langueSource, langueCible: langueCible,
                    resume: resume)
                if let detail = res.detail {
                    etat = .erreur(detail)
                } else {
                    let sections = res.sectionTraitees.map { "\($0) sections" } ?? "—"
                    let sortie = res.cheminSortie ?? "—"
                    etat = .succes("Terminé — \(sections)\nFichier : \(sortie)")
                    repriseInfo = nil
                }
            } catch {
                etat = .erreur(error.localizedDescription)
            }
        }
    }
}

struct ContentView: View {
    @StateObject private var vm = AppViewModel()

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            VStack(alignment: .leading, spacing: 2) {
                Text("iTraducteur PDF")
                    .font(.title2.bold())
                Text("Traduction locale via Ollama — aucune donnée ne quitte ton ordinateur.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }

            StatutView(apiEnLigne: vm.apiEnLigne, ollamaOk: vm.ollamaOk)

            DocumentView(
                cheminPdf: $vm.cheminPdf,
                modeleChoisi: $vm.modeleChoisi,
                langueSource: $vm.langueSource,
                langueCible: $vm.langueCible,
                modeles: vm.modeles,
                onCheminChange: vm.checkResume
            )

            ResultatView(
                etat: vm.etat,
                repriseInfo: vm.repriseInfo,
                onAnalyser: vm.analyser,
                onTraduire: { vm.traduire(resume: false) },
                onReprendre: { vm.traduire(resume: true) }
            )

            Spacer()
        }
        .padding(20)
        .frame(minWidth: 620, minHeight: 420)
        .task { vm.verifierStatut() }
    }
}

#Preview {
    ContentView()
}
