import SwiftUI

// ============================================================================
// Module D — « Réglages » : choix du style visuel de l'interface.
// Même grille de 6 styles que l'onglet Réglages web. Le clic applique le style
// immédiatement (ContentView observe la même clé @AppStorage).
// ============================================================================

struct ReglagesModuleView: View {
    @AppStorage(StyleVisuel.cleStockage) private var styleChoisi: StyleVisuel = .classique

    private let colonnes = [GridItem(.adaptive(minimum: 240), spacing: 14)]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Réglages")
                        .font(.title2.bold())
                    Text("Choisis l'apparence qui te convient : le changement est immédiat et mémorisé. Chaque style existe en clair et en sombre (sélecteur Auto / Clair / Sombre en haut).")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                GroupBox {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("Style de l'interface")
                            .font(.headline)
                        LazyVGrid(columns: colonnes, spacing: 14) {
                            ForEach(StyleVisuel.allCases) { style in
                                carte(style)
                            }
                        }
                    }
                    .padding(6)
                }
            }
            .padding(24)
            .frame(maxWidth: 760, alignment: .leading)
            .frame(maxWidth: .infinity)
        }
    }

    private func carte(_ style: StyleVisuel) -> some View {
        let actif = style == styleChoisi
        return Button {
            styleChoisi = style
        } label: {
            VStack(alignment: .leading, spacing: 8) {
                apercu(style)
                HStack {
                    Text(style.libelle)
                        .font(.system(size: 13, weight: .semibold))
                    Spacer()
                    if actif {
                        Image(systemName: "checkmark.circle.fill")
                            .foregroundStyle(style.accent)
                    }
                }
                Text(style.detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.background.secondary, in: RoundedRectangle(cornerRadius: DS.radiusSm))
            .overlay(
                RoundedRectangle(cornerRadius: DS.radiusSm)
                    .strokeBorder(actif ? style.accent : DS.border, lineWidth: actif ? 2 : 1)
            )
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(actif ? .isSelected : [])
    }

    /// Pastille d'aperçu : fond, typographie et accent du style représenté.
    private func apercu(_ style: StyleVisuel) -> some View {
        ZStack(alignment: .topLeading) {
            RoundedRectangle(cornerRadius: style.radiusSm)
                .fill(style.fondOuNatif)
            VStack(alignment: .leading, spacing: 8) {
                Text("Aa")
                    .font(.system(size: 17, weight: .bold, design: style.design))
                    .foregroundStyle(DS.text)
                RoundedRectangle(cornerRadius: style.radiusSm / 3)
                    .fill(style.accent)
                    .frame(width: 110, height: 6)
            }
            .padding(10)
        }
        .frame(height: 64)
        .overlay(
            RoundedRectangle(cornerRadius: style.radiusSm)
                .strokeBorder(DS.border)
        )
    }
}
