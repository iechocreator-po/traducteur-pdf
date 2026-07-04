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
    @Published var modeSource: ModeSource = .pdf
    @Published var cheminPdf: String = ""
    @Published var cheminMd: String = ""
    @Published var etat: EtatOperation = .vide
    @Published var repriseInfo: String? = nil
    @Published var extracteurs: [ExtracteurConfig] = []
    @Published var extracteurChoisi: String = "pymupdf4llm"
    @Published var derniereAnalyse: ResultatAnalyse? = nil
    @Published var etatJob: EtatJob? = nil
    @Published var afficherScheduleSheet: Bool = false
    @Published var afficherJobsPlanifies: Bool = false
    @Published var derniereConfirmationPlanification: String? = nil
    @Published var chapitresDisponibles: [Chapitre] = []
    @Published var chapitresSelectionnes: Set<Int> = []
    @Published var chapitresSource: String = ""

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

    func identifierChapitres() {
        let pdf = cheminPdf.trimmingCharacters(in: .whitespaces)
        let md  = cheminMd.trimmingCharacters(in: .whitespaces)
        let estMd = modeSource == .markdown
        let chemin = estMd ? md : pdf
        guard !chemin.isEmpty else { etat = .erreur("Indique un fichier d'abord."); return }
        etat = .enCours("Identification des chapitres…")
        Task {
            do {
                let resultat = try await APIService.shared.chapitres(
                    cheminPdf: estMd ? nil : pdf,
                    cheminMd: estMd ? md : nil,
                    extracteur: extracteurChoisi
                )
                chapitresDisponibles = resultat.chapitres
                chapitresSelectionnes = Set(resultat.chapitres.map(\.index))
                chapitresSource = resultat.source == "signets_pdf"
                    ? "📑 Table des matières officielle (signets PDF)"
                    : "🔍 Titres détectés dans le Markdown"
                etat = .vide
            } catch {
                etat = .erreur(error.localizedDescription)
            }
        }
    }

    func reinitialiserChapitres() {
        chapitresDisponibles = []
        chapitresSelectionnes = []
        chapitresSource = ""
    }

    func checkResume() {
        let pdf = cheminPdf.trimmingCharacters(in: .whitespaces)
        let md  = cheminMd.trimmingCharacters(in: .whitespaces)
        let actif = modeSource == .markdown ? md : pdf
        reinitialiserChapitres()
        guard !actif.isEmpty else { repriseInfo = nil; return }
        Task {
            let j = modeSource == .markdown
                ? try? await APIService.shared.checkResume(cheminMd: md)
                : try? await APIService.shared.checkResume(cheminPdf: pdf)
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
        guard modeSource == .pdf else { return }
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
        guard modeSource == .pdf else { return }
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
        let pdf = cheminPdf.trimmingCharacters(in: .whitespaces)
        let md  = cheminMd.trimmingCharacters(in: .whitespaces)
        let estMd = modeSource == .markdown
        let cheminActif = estMd ? md : pdf
        guard !cheminActif.isEmpty else {
            etat = .erreur(estMd ? "Indique le chemin du fichier Markdown." : "Indique le chemin du PDF.")
            return
        }

        Task {
            // Analyse préalable uniquement en mode PDF
            if !estMd && derniereAnalyse == nil && !resume {
                etat = .enCours("Analyse préalable…")
                if let res = try? await APIService.shared.analyser(
                    cheminPdf: pdf, modele: modeleChoisi,
                    langueSource: langueSource, langueCible: langueCible) {
                    derniereAnalyse = res
                }
            }

            etat = .enCours(resume ? "Reprise en cours…" : "Traduction démarrée…")
            do {
                let chapitres = chapitresDisponibles.isEmpty || chapitresSelectionnes.isEmpty
                    ? nil
                    : Array(chapitresSelectionnes)
                let jobId = try await APIService.shared.traduire(
                    cheminPdf: estMd ? nil : pdf,
                    cheminMd: estMd ? md : nil,
                    modele: modeleChoisi,
                    langueSource: langueSource, langueCible: langueCible,
                    extracteur: extracteurChoisi, resume: resume,
                    chapitresSelectionnes: chapitres)
                jobActuel = (jobId: jobId, cheminPdf: cheminActif)
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
                    modeSource: $vm.modeSource,
                    cheminPdf: $vm.cheminPdf,
                    cheminMd: $vm.cheminMd,
                    modeleChoisi: $vm.modeleChoisi,
                    langueSource: $vm.langueSource,
                    langueCible: $vm.langueCible,
                    extracteurChoisi: $vm.extracteurChoisi,
                    modeles: vm.modeles,
                    extracteurs: vm.extracteurs,
                    onCheminChange: vm.checkResume
                )

                ChapitresView(
                    chapitres: vm.chapitresDisponibles,
                    selectionnes: $vm.chapitresSelectionnes,
                    source: vm.chapitresSource,
                    onIdentifier: vm.identifierChapitres
                )

                ResultatView(
                    etat: vm.etat,
                    repriseInfo: vm.repriseInfo,
                    jobEnCours: vm.jobEnCours,
                    modeSource: vm.modeSource,
                    onAnalyser: vm.analyser,
                    onConvertir: vm.convertir,
                    onTraduire: { vm.traduire(resume: false) },
                    onReprendre: { vm.traduire(resume: true) },
                    onPlanifier: { vm.afficherScheduleSheet = true },
                    onVoirJobsPlanifies: { vm.afficherJobsPlanifies = true },
                    confirmationPlanification: vm.derniereConfirmationPlanification
                )
                .sheet(isPresented: $vm.afficherScheduleSheet) {
                    let estMd = vm.modeSource == .markdown
                    ScheduleSheet(
                        cheminPdf: estMd ? nil : vm.cheminPdf.trimmingCharacters(in: .whitespaces),
                        cheminMd: estMd ? vm.cheminMd.trimmingCharacters(in: .whitespaces) : nil,
                        modele: vm.modeleChoisi,
                        langueSource: vm.langueSource,
                        langueCible: vm.langueCible,
                        extracteur: vm.extracteurChoisi,
                        chapitresSelectionnes: vm.chapitresDisponibles.isEmpty || vm.chapitresSelectionnes.isEmpty
                            ? nil
                            : Array(vm.chapitresSelectionnes),
                        isPresented: $vm.afficherScheduleSheet
                    ) { job in
                        let fmt = DateFormatter()
                        fmt.dateStyle = .medium
                        fmt.timeStyle = .short
                        let date = job.dateExecution.map { fmt.string(from: $0) } ?? job.executer_a
                        let chapitresInfo: String
                        if vm.chapitresDisponibles.isEmpty || vm.chapitresSelectionnes.isEmpty {
                            chapitresInfo = "document complet"
                        } else {
                            let indices = vm.chapitresSelectionnes.sorted().map { String($0 + 1) }.joined(separator: ", ")
                            chapitresInfo = "\(vm.chapitresSelectionnes.count) chapitre(s) : \(indices)"
                        }
                        vm.derniereConfirmationPlanification = "Planifiée le \(date) — \(chapitresInfo)"
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

// MARK: - ChapitresView

struct ChapitresView: View {
    let chapitres: [Chapitre]
    @Binding var selectionnes: Set<Int>
    var source: String = ""
    let onIdentifier: () -> Void

    var body: some View {
        GroupBox("Chapitres (optionnel)") {
            VStack(alignment: .leading, spacing: 8) {
                Text("Identifie les chapitres pour ne traduire que certaines sections. Sans sélection, tout le document est traduit.")
                    .font(.caption)
                    .foregroundStyle(.secondary)

                Button("📋 Identifier les chapitres", action: onIdentifier)
                    .buttonStyle(.bordered)

                if !source.isEmpty {
                    Text(source)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                if !chapitres.isEmpty {
                    HStack {
                        Button("Tout sélectionner") {
                            selectionnes = Set(chapitres.map(\.index))
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.small)

                        Button("Tout désélectionner") {
                            selectionnes = []
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.small)

                        Spacer()
                        Text("\(selectionnes.count) / \(chapitres.count) sélectionné(s)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }

                    ScrollView {
                        VStack(alignment: .leading, spacing: 2) {
                            ForEach(chapitres) { chap in
                                Toggle(isOn: Binding(
                                    get: { selectionnes.contains(chap.index) },
                                    set: { isOn in
                                        if isOn { selectionnes.insert(chap.index) }
                                        else { selectionnes.remove(chap.index) }
                                    }
                                )) {
                                    let pageInfo = chap.page.map { " (p.\($0))" } ?? ""
                                    Text(String(repeating: "#", count: chap.niveau) + " " + chap.titre + pageInfo)
                                        .font(.system(size: 12, design: .monospaced))
                                        .lineLimit(1)
                                        .padding(.leading, CGFloat((chap.niveau - 1) * 10))
                                }
                                .toggleStyle(.checkbox)
                            }
                        }
                        .padding(.vertical, 4)
                    }
                    .frame(maxHeight: 220)
                    .overlay(
                        RoundedRectangle(cornerRadius: 6)
                            .stroke(Color(nsColor: .separatorColor), lineWidth: 1)
                    )
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.top, 4)
        }
    }
}

#Preview {
    ContentView()
}
