import SwiftUI

// ============================================================================
// REPÈRES D'INTERFACE (10/10/2026) — même idée que la version web (js/ui-ids.js) et que
// castellana : une lettre par module, une lettre+numéro par carte (ex. « C3 »), pour pointer
// précisément un endroit de l'app dans un retour. Interrupteur dans Réglages (⚙), OFF par défaut.
//
// DIFFÉRENCE avec le web, assumée : la lettre de chaque module est DÉRIVÉE de l'ordre de
// `ModuleApp.allCases`, mais le numéro d'une carte est écrit à la main au site d'appel
// (`.repere("C3")`) — SwiftUI n'a pas de DOM à parcourir. Conséquence : ajouter une carte
// au milieu d'un module oblige à renuméroter à la main les suivantes. À ne PAS oublier ;
// c'est le seul endroit du produit où un identifiant d'interface est écrit en dur.
//
// Les lignes créées dynamiquement (documents du lot) n'ont pas de repère : leur nombre change
// et décalerait toutes les cartes qui les suivent.
// ============================================================================

private struct ReperesActifsKey: EnvironmentKey {
    static let defaultValue = false
}

extension EnvironmentValues {
    /// Vrai quand l'interrupteur « Repères d'interface » est actif.
    var reperesActifs: Bool {
        get { self[ReperesActifsKey.self] }
        set { self[ReperesActifsKey.self] = newValue }
    }
}

private struct CartesMasqueesKey: EnvironmentKey {
    static let defaultValue: Set<String> = []
}

extension EnvironmentValues {
    /// Repères des cartes que l'utilisateur a désactivées dans Réglages (ex. ["C2", "C4"]).
    /// Une carte masquée disparaît de la mise en page mais GARDE son repère : la numérotation
    /// ne change pas.
    var cartesMasquees: Set<String> {
        get { self[CartesMasqueesKey.self] }
        set { self[CartesMasqueesKey.self] = newValue }
    }
}

/// Les cartes masquées sont mémorisées dans `@AppStorage` sous forme de liste « C2,C4 ».
func idsDepuisCSV(_ csv: String) -> Set<String> {
    Set(csv.split(separator: ",").map { String($0).trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty })
}

func csvDepuisIds(_ ids: Set<String>) -> String {
    ids.sorted().joined(separator: ",")
}

/// Pastille de lettre d'un module (barre supérieure) ou d'un onglet de Réglages.
struct RepereOnglet: View {
    let texte: String
    var actif: Bool = false

    var body: some View {
        Text(texte)
            .font(.system(size: 9.5, weight: .bold, design: .monospaced))
            .foregroundStyle(Color(nsColor: .windowBackgroundColor))
            .padding(.horizontal, 4)
            .frame(minWidth: 16, minHeight: 16)
            .background(Capsule().fill(actif ? DS.accent : DS.text3))
            .accessibilityHidden(true)
    }
}

private struct RepereCarte: ViewModifier {
    @Environment(\.reperesActifs) private var actif
    @Environment(\.cartesMasquees) private var masquees
    let id: String

    @ViewBuilder
    func body(content: Content) -> some View {
        if masquees.contains(id) {
            EmptyView()
        } else {
            content.overlay(alignment: .topTrailing) {
                if actif {
                Text(id)
                    .font(.system(size: 10.5, weight: .bold, design: .monospaced))
                    .foregroundStyle(Color(nsColor: .windowBackgroundColor))
                    .padding(.horizontal, 7)
                    .padding(.vertical, 2)
                    .background(RoundedRectangle(cornerRadius: 6).fill(Color.primary))
                    .offset(x: -12, y: -9)
                    .allowsHitTesting(false)
                    .accessibilityHidden(true)
                }
            }
        }
    }
}

extension View {
    /// Pose le repère d'une carte (ex. `.repere("C3")`) — visible seulement si les repères sont actifs ;
    /// la carte disparaît si elle a été désactivée dans Réglages (`cartesMasquees`).
    func repere(_ id: String) -> some View { modifier(RepereCarte(id: id)) }
}

/// Lettre d'un module d'après sa position dans `ModuleApp.allCases` (A, B, C…).
func lettreRepere(_ index: Int) -> String {
    var n = index
    var sortie = ""
    repeat {
        sortie = String(UnicodeScalar(UInt8(65 + n % 26))) + sortie
        n = n / 26 - 1
    } while n >= 0
    return sortie
}
