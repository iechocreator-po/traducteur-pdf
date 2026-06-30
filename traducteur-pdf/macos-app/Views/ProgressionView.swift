import SwiftUI

struct ProgressionView: View {
    let etatJob: EtatJob?
    let estimationTotale: Double?

    var body: some View {
        GroupBox("Progression de la traduction") {
            VStack(alignment: .leading, spacing: 10) {
                // Barre de progression
                ProgressView(value: pourcentage, total: 1.0)
                    .progressViewStyle(.linear)

                // Indicateurs
                Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 6) {
                    GridRow {
                        Text("Pages traduites").foregroundStyle(.secondary)
                        Text(textePages).monospacedDigit()
                    }
                    GridRow {
                        Text("Mots traduits").foregroundStyle(.secondary)
                        Text(texteMots).monospacedDigit()
                    }
                    GridRow {
                        Text("Sections / chunks traduits").foregroundStyle(.secondary)
                        Text(texteSections).monospacedDigit()
                    }
                }
                .font(.system(.body, design: .monospaced))

                // Timing
                Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 6) {
                    GridRow {
                        Text("Temps écoulé").foregroundStyle(.secondary)
                        Text(tempsEcoule).monospacedDigit()
                    }
                    GridRow {
                        Text("Temps restant estimé").foregroundStyle(.secondary)
                        Text(tempsRestant).monospacedDigit()
                    }
                    GridRow {
                        Text("Durée totale estimée").foregroundStyle(.secondary)
                        Text(tempsTotalEstime).monospacedDigit()
                    }
                }
                .font(.system(.body, design: .monospaced))

                if let erreurs = etatJob?.erreurs, !erreurs.isEmpty {
                    Divider()
                    Text("⚠ \(erreurs.last ?? "")")
                        .foregroundStyle(.orange)
                        .font(.caption)
                }

                if let journal = etatJob?.journal, !journal.isEmpty {
                    Divider()
                    Text("Trace")
                        .font(.caption.bold())
                        .foregroundStyle(.secondary)
                    ScrollView {
                        VStack(alignment: .leading, spacing: 2) {
                            ForEach(journal.suffix(20), id: \.self) { ligne in
                                Text(ligne)
                                    .font(.caption.monospaced())
                                    .foregroundStyle(.secondary)
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .frame(maxHeight: 100)
                }
            }
            .padding(.top, 4)
        }
    }

    // MARK: - Computed

    private var sections: (done: Int, total: Int) {
        guard let j = etatJob else { return (0, 0) }
        return (j.derniereSectionCompletee, j.totalSections)
    }

    private var texteSections: String {
        let s = sections
        if s.total == 0 { return "—" }
        return "\(s.done) / \(s.total)"
    }

    private var textePages: String {
        guard let j = etatJob, j.totalPages > 0 else { return "—" }
        return "\(j.pagesTraduites) / \(j.totalPages)"
    }

    private var texteMots: String {
        guard let j = etatJob, j.totalMots > 0 else { return "—" }
        return "\(j.motsTraduits) / \(j.totalMots)"
    }

    private var pourcentage: Double {
        let s = sections
        guard s.total > 0 else { return 0 }
        return Double(s.done) / Double(s.total)
    }

    private var ecouleSecondes: Double {
        etatJob?.tempsEcouleSecondes ?? 0
    }

    private var tempsEcoule: String {
        formaterDureeDouble(ecouleSecondes)
    }

    private var estimationEffective: Double? {
        etatJob?.estimationTempsTotalSecondes ?? estimationTotale
    }

    private var tempsRestant: String {
        guard let total = estimationEffective, total > 0 else { return "—" }
        let restant = max(0, total - ecouleSecondes)
        return formaterDureeDouble(restant)
    }

    private var tempsTotalEstime: String {
        guard let total = estimationEffective, total > 0 else { return "—" }
        return formaterDureeDouble(total)
    }
}
