import SwiftUI
import UniformTypeIdentifiers

struct DocumentView: View {
    @Binding var cheminPdf: String
    @Binding var modeleChoisi: String
    @Binding var langueSource: Langue
    @Binding var langueCible: Langue
    let modeles: [String]
    let onCheminChange: () -> Void

    var body: some View {
        GroupBox("Document & options") {
            VStack(alignment: .leading, spacing: 12) {
                // Sélecteur de fichier
                HStack {
                    TextField("Chemin du PDF…", text: $cheminPdf)
                        .textFieldStyle(.roundedBorder)
                        .onChange(of: cheminPdf) { _, _ in onCheminChange() }
                    Button("Choisir…") { ouvrirPicker() }
                }

                // Modèle
                HStack {
                    Text("Modèle Ollama :")
                        .frame(width: 120, alignment: .trailing)
                    Picker("", selection: $modeleChoisi) {
                        ForEach(modeles, id: \.self) { Text($0).tag($0) }
                        if modeles.isEmpty {
                            Text("Aucun modèle").tag("")
                        }
                    }
                    .labelsHidden()
                }

                // Langues
                HStack {
                    Text("Langue source :")
                        .frame(width: 120, alignment: .trailing)
                    Picker("", selection: $langueSource) {
                        ForEach(Langue.allCases) { Text($0.label).tag($0) }
                    }
                    .labelsHidden()
                    .frame(width: 120)

                    Text("→ Langue cible :")
                    Picker("", selection: $langueCible) {
                        ForEach(Langue.allCases) { Text($0.label).tag($0) }
                    }
                    .labelsHidden()
                    .frame(width: 120)
                }
            }
            .padding(.top, 4)
        }
    }

    private func ouvrirPicker() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.pdf]
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        if panel.runModal() == .OK, let url = panel.url {
            cheminPdf = url.path
            onCheminChange()
        }
    }
}
