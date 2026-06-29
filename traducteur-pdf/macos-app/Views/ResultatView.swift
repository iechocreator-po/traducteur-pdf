import SwiftUI

enum EtatOperation {
    case vide
    case enCours(String)
    case succes(String)
    case erreur(String)
}

struct ResultatView: View {
    let etat: EtatOperation
    let repriseInfo: String?
    let onAnalyser: () -> Void
    let onTraduire: () -> Void
    let onReprendre: () -> Void

    var body: some View {
        GroupBox("Actions") {
            VStack(alignment: .leading, spacing: 12) {
                // Boutons
                HStack(spacing: 12) {
                    Button("Analyser") { onAnalyser() }
                        .buttonStyle(.bordered)
                        .disabled(estEnCours)

                    Button("Traduire") { onTraduire() }
                        .buttonStyle(.borderedProminent)
                        .disabled(estEnCours)

                    if let info = repriseInfo {
                        Button("Reprendre (\(info))") { onReprendre() }
                            .buttonStyle(.bordered)
                            .disabled(estEnCours)
                    }

                    if estEnCours {
                        ProgressView().scaleEffect(0.7)
                    }
                }

                // Résultat
                if case .vide = etat { } else {
                    Divider()
                    ScrollView {
                        Text(texteResultat)
                            .font(.system(.body, design: .monospaced))
                            .foregroundStyle(couleurTexte)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .textSelection(.enabled)
                    }
                    .frame(minHeight: 80, maxHeight: 200)
                }
            }
            .padding(.top, 4)
        }
    }

    private var estEnCours: Bool {
        if case .enCours = etat { return true }
        return false
    }

    private var texteResultat: String {
        switch etat {
        case .vide: return ""
        case .enCours(let msg): return msg
        case .succes(let msg): return msg
        case .erreur(let msg): return "Erreur : \(msg)"
        }
    }

    private var couleurTexte: Color {
        switch etat {
        case .erreur: return .red
        case .succes: return .primary
        default: return .secondary
        }
    }
}
