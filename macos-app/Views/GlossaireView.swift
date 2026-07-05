import SwiftUI

/// Glossaire de termes à ne jamais traduire — un terme par ligne,
/// persisté côté backend (glossaire.json) et appliqué à toutes les traductions.
struct GlossaireView: View {
    @State private var texte: String = ""
    @State private var statut: String? = nil
    @State private var enCours = false

    var body: some View {
        GroupBox("Glossaire (optionnel)") {
            VStack(alignment: .leading, spacing: 8) {
                Text("Termes à ne jamais traduire (noms propres, acronymes, marques…) — un par ligne. Appliqué à toutes les traductions.")
                    .font(.caption)
                    .foregroundStyle(.secondary)

                TextEditor(text: $texte)
                    .font(.system(size: 12, design: .monospaced))
                    .frame(height: 72)
                    .overlay(
                        RoundedRectangle(cornerRadius: 6)
                            .stroke(Color(nsColor: .separatorColor), lineWidth: 1)
                    )

                HStack {
                    Button(enCours ? "Enregistrement…" : "💾 Enregistrer le glossaire") {
                        sauvegarder()
                    }
                    .buttonStyle(.bordered)
                    .disabled(enCours)

                    if let statut {
                        Text(statut)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.top, 4)
        }
        .task { await charger() }
    }

    private func charger() async {
        if let termes = try? await APIService.shared.glossaire() {
            texte = termes.joined(separator: "\n")
        }
    }

    private func sauvegarder() {
        enCours = true
        statut = nil
        let termes = texte
            .split(separator: "\n")
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
        Task {
            do {
                let sauves = try await APIService.shared.sauvegarderGlossaire(termes: termes)
                texte = sauves.joined(separator: "\n")
                statut = "✅ \(sauves.count) terme(s) enregistré(s)"
            } catch {
                statut = "❌ \(error.localizedDescription)"
            }
            enCours = false
        }
    }
}

#Preview {
    GlossaireView()
}
