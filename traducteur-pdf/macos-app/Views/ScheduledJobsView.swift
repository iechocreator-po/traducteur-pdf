import SwiftUI

struct ScheduledJobsView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var jobs: [JobPlanifie] = []
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
                    JobPlanifieRow(job: job, formatter: fmt) {
                        annuler(job)
                    }
                }
            }
        }
        .frame(minWidth: 480, minHeight: 300)
        .task { charger() }
    }

    private func charger() {
        chargement = true
        Task {
            jobs = (try? await APIService.shared.jobsPlanifies()) ?? []
            chargement = false
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
                }
                .font(.caption)
                .foregroundStyle(.secondary)
            }
            Spacer()
            Button("Annuler", role: .destructive) { onAnnuler() }
                .buttonStyle(.bordered)
        }
        .padding(.vertical, 4)
    }
}
