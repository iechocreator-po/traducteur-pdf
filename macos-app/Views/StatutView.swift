import SwiftUI

struct StatutView: View {
    let apiEnLigne: Bool?
    let ollamaOk: Bool?

    var body: some View {
        GroupBox("État du système") {
            HStack(spacing: 16) {
                indicateur(label: "Backend API", ok: apiEnLigne)
                indicateur(label: "Ollama", ok: ollamaOk)
            }
            .padding(.top, 4)
        }
    }

    @ViewBuilder
    private func indicateur(label: String, ok: Bool?) -> some View {
        HStack(spacing: 6) {
            Circle()
                .fill(couleur(ok))
                .frame(width: 10, height: 10)
            Text(label)
                .font(.subheadline)
            Text(texte(ok))
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
    }

    private func couleur(_ ok: Bool?) -> Color {
        switch ok {
        case true: return DS.green
        case false: return DS.red
        case nil: return DS.text3
        }
    }

    private func texte(_ ok: Bool?) -> String {
        switch ok {
        case true: return "✓"
        case false: return "✗"
        case nil: return "…"
        }
    }
}
