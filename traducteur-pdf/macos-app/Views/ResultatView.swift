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
    let jobEnCours: Bool
    let onAnalyser: () -> Void
    let onConvertir: () -> Void
    let onTraduire: () -> Void
    let onReprendre: () -> Void
    let onPlanifier: () -> Void
    let onVoirJobsPlanifies: () -> Void
    let confirmationPlanification: String?

    var body: some View {
        GroupBox("Actions") {
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 12) {
                    Button("Analyser") { onAnalyser() }
                        .buttonStyle(.bordered)
                        .disabled(bloque)

                    Button("Convertir en Markdown") { onConvertir() }
                        .buttonStyle(.bordered)
                        .disabled(bloque)

                    Menu("Traduire") {
                        Button("Traduire maintenant") { onTraduire() }
                        Button("Planifier la traduction…") { onPlanifier() }
                        Divider()
                        Button("Voir les traductions planifiées") { onVoirJobsPlanifies() }
                    }
                    .menuStyle(.borderedButton)
                    .fixedSize()
                    .disabled(bloque)

                    if let info = repriseInfo {
                        Button("Reprendre (\(info))") { onReprendre() }
                            .buttonStyle(.bordered)
                            .disabled(bloque)
                    }

                    if bloque {
                        ProgressView().scaleEffect(0.7)
                    }
                }

                if let conf = confirmationPlanification {
                    Text(conf)
                        .font(.footnote)
                        .foregroundStyle(.green)
                }

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

    private var bloque: Bool {
        jobEnCours || { if case .enCours = etat { return true }; return false }()
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
