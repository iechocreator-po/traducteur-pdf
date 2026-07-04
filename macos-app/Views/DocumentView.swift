import SwiftUI
import UniformTypeIdentifiers

struct DocumentView: View {
    @Binding var modeSource: ModeSource
    @Binding var cheminPdf: String
    @Binding var cheminMd: String
    @Binding var modeleChoisi: String
    @Binding var langueSource: Langue
    @Binding var langueCible: Langue
    @Binding var extracteurChoisi: String
    let modeles: [String]
    let extracteurs: [ExtracteurConfig]
    let onCheminChange: () -> Void

    var body: some View {
        GroupBox("Document & options") {
            VStack(alignment: .leading, spacing: 12) {

                // Toggle PDF / Markdown
                Picker("Source", selection: $modeSource) {
                    ForEach(ModeSource.allCases) { Text($0.rawValue).tag($0) }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .onChange(of: modeSource) { _, _ in onCheminChange() }

                // Sélecteur de fichier selon le mode
                if modeSource == .pdf {
                    HStack {
                        TextField("Chemin du PDF…", text: $cheminPdf)
                            .textFieldStyle(.roundedBorder)
                            .onChange(of: cheminPdf) { _, _ in onCheminChange() }
                        Button("Choisir…") { ouvrirPickerPdf() }
                    }
                } else {
                    HStack {
                        TextField("Chemin du fichier Markdown…", text: $cheminMd)
                            .textFieldStyle(.roundedBorder)
                            .onChange(of: cheminMd) { _, _ in onCheminChange() }
                        Button("Choisir…") { ouvrirPickerMd() }
                    }
                }

                // Modèle
                HStack {
                    Text("Modèle LLM :")
                        .frame(width: 120, alignment: .trailing)
                    Picker("", selection: $modeleChoisi) {
                        ForEach(modeles, id: \.self) { Text($0).tag($0) }
                        if modeles.isEmpty { Text("Aucun modèle").tag("") }
                    }
                    .labelsHidden()
                }

                // Extracteur PDF — masqué en mode Markdown
                if modeSource == .pdf {
                    HStack {
                        Text("Extracteur PDF :")
                            .frame(width: 120, alignment: .trailing)
                        Picker("", selection: $extracteurChoisi) {
                            ForEach(extracteurs) { ext in
                                Text(ext.disponible ? ext.nom : "\(ext.nom) (bientôt)")
                                    .tag(ext.id)
                            }
                            if extracteurs.isEmpty {
                                Text("PyMuPDF4LLM").tag("pymupdf4llm")
                            }
                        }
                        .labelsHidden()
                        .disabled(extracteurs.filter(\.disponible).count <= 1)
                    }
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

    private func ouvrirPickerPdf() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.pdf]
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        if panel.runModal() == .OK, let url = panel.url {
            cheminPdf = url.path
            onCheminChange()
        }
    }

    private func ouvrirPickerMd() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [UTType(filenameExtension: "md") ?? .plainText]
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        if panel.runModal() == .OK, let url = panel.url {
            cheminMd = url.path
            onCheminChange()
        }
    }
}
