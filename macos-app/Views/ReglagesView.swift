import SwiftUI

/// Réglages (⚙) — feuille ouverte depuis la barre supérieure, comme le panneau de la version
/// web et de castellana. Un seul onglet pour l'instant, « Personnalisation » ; la structure
/// accueille les suivants.
struct ReglagesView: View {
    @Binding var modeAvance: Bool
    @Binding var reperesInterface: Bool
    @Binding var cartesMasqueesCSV: String
    @Environment(\.dismiss) private var dismiss
    @AppStorage(StyleVisuel.cleStockage) private var styleChoisi: StyleVisuel = .classique

    /// Cartes du Laboratoire activables une à une. Liste écrite à la main, comme les repères
    /// eux-mêmes (voir Reperes.swift) : ajouter une carte au Laboratoire oblige à l'ajouter ici.
    private let cartesLab: [(id: String, titre: String)] = [
        ("C1", "État du système"),
        ("C2", "Glossaire (optionnel)"),
        ("C3", "Lecture audio (TTS local)"),
        ("C4", "Outils document"),
        ("C5", "Voix personnalisées"),
        ("C6", "Export PDF"),
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Réglages").font(.title3.bold())
                Spacer()
                Button("Fermer") { dismiss() }
                    .keyboardShortcut(.cancelAction)
            }
            Divider()

            HStack(spacing: 6) {
                if reperesInterface { RepereOnglet(texte: "R-A", actif: true) }
                Text("Personnalisation")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(DS.accent)
            }

            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    carteModeAvance.repere("R-A1")
                    carteReperes.repere("R-A2")
                    carteLaboratoire.repere("R-A3")
                    carteStyle.repere("R-A4")
                }
                .padding(.top, 8)
                .padding(.horizontal, 2)
            }
        }
        .padding(20)
        .frame(width: 560, height: 560)
        .environment(\.reperesActifs, reperesInterface)
    }

    private var carteModeAvance: some View {
        GroupBox("Mode avancé") {
            HStack(alignment: .top, spacing: 16) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Affiche, dans « Nouveau document », le choix du moteur de conversion PDF. À ne toucher qu'en cas de PDF récalcitrant : un mauvais choix y coûte cher (l'OCR sur un PDF qui a déjà une couche texte donnerait de l'OCR là où le texte réel existe).")
                    Text("Rien n'est supprimé en le désactivant. La version web en montre davantage (Laboratoire, Résumé & Quiz, sélection de chapitres par cases à cocher).")
                }
                .font(.caption)
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .leading)
                Toggle("Mode avancé", isOn: $modeAvance)
                    .toggleStyle(.switch)
                    .labelsHidden()
            }
            .padding(.top, 4)
        }
    }

    private var carteReperes: some View {
        GroupBox("Repères d'interface") {
            HStack(alignment: .center, spacing: 16) {
                Text("Affiche une lettre sur chaque module et une lettre+numéro sur chaque carte (ex. C3) ; ceux des Réglages sont préfixés de R-. Pratique pour pointer précisément un endroit de l'app dans un retour. Les lignes créées dynamiquement (documents du lot) n'en ont pas.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Toggle("Repères d'interface", isOn: $reperesInterface)
                    .toggleStyle(.switch)
                    .labelsHidden()
            }
            .padding(.top, 4)
        }
    }

    private var carteLaboratoire: some View {
        GroupBox("Laboratoire — éléments affichés") {
            VStack(alignment: .leading, spacing: 6) {
                Text("Choisissez, carte par carte, ce que le Laboratoire affiche. Le repère (C1, C2…) est celui des Repères d'interface ; une carte masquée garde son repère.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                ForEach(cartesLab, id: \.id) { carte in
                    Toggle(isOn: affichee(carte.id)) {
                        HStack(spacing: 8) {
                            Text(carte.id)
                                .font(.system(size: 11, weight: .bold, design: .monospaced))
                                .foregroundStyle(DS.text3)
                            Text(carte.titre)
                        }
                    }
                    .toggleStyle(.switch)   // même style que « Mode avancé » et « Repères d'interface »
                }
                Button("Tout afficher") { cartesMasqueesCSV = "" }
                    .controlSize(.small)
                    .disabled(cartesMasqueesCSV.isEmpty)
            }
            .padding(.top, 4)
        }
    }

    /// Liaison « affichée » d'une carte : vrai tant que son repère n'est pas dans la liste des masquées.
    private func affichee(_ id: String) -> Binding<Bool> {
        Binding(
            get: { !idsDepuisCSV(cartesMasqueesCSV).contains(id) },
            set: { visible in
                var ids = idsDepuisCSV(cartesMasqueesCSV)
                if visible { ids.remove(id) } else { ids.insert(id) }
                cartesMasqueesCSV = csvDepuisIds(ids)
            }
        )
    }

    // MARK: - Style de l'interface (même 6 styles que la version web)

    private var carteStyle: some View {
        GroupBox("Style de l'interface") {
            VStack(alignment: .leading, spacing: 10) {
                Text("Le changement est immédiat et mémorisé. Chaque style existe en clair et en sombre (sélecteur Auto / Clair / Sombre en haut). « Classique » est l'apparence habituelle de toledo.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 200), spacing: 12)], spacing: 12) {
                    ForEach(StyleVisuel.allCases) { style in
                        carteChoixStyle(style)
                    }
                }
            }
            .padding(.top, 4)
        }
    }

    private func carteChoixStyle(_ style: StyleVisuel) -> some View {
        let actif = style == styleChoisi
        return Button {
            styleChoisi = style
        } label: {
            VStack(alignment: .leading, spacing: 6) {
                ZStack(alignment: .topLeading) {
                    RoundedRectangle(cornerRadius: style.radiusSm)
                        .fill(style.fondOuNatif)
                    VStack(alignment: .leading, spacing: 6) {
                        Text("Aa")
                            .font(.system(size: 16, weight: .bold, design: style.design))
                            .foregroundStyle(DS.text)
                        RoundedRectangle(cornerRadius: style.radiusSm / 3)
                            .fill(style.accent)
                            .frame(width: 90, height: 5)
                    }
                    .padding(8)
                }
                .frame(height: 54)
                .overlay(RoundedRectangle(cornerRadius: style.radiusSm).strokeBorder(DS.border))
                HStack {
                    Text(style.libelle)
                        .font(.system(size: 12, weight: .semibold))
                    Spacer()
                    if actif {
                        Image(systemName: "checkmark.circle.fill")
                            .foregroundStyle(style.accent)
                    }
                }
                Text(style.detail)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(8)
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
}
