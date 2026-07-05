import SwiftUI

struct ScheduledJobsView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var jobs: [JobPlanifie] = []
    @State private var etatsReels: [String: String] = [:]  // job.id → libellé du statut réel
    @State private var chargement = false

    private let fmt: DateFormatter = {
        let f = DateFormatter()
        f.dateStyle = .medium
        f.timeStyle = .short
        return f
    }()

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("Traductions planifiées")
                    .font(.title3.bold())
                Spacer()
                Button { charger() } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .buttonStyle(.borderless)
                .disabled(chargement)

                Button("Fermer") { dismiss() }
                    .buttonStyle(.bordered)
                    .keyboardShortcut(.cancelAction)
            }
            .padding()

            Divider()

            if jobs.isEmpty {
                Text(chargement ? "Chargement…" : "Aucune traduction planifiée.")
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .padding()
            } else {
                List(jobs) { job in
                    JobPlanifieRow(
                        job: job,
                        formatter: fmt,
                        statutAffiche: libelleStatut(job)
                    ) {
                        annuler(job)
                    }
                }
            }
        }
        .frame(minWidth: 520, minHeight: 320)
        .task { charger() }
    }

    private func libelleStatut(_ job: JobPlanifie) -> String {
        switch job.statut {
        case "planifie": return "🕐 Planifié"
        case "annule":   return "✕ Annulé"
        default:         return etatsReels[job.id] ?? "▶ Déclenché"
        }
    }

    private func charger() {
        chargement = true
        Task {
            let tous = (try? await APIService.shared.tousJobsPlanifies()) ?? []
            // Les plus récents en premier
            jobs = tous.sorted { $0.creeA > $1.creeA }
            chargement = false

            // Pour les jobs déclenchés, va chercher l'état réel de la traduction
            for job in jobs where job.statut == "declenche" {
                let chemin = job.cheminPdf
                let estMd = chemin.lowercased().hasSuffix(".md")
                let etat = estMd
                    ? try? await APIService.shared.checkResume(cheminMd: chemin)
                    : try? await APIService.shared.checkResume(cheminPdf: chemin)
                etatsReels[job.id] = libelleEtatReel(etat.flatMap { $0 })
            }
        }
    }

    private func libelleEtatReel(_ etat: EtatJob?) -> String {
        guard let etat else { return "▶ Déclenché" }
        switch etat.statut {
        case "termine":    return "✅ Terminé"
        case "erreur":     return "❌ Erreur"
        case "annule":     return "✕ Annulé"
        case "en_pause":   return "⏸ En pause"
        case "en_attente": return "⏳ En file d'attente"
        default:           return "🔄 En cours — \(etat.derniereSectionCompletee)/\(etat.totalSections) sections"
        }
    }

    private func annuler(_ job: JobPlanifie) {
        Task {
            try? await APIService.shared.annulerJobPlanifie(id: job.id)
            charger()
        }
    }
}

private struct JobPlanifieRow: View {
    let job: JobPlanifie
    let formatter: DateFormatter
    let statutAffiche: String
    let onAnnuler: () -> Void

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 4) {
                Text(URL(fileURLWithPath: job.cheminPdf).lastPathComponent)
                    .font(.headline)
                HStack(spacing: 12) {
                    Text("\(job.langueSource) → \(job.langueCible)")
                    if let d = job.dateExecution {
                        Text(formatter.string(from: d))
                    }
                    Text(statutAffiche)
                }
                .font(.caption)
                .foregroundStyle(.secondary)
            }
            Spacer()
            if job.statut == "planifie" {
                Button("Annuler", role: .destructive) { onAnnuler() }
                    .buttonStyle(.bordered)
            }
        }
        .padding(.vertical, 4)
    }
}
