import SwiftUI

struct ScheduleSheet: View {
    let cheminPdf: String
    let modele: String
    let langueSource: Langue
    let langueCible: Langue
    let extracteur: String

    @Binding var isPresented: Bool
    var onPlanifie: (JobPlanifie) -> Void

    @State private var dateChoisie: Date = {
        // Proposer dans 1 heure par défaut
        Calendar.current.date(byAdding: .hour, value: 1, to: Date()) ?? Date()
    }()
    @State private var enCours = false
    @State private var erreur: String? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text("Planifier la traduction")
                .font(.title3.bold())

            GroupBox("Résumé du job") {
                VStack(alignment: .leading, spacing: 6) {
                    LabelValeur(label: "Fichier", valeur: URL(fileURLWithPath: cheminPdf).lastPathComponent)
                    LabelValeur(label: "De", valeur: langueSource.label)
                    LabelValeur(label: "Vers", valeur: langueCible.label)
                    LabelValeur(label: "Modèle", valeur: modele)
                    LabelValeur(label: "Extracteur", valeur: extracteur)
                }
                .padding(.top, 4)
            }

            DatePicker(
                "Exécuter le",
                selection: $dateChoisie,
                in: Date()...,
                displayedComponents: [.date, .hourAndMinute]
            )
            .datePickerStyle(.field)

            if let err = erreur {
                Text(err)
                    .foregroundStyle(.red)
                    .font(.footnote)
            }

            HStack {
                Spacer()
                Button("Annuler") { isPresented = false }
                    .buttonStyle(.bordered)
                    .keyboardShortcut(.cancelAction)

                Button(enCours ? "Planification…" : "Planifier") {
                    planifier()
                }
                .buttonStyle(.borderedProminent)
                .disabled(enCours)
                .keyboardShortcut(.defaultAction)
            }
        }
        .padding(24)
        .frame(minWidth: 400, maxWidth: 480)
    }

    private func planifier() {
        enCours = true
        erreur = nil
        Task {
            do {
                let job = try await APIService.shared.planifier(
                    cheminPdf: cheminPdf,
                    modele: modele,
                    langueSource: langueSource,
                    langueCible: langueCible,
                    extracteur: extracteur,
                    executer_a: dateChoisie
                )
                onPlanifie(job)
                isPresented = false
            } catch {
                erreur = error.localizedDescription
            }
            enCours = false
        }
    }
}

private struct LabelValeur: View {
    let label: String
    let valeur: String

    var body: some View {
        HStack(alignment: .top) {
            Text("\(label) :")
                .foregroundStyle(.secondary)
                .frame(width: 80, alignment: .trailing)
            Text(valeur)
                .lineLimit(2)
        }
        .font(.callout)
    }
}
