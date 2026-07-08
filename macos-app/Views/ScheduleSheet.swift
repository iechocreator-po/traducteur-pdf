import SwiftUI

struct ScheduleSheet: View {
    let cheminPdf: String?
    let cheminMd: String?
    let modele: String
    let langueSource: Langue
    let langueCible: Langue
    let extracteur: String
    var chapitresSelectionnes: [Int]? = nil

    @Binding var isPresented: Bool
    var onPlanifie: ([JobPlanifie]) -> Void

    @State private var dateChoisie: Date = {
        // Proposer dans 1 heure par défaut
        Calendar.current.date(byAdding: .hour, value: 1, to: Date()) ?? Date()
    }()
    @State private var fichiersSupplementaires: String = ""
    @State private var enCours = false
    @State private var erreur: String? = nil

    private var extras: [String] {
        fichiersSupplementaires
            .split(separator: "\n")
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text("Planifier la traduction")
                .font(.title3.bold())

            GroupBox("Résumé du job") {
                VStack(alignment: .leading, spacing: 6) {
                    let nomFichier = (cheminMd ?? cheminPdf).map { URL(fileURLWithPath: $0).lastPathComponent } ?? "—"
                    LabelValeur(label: "Fichier", valeur: nomFichier)
                    LabelValeur(label: "De", valeur: langueSource.label)
                    LabelValeur(label: "Vers", valeur: langueCible.label)
                    LabelValeur(label: "Modèle", valeur: modele)
                    if cheminMd == nil {
                        LabelValeur(label: "Extracteur", valeur: extracteur)
                    }
                    if let chapitres = chapitresSelectionnes, !chapitres.isEmpty {
                        let indices = chapitres.sorted().map { String($0 + 1) }.joined(separator: ", ")
                        LabelValeur(label: "Chapitres", valeur: "\(chapitres.count) sélectionné(s) : \(indices)")
                    } else {
                        LabelValeur(label: "Chapitres", valeur: "Document complet")
                    }
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

            GroupBox("Autres fichiers à enchaîner (optionnel)") {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Un chemin complet par ligne (.pdf ou .md). Ils seront traduits l'un après l'autre, à la suite du fichier principal.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    TextEditor(text: $fichiersSupplementaires)
                        .font(.system(size: 12, design: .monospaced))
                        .frame(height: 60)
                        .overlay(
                            RoundedRectangle(cornerRadius: 6)
                                .stroke(Color(nsColor: .separatorColor), lineWidth: 1)
                        )
                    if !extras.isEmpty, chapitresSelectionnes?.isEmpty == false {
                        Text("⚠️ En mode multi-fichiers, la sélection de chapitres est ignorée : les documents sont traduits en entier.")
                            .font(.caption)
                            .foregroundStyle(DS.amber)
                    }
                }
                .padding(.top, 4)
            }

            if let err = erreur {
                Text(err)
                    .foregroundStyle(DS.red)
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
                if extras.isEmpty {
                    let job = try await APIService.shared.planifier(
                        cheminPdf: cheminPdf,
                        cheminMd: cheminMd,
                        modele: modele,
                        langueSource: langueSource,
                        langueCible: langueCible,
                        extracteur: extracteur,
                        executer_a: dateChoisie,
                        chapitresSelectionnes: chapitresSelectionnes
                    )
                    onPlanifie([job])
                } else {
                    // Multi-fichiers : le principal + les extras, enchaînés par la file d'attente
                    var chemins: [String] = []
                    if let principal = cheminMd ?? cheminPdf, !principal.isEmpty {
                        chemins.append(principal)
                    }
                    chemins.append(contentsOf: extras)
                    let jobs = try await APIService.shared.planifierBatch(
                        chemins: chemins,
                        modele: modele,
                        langueSource: langueSource,
                        langueCible: langueCible,
                        extracteur: extracteur,
                        executerA: dateChoisie
                    )
                    onPlanifie(jobs)
                }
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
