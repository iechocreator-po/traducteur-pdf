import SwiftUI

// ============================================================================
// STYLES VISUELS — équivalent macOS de l'onglet web « Réglages ».
// Mêmes 6 styles que frontend/css/styles-visuels.css, mêmes palettes d'accent et
// de fond (clair / sombre). Choix mémorisé dans UserDefaults ("styleVisuel"),
// lu par DS (Theme.swift) pour l'accent et les rayons.
//
// Ce que le style change : accent (.tint), fond de la fenêtre, typographie
// (design système : monospace / serif), casse des onglets, rayons des coins.
// Les surfaces natives (GroupBox, matériaux macOS) restent natives : elles
// s'adaptent déjà au clair/sombre et gardent le look de la plateforme.
// Pour ajouter un style : un case + ses propriétés ici, et l'équivalent CSS
// dans frontend/css/styles-visuels.css.
// ============================================================================

enum StyleVisuel: String, CaseIterable, Identifiable {
    case classique, suisse, machine, classeur, plan, calque

    /// Clé UserDefaults / @AppStorage partagée par toute l'app.
    static let cleStockage = "styleVisuel"

    /// Style courant, lu à la volée (DS.* est évalué à chaque rendu).
    static var actuel: StyleVisuel {
        StyleVisuel(rawValue: UserDefaults.standard.string(forKey: cleStockage) ?? "") ?? .classique
    }

    var id: String { rawValue }

    var libelle: String {
        switch self {
        case .classique: return "Classique"
        case .suisse: return "Bureau suisse"
        case .machine: return "Machine à écrire"
        case .classeur: return "Classeur d'archives"
        case .plan: return "Plan de travail"
        case .calque: return "Calque"
        }
    }

    var detail: String {
        switch self {
        case .classique: return "Le design system d'origine : doux, arrondi, bleu."
        case .suisse: return "Grille nette, rouge vif, aucun arrondi."
        case .machine: return "Chasse fixe, ruban rouge, onglets en capitales."
        case .classeur: return "Teintes de chemise cartonnée, brique, coins peu arrondis."
        case .plan: return "Bleu de plan, texte technique, repère jaune en sombre."
        case .calque: return "Papier calque gris-bleu, titres en empattement, encre bleue."
        }
    }

    /// Accent (clair / sombre) — mêmes valeurs que --accent dans styles-visuels.css.
    var accent: Color {
        switch self {
        case .classique: return Color(light: 0x2a78d6, dark: 0x5b9be0)
        case .suisse: return Color(light: 0xd62a14, dark: 0xff5a42)
        case .machine: return Color(light: 0xb3261e, dark: 0xef6f63)
        case .classeur: return Color(light: 0xa8321f, dark: 0xe2745f)
        case .plan: return Color(light: 0x0a58ca, dark: 0xffd24a)
        case .calque: return Color(light: 0x1d3fd0, dark: 0x9bb0ff)
        }
    }

    /// Fond de fenêtre teinté ; nil = fond natif macOS (Classique).
    var fond: Color? {
        switch self {
        case .classique: return nil
        case .suisse: return Color(light: 0xf1f1ee, dark: 0x111111)
        case .machine: return Color(light: 0xecebe6, dark: 0x1c1b18)
        case .classeur: return Color(light: 0xe8d3a2, dark: 0x2a2620)
        case .plan: return Color(light: 0xeaf1f8, dark: 0x0e2b4d)
        case .calque: return Color(light: 0xd9dee2, dark: 0x1a2128)
        }
    }

    var fondOuNatif: Color { fond ?? Color(nsColor: .windowBackgroundColor) }

    /// Design typographique système (pas de police embarquée : l'app reste 100 % locale).
    var design: Font.Design {
        switch self {
        case .machine, .plan: return .monospaced
        case .calque: return .serif
        case .classique, .suisse, .classeur: return .default
        }
    }

    /// Casse des libellés d'onglets (nil = telle quelle).
    var casse: Text.Case? {
        switch self {
        case .machine, .classeur, .plan: return .uppercase
        case .classique, .suisse, .calque: return nil
        }
    }

    // Rayons (--ds-radius-sm / --ds-radius / --ds-radius-lg).
    var radiusSm: CGFloat {
        switch self {
        case .classique: return 8
        case .classeur, .calque: return 3
        case .suisse, .machine, .plan: return 0
        }
    }

    var radius: CGFloat {
        switch self {
        case .classique: return 10
        case .classeur, .calque: return 3
        case .suisse, .machine, .plan: return 0
        }
    }

    var radiusLg: CGFloat {
        switch self {
        case .classique: return 12
        case .classeur, .calque: return 3
        case .suisse, .machine, .plan: return 0
        }
    }
}
