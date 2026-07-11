import SwiftUI
import Combine
import UniformTypeIdentifiers

// ============================================================================
// Module A — « Nouveau document » : lot multi-fichiers.
// Dépôt par glisser-déposer ou sélecteur natif → analyse automatique →
// lancement en lot (file séquentielle côté backend : un seul job Ollama à la
// fois, progression individuelle par fichier) → document lisible en Bibliothèque.
// ============================================================================

struct FichierLot: Identifiable {
    enum Stage {
        case analyse, pret, probleme, lance, termine, erreur
    }

    let id = UUID()
    let chemin: String
    var stage: Stage = .analyse
    var qualite: String? = nil
    var etaSecondes: Int? = nil
    var nbChapitres: Int? = nil
    var message: String? = nil       // recommandation d'analyse ou erreur
    var jobId: String? = nil
    var statutJob: String = ""       // en_attente | en_cours | en_pause | …
    var progression: Double = 0      // 0…1
    var sectionsLabel: String = ""

    var type: String { estMarkdown(chemin) ? "MD" : "PDF" }
}

@MainActor
final class ImportViewModel: ObservableObject {
    @Published var lot: [FichierLot] = []
    @Published var lotEnPause = false
    @Published var afficherPlanif = false
    @Published var datePlanif: Date = {
        var d = Calendar.current.date(bySettingHour: 23, minute: 0, second: 0, of: Date()) ?? Date()
        if d < Date() { d = Calendar.current.date(byAdding: .day, value: 1, to: d) ?? d }
        return d
    }()
    @Published var planifStatut: String? = nil

    private var pollTask: Task<Void, Never>? = nil

    var nbPrets: Int { lot.filter { $0.stage == .pret }.count }
    var enTraitement: Bool { lot.contains { $0.stage == .lance } }
    var tousTermines: Bool { !lot.isEmpty && lot.allSatisfy { $0.stage == .termine } }

    // MARK: - Ajout & analyse

    func ajouter(chemins: [String], env: AppEnvironment) {
        for chemin in chemins {
            let c = chemin.trimmingCharacters(in: .whitespaces)
            guard !c.isEmpty, !lot.contains(where: { $0.chemin == c }) else { continue }
            guard c.lowercased().hasSuffix(".pdf") || estMarkdown(c) else { continue }
            let fichier = FichierLot(chemin: c)
            lot.append(fichier)
            Task { await analyser(id: fichier.id, env: env) }
        }
    }

    private func analyser(id: UUID, env: AppEnvironment) async {
        guard let chemin = lot.first(where: { $0.id == id })?.chemin else { return }
        do {
            if estMarkdown(chemin) {
                // Pas d'analyse LLM pour un Markdown : comptage des chapitres suffit
                let res = try await APIService.shared.chapitres(cheminMd: chemin, extracteur: env.extracteurChoisi)
                maj(id) {
                    $0.qualite = "Markdown"
                    $0.nbChapitres = res.chapitres.count
                    $0.stage = .pret
                }
            } else {
                let res = try await APIService.shared.analyser(
                    cheminPdf: chemin, modele: env.modeleChoisi,
                    langueSource: env.langueSource, langueCible: env.langueCible)
                maj(id) {
                    $0.nbChapitres = res.nbChapitres
                    $0.etaSecondes = res.estimationTempsSecondes
                    if res.texteExtractible {
                        $0.qualite = res.avertissements.isEmpty ? "Excellente" : "Correcte"
                        $0.stage = .pret
                    } else {
                        $0.qualite = "Problème"
                        $0.stage = .probleme
                        $0.message = res.recommandation
                    }
                }
            }
        } catch {
            maj(id) {
                $0.stage = .erreur
                $0.message = error.localizedDescription
            }
        }
    }

    func retirer(id: UUID) {
        lot.removeAll { $0.id == id && $0.stage != .lance }
    }

    // MARK: - Lancement & suivi

    func lancer(env: AppEnvironment) async {
        guard await env.santeOk() else { return }
        for fichier in lot where fichier.stage == .pret {
            do {
                let jobId = try await APIService.shared.traduire(
                    cheminPdf: estMarkdown(fichier.chemin) ? nil : fichier.chemin,
                    cheminMd: estMarkdown(fichier.chemin) ? fichier.chemin : nil,
                    modele: env.modeleChoisi,
                    langueSource: env.langueSource, langueCible: env.langueCible,
                    extracteur: env.extracteurChoisi, resume: false)
                maj(fichier.id) {
                    $0.jobId = jobId
                    $0.stage = .lance
                    $0.statutJob = "en_attente"
                }
            } catch {
                maj(fichier.id) {
                    $0.stage = .erreur
                    $0.message = error.localizedDescription
                }
            }
        }
        lotEnPause = false
        demarrerPolling()
    }

    private func demarrerPolling() {
        pollTask?.cancel()
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self, self.lot.contains(where: { $0.stage == .lance }) else { break }
                await self.pollLot()
                try? await Task.sleep(for: .seconds(2))
            }
        }
    }

    private func pollLot() async {
        for fichier in lot where fichier.stage == .lance {
            let etat = estMarkdown(fichier.chemin)
                ? try? await APIService.shared.checkResume(cheminMd: fichier.chemin)
                : try? await APIService.shared.checkResume(cheminPdf: fichier.chemin)
            guard let etat else { continue }
            maj(fichier.id) {
                $0.statutJob = etat.statut
                $0.sectionsLabel = "\(etat.derniereSectionCompletee)/\(etat.totalSections) sections"
                $0.progression = etat.totalSections > 0
                    ? Double(etat.derniereSectionCompletee) / Double(etat.totalSections)
                    : 0
                switch etat.statut {
                case "termine":
                    $0.stage = .termine
                    $0.progression = 1
                case "erreur":
                    $0.stage = .erreur
                    $0.message = etat.erreurs.last ?? "Erreur du job"
                case "annule":
                    $0.stage = .erreur
                    $0.message = "Job annulé"
                default:
                    break
                }
            }
        }
    }

    // MARK: - Pause / reprise globale

    func basculerPause(env: AppEnvironment) async {
        if lotEnPause {
            for fichier in lot where fichier.stage == .lance && fichier.statutJob == "en_pause" {
                if let jobId = try? await APIService.shared.traduire(
                    cheminPdf: estMarkdown(fichier.chemin) ? nil : fichier.chemin,
                    cheminMd: estMarkdown(fichier.chemin) ? fichier.chemin : nil,
                    modele: env.modeleChoisi,
                    langueSource: env.langueSource, langueCible: env.langueCible,
                    extracteur: env.extracteurChoisi, resume: true) {
                    maj(fichier.id) {
                        $0.jobId = jobId
                        $0.statutJob = "en_attente"
                    }
                }
            }
            lotEnPause = false
            demarrerPolling()
        } else {
            for fichier in lot where fichier.stage == .lance {
                if let jobId = fichier.jobId {
                    try? await APIService.shared.pauseJob(jobId: jobId)
                }
            }
            lotEnPause = true
        }
    }

    // MARK: - Planification

    func planifier(env: AppEnvironment) async {
        let chemins = lot.filter { $0.stage == .pret }.map(\.chemin)
        guard !chemins.isEmpty else {
            planifStatut = "Aucun fichier prêt à planifier."
            return
        }
        do {
            let jobs = try await APIService.shared.planifierBatch(
                chemins: chemins, modele: env.modeleChoisi,
                langueSource: env.langueSource, langueCible: env.langueCible,
                extracteur: env.extracteurChoisi, executerA: datePlanif)
            planifStatut = "✅ \(jobs.count) fichier(s) planifié(s)"
            lot.removeAll { $0.stage == .pret }
        } catch {
            planifStatut = "❌ \(error.localizedDescription)"
        }
    }

    // MARK: - Helpers

    private func maj(_ id: UUID, _ transform: (inout FichierLot) -> Void) {
        guard let idx = lot.firstIndex(where: { $0.id == id }) else { return }
        transform(&lot[idx])
    }
}

// MARK: - Vue

struct ImportModuleView: View {
    let modeAvance: Bool
    @EnvironmentObject private var env: AppEnvironment
    @StateObject private var vm = ImportViewModel()
    @State private var deposeEnCours = false
    @State private var afficherJobsPlanifies = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                dropzone
                if !vm.lot.isEmpty {
                    enTeteLot
                    reglagesLot
                    ForEach(vm.lot) { fichier in
                        ligneFichier(fichier)
                    }
                }
                HStack {
                    Spacer()
                    Button("🕐 Voir les traductions planifiées") { afficherJobsPlanifies = true }
                        .buttonStyle(.borderless)
                        .font(.caption)
                }
            }
            .padding(24)
            .frame(maxWidth: 680)
            .frame(maxWidth: .infinity)
        }
        .sheet(isPresented: $afficherJobsPlanifies) {
            ScheduledJobsView()
        }
    }

    // MARK: - Dropzone

    private var dropzone: some View {
        VStack(spacing: 10) {
            RoundedRectangle(cornerRadius: 12)
                .fill(DS.accent.opacity(0.15))
                .frame(width: 40, height: 40)
            Text("Glisse tes PDF ou Markdown ici")
                .font(.system(size: 15, weight: .bold))
            Text("plusieurs fichiers à la fois — ou clique pour parcourir")
                .font(.caption)
                .foregroundStyle(.secondary)
            Button("Parcourir…") { choisirFichiers() }
                .buttonStyle(.bordered)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, vm.lot.isEmpty ? 36 : 20)
        .background(
            RoundedRectangle(cornerRadius: DS.radiusLg)
                .strokeBorder(deposeEnCours ? DS.accent : DS.border,
                              style: StrokeStyle(lineWidth: 2, dash: [7]))
                .background(
                    RoundedRectangle(cornerRadius: DS.radiusLg)
                        .fill(deposeEnCours ? DS.accent.opacity(0.06) : .clear)
                )
        )
        .dropDestination(for: URL.self) { urls, _ in
            vm.ajouter(chemins: urls.map(\.path), env: env)
            return true
        } isTargeted: { cible in
            deposeEnCours = cible
        }
    }

    private func choisirFichiers() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories = false
        var types: [UTType] = [.pdf]
        if let md = UTType(filenameExtension: "md") { types.append(md) }
        panel.allowedContentTypes = types
        if panel.runModal() == .OK {
            vm.ajouter(chemins: panel.urls.map(\.path), env: env)
        }
    }

    // MARK: - Lot

    private var enTeteLot: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text("\(vm.lot.count) fichier\(vm.lot.count > 1 ? "s" : "") dans le lot")
                .font(.system(size: 13.5, weight: .bold))
            Text(vm.tousTermines ? "Tous terminés" : "\(vm.nbPrets) prêt\(vm.nbPrets > 1 ? "s" : "") à traduire")
                .font(.caption)
                .foregroundStyle(.secondary)
            Spacer()
        }
    }

    private var reglagesLot: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 12) {
                Text("RÉGLAGES DU LOT — APPLIQUÉS À TOUS LES FICHIERS")
                    .font(.system(size: 10, weight: .bold))
                    .foregroundStyle(.secondary)

                HStack(spacing: 8) {
                    Picker("", selection: $env.langueSource) {
                        ForEach(Langue.allCases) { l in
                            Text("Depuis : \(l.label)").tag(l)
                        }
                    }
                    .labelsHidden()
                    Text("→").foregroundStyle(.secondary)
                    Picker("", selection: $env.langueCible) {
                        ForEach(Langue.allCases) { l in
                            Text("Vers : \(l.label)").tag(l)
                        }
                    }
                    .labelsHidden()
                }

                if modeAvance {
                    HStack(spacing: 12) {
                        Picker("Moteur de conversion :", selection: $env.extracteurChoisi) {
                            ForEach(env.extracteurs) { ext in
                                Text(ext.disponible ? ext.nom : "\(ext.nom) (bientôt)")
                                    .tag(ext.id)
                            }
                        }
                        Picker("Modèle IA :", selection: $env.modeleChoisi) {
                            ForEach(env.modeles, id: \.self) { m in
                                Text(m).tag(m)
                            }
                        }
                    }
                    .font(.caption)
                }

                HStack(spacing: 10) {
                    Button(vm.nbPrets > 0 ? "Lancer la traduction (\(vm.nbPrets))" : "Lancer la traduction") {
                        Task { await vm.lancer(env: env) }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(vm.nbPrets == 0)

                    if vm.enTraitement {
                        Button(vm.lotEnPause ? "▸ Reprendre" : "⏸ Pause") {
                            Task { await vm.basculerPause(env: env) }
                        }
                        .buttonStyle(.bordered)
                    }

                    Spacer()

                    Button("Planifier plus tard →") {
                        vm.afficherPlanif.toggle()
                    }
                    .buttonStyle(.borderless)
                    .font(.caption)
                }

                if vm.afficherPlanif {
                    Divider()
                    HStack(spacing: 10) {
                        DatePicker("Exécuter à partir de :", selection: $vm.datePlanif)
                            .font(.caption)
                        Button("🕐 Planifier le lot") {
                            Task { await vm.planifier(env: env) }
                        }
                        .buttonStyle(.bordered)
                        .disabled(vm.nbPrets == 0)
                    }
                    if let statut = vm.planifStatut {
                        Text(statut)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(6)
        }
    }

    // MARK: - Ligne fichier

    private func ligneFichier(_ fichier: FichierLot) -> some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 10) {
                    Text(fichier.type)
                        .font(.system(size: 9.5, weight: .heavy))
                        .padding(.horizontal, 6)
                        .padding(.vertical, 3)
                        .background(DS.accent.opacity(0.14), in: RoundedRectangle(cornerRadius: 5))
                        .foregroundStyle(DS.accent)

                    Text(nomFichier(fichier.chemin))
                        .font(.system(size: 12.5, weight: .bold))
                        .lineLimit(1)
                        .truncationMode(.middle)
                        .help(fichier.chemin)

                    Spacer()

                    pillStatut(fichier)

                    if fichier.stage != .lance {
                        Button {
                            vm.retirer(id: fichier.id)
                        } label: {
                            Image(systemName: "xmark")
                                .font(.system(size: 10, weight: .bold))
                                .foregroundStyle(.secondary)
                        }
                        .buttonStyle(.plain)
                        .help("Retirer du lot")
                    }
                }

                switch fichier.stage {
                case .analyse:
                    HStack(spacing: 6) {
                        ProgressView().controlSize(.small)
                        Text("Analyse en cours…")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                case .pret, .probleme:
                    HStack(spacing: 16) {
                        if let q = fichier.qualite {
                            Text("Qualité : \(Text(q).bold().foregroundStyle(fichier.stage == .probleme ? DS.amber : DS.green))")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        if let eta = fichier.etaSecondes {
                            Text("≈ \(formaterDuree(eta))").font(.caption).foregroundStyle(.secondary)
                        }
                        if let nb = fichier.nbChapitres {
                            Text("\(nb) chapitre\(nb > 1 ? "s" : "")").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    if fichier.stage == .probleme, let msg = fichier.message {
                        Text("⚠ \(msg)")
                            .font(.caption)
                            .foregroundStyle(DS.amber)
                    }
                case .lance, .termine:
                    ProgressView(value: fichier.progression)
                        .tint(fichier.stage == .termine ? DS.green : DS.accent)
                    if !fichier.sectionsLabel.isEmpty {
                        Text(fichier.sectionsLabel)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                case .erreur:
                    if let msg = fichier.message {
                        Text("⚠ \(msg)")
                            .font(.caption)
                            .foregroundStyle(DS.red)
                    }
                }
            }
            .padding(4)
        }
    }

    private func pillStatut(_ fichier: FichierLot) -> some View {
        let (texte, couleur): (String, Color) = switch fichier.stage {
        case .analyse: ("Analyse…", DS.text3)
        case .pret: ("Prêt", DS.accent)
        case .probleme: ("Problème", DS.amber)
        case .lance: (fichier.statutJob == "en_attente" ? "En file…"
                      : fichier.statutJob == "en_pause" ? "En pause" : "En cours…", DS.amber)
        case .termine: ("Terminé", DS.green)
        case .erreur: ("Erreur", DS.red)
        }
        return Text(texte)
            .font(.system(size: 10, weight: .bold))
            .padding(.horizontal, 7)
            .padding(.vertical, 3)
            .background(couleur.opacity(0.15), in: RoundedRectangle(cornerRadius: 6))
            .foregroundStyle(couleur)
    }
}
