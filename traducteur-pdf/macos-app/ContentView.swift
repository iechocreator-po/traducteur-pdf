import SwiftUI
import Combine

@MainActor
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
    @Published var extracteurs: [ExtracteurConfig] = []
    @Published var extracteurChoisi: String = "pymupdf4llm"
    @Published var derniereAnalyse: ResultatAnalyse? = nil
    @Published var etatJob: EtatJob? = nil
    @Published var afficherScheduleSheet: Bool = false
    @Published var afficherJobsPlanifies: Bool = false
    @Published var derniereConfirmationPlanification: String? = nil

    private var jobActuel: (jobId: String, cheminPdf: String)? = nil
    private var pollingTimer: Timer? = nil

    var jobEnCours: Bool { jobActuel != nil }

    // MARK: - Init

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
        Task {
            do {
                let rep = try await APIService.shared.extracteurs()
                extracteurs = rep.extracteurs
                extracteurChoisi = rep.defaut
            } catch {}
        }
    }

    func checkResume() {
        let chemin = cheminPdf.trimmingCharacters(in: .whitespaces)
        guard !chemin.isEmpty else { repriseInfo = nil; return }
        Task {
            let j = try? await APIService.shared.checkResume(cheminPdf: chemin)
            if let j, j.derniereSectionCompletee > 0, j.statut != "termine" {
                repriseInfo = "section \(j.derniereSectionCompletee)/\(j.totalSections)"
            } else {
                repriseInfo = nil
            }
        }
    }

    // MARK: - Actions

    func analyser() {
        let chemin = cheminPdf.trimmingCharacters(in: .whitespaces)
        guard !chemin.isEmpty else { etat = .erreur("Indique le chemin du PDF."); return }
        etat = .enCours("Analyse en cours…")
        Task {
            do {
                let res = try await APIService.shared.analyser(
                    cheminPdf: chemin, modele: modeleChoisi,
                    langueSource: langueSource, langueCible: langueCible)
                derniereAnalyse = res
                etat = .succes("""
                    Pages analysées : \(res.nbPagesAnalysees)
                    Texte extractible : \(res.texteExtractible ? "oui" : "non")
                    Langue détectée : \(res.langueDetectee ?? "—")
                    Sections estimées : \(res.estimationNbChunks)
                    Durée estimée : \(formaterDuree(res.estimationTempsSecondes))
                    Recommandation : \(res.recommandation)
                    """)
            } catch {
                etat = .erreur(error.localizedDescription)
            }
        }
    }

    func convertir() {
        let chemin = cheminPdf.trimmingCharacters(in: .whitespaces)
        guard !chemin.isEmpty else { etat = .erreur("Indique le chemin du PDF."); return }
        etat = .enCours("Conversion en cours…")
        Task {
            do {
                let res = try await APIService.shared.convertir(cheminPdf: chemin, extracteur: extracteurChoisi)
                etat = .succes("Conversion terminée — \(res.nbCaracteres.formatted()) caractères\nFichier : \(res.cheminSortie)")
            } catch {
                etat = .erreur(error.localizedDescription)
            }
        }
    }

    func traduire(resume: Bool = false) {
        let chemin = cheminPdf.trimmingCharacters(in: .whitespaces)
        guard !chemin.isEmpty else { etat = .erreur("Indique le chemin du PDF."); return }

        Task {
            // Analyse préalable si pas encore faite
            if derniereAnalyse == nil && !resume {
                etat = .enCours("Analyse préalable…")
                if let res = try? await APIService.shared.analyser(
                    cheminPdf: chemin, modele: modeleChoisi,
                    langueSource: langueSource, langueCible: langueCible) {
                    derniereAnalyse = res
                }
            }

            etat = .enCours(resume ? "Reprise en cours…" : "Traduction démarrée…")
            do {
                let jobId = try await APIService.shared.traduire(
                    cheminPdf: chemin, modele: modeleChoisi,
                    langueSource: langueSource, langueCible: langueCible,
                    extracteur: extracteurChoisi, resume: resume)
                jobActuel = (jobId: jobId, cheminPdf: chemin)
                etatJob = nil
                demarrerPolling()
            } catch {
                etat = .erreur(error.localizedDescription)
            }
        }
    }

    // MARK: - Polling

    private func demarrerPolling() {
        arreterPolling()
        pollingTimer = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in
                await self?.pollStatut()
            }
        }
    }

    private func arreterPolling() {
        pollingTimer?.invalidate()
        pollingTimer = nil
    }

    private func pollStatut() async {
        guard let job = jobActuel else { return }
        guard let j = try? await APIService.shared.statutJob(jobId: job.jobId, cheminPdf: job.cheminPdf) else { return }
        etatJob = j

        switch j.statut {
        case "termine":
            arreterPolling()
            jobActuel = nil
            etat = .succes("Traduction terminée\nFichier : \(j.cheminSortie)")
            repriseInfo = nil
        case "erreur":
            arreterPolling()
            jobActuel = nil
            etat = .erreur(j.erreurs.first ?? "Erreur inconnue")
        case "en_pause":
            arreterPolling()
            repriseInfo = "section \(j.derniereSectionCompletee)/\(j.totalSections)"
        default:
            break
        }
    }
}

// MARK: - Helpers

func formaterDuree(_ secondes: Int) -> String {
    let m = secondes / 60
    let s = secondes % 60
    return "\(m):\(String(format: "%02d", s))"
}

func formaterDureeDouble(_ secondes: Double) -> String {
    guard secondes >= 0 else { return "—" }
    return formaterDuree(Int(secondes))
}

// MARK: - ContentView

struct ContentView: View {
    @StateObject private var vm = AppViewModel()

    var body: some View {
        ScrollView {
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
                    extracteurChoisi: $vm.extracteurChoisi,
                    modeles: vm.modeles,
                    extracteurs: vm.extracteurs,
                    onCheminChange: vm.checkResume
                )

                ResultatView(
                    etat: vm.etat,
                    repriseInfo: vm.repriseInfo,
                    jobEnCours: vm.jobEnCours,
                    onAnalyser: vm.analyser,
                    onConvertir: vm.convertir,
                    onTraduire: { vm.traduire(resume: false) },
                    onReprendre: { vm.traduire(resume: true) },
                    onPlanifier: { vm.afficherScheduleSheet = true },
                    onVoirJobsPlanifies: { vm.afficherJobsPlanifies = true },
                    confirmationPlanification: vm.derniereConfirmationPlanification
                )
                .sheet(isPresented: $vm.afficherScheduleSheet) {
                    ScheduleSheet(
                        cheminPdf: vm.cheminPdf.trimmingCharacters(in: .whitespaces),
                        modele: vm.modeleChoisi,
                        langueSource: vm.langueSource,
                        langueCible: vm.langueCible,
                        extracteur: vm.extracteurChoisi,
                        isPresented: $vm.afficherScheduleSheet
                    ) { job in
                        let fmt = DateFormatter()
                        fmt.dateStyle = .medium
                        fmt.timeStyle = .short
                        let date = job.dateExecution.map { fmt.string(from: $0) } ?? job.executer_a
                        vm.derniereConfirmationPlanification = "Traduction planifiée pour le \(date)"
                    }
                }
                .sheet(isPresented: $vm.afficherJobsPlanifies) {
                    ScheduledJobsView()
                }

                if vm.jobEnCours || vm.etatJob != nil {
                    ProgressionView(
                        etatJob: vm.etatJob,
                        estimationTotale: vm.derniereAnalyse.map { Double($0.estimationTempsSecondes) }
                    )
                }

                Spacer()
            }
            .padding(20)
        }
        .frame(minWidth: 640, minHeight: 480)
        .task { vm.verifierStatut() }
    }
}

#Preview {
    ContentView()
}
