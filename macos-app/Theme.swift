import SwiftUI
import AppKit

// ============================================================================
// DESIGN SYSTEM — thème macOS
// Traduction Swift des tokens du design system partagé
// (2000_DigitalProducts/design-system/tokens.css). Copie vendorée : traducteur-pdf
// est un projet indépendant, on ne référence pas le dossier parent. Les valeurs
// (clair / sombre) doivent rester alignées sur tokens.css.
//
// On applique surtout l'ACCENT (via .tint) et les couleurs SÉMANTIQUES de statut.
// Les surfaces natives (GroupBox, matériaux macOS) sont laissées telles quelles :
// elles s'adaptent déjà au mode clair/sombre et gardent le look natif de la plateforme.
// ============================================================================

extension Color {
    /// Construit une couleur opaque à partir d'un hex 0xRRGGBB.
    init(hex: UInt) {
        self.init(
            .sRGB,
            red: Double((hex >> 16) & 0xff) / 255,
            green: Double((hex >> 8) & 0xff) / 255,
            blue: Double(hex & 0xff) / 255,
            opacity: 1
        )
    }

    /// Couleur dynamique : bascule automatiquement selon l'apparence (clair/sombre).
    init(light: UInt, dark: UInt) {
        self.init(nsColor: NSColor(name: nil) { appearance in
            let estSombre = appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua
            return NSColor(Color(hex: estSombre ? dark : light))
        })
    }
}

/// Tokens sémantiques du design system, exposés à SwiftUI.
enum DS {
    // ---- Accent & statuts (valeur clair / valeur sombre) -------------------
    static let accent = Color(light: 0x2a78d6, dark: 0x5b9be0)
    static let green  = Color(light: 0x0f8a4c, dark: 0x3fbf7f)
    static let red    = Color(light: 0xcf3535, dark: 0xe57373)
    static let amber  = Color(light: 0x9a6a08, dark: 0xd9a94a)

    // ---- Neutres (disponibles ; on privilégie les couleurs système natives) --
    static let text  = Color(light: 0x1a1a19, dark: 0xf2f1ec)
    static let text2 = Color(light: 0x5f5e5a, dark: 0xb9b8b0)
    static let text3 = Color(light: 0x8a8880, dark: 0x85847d)
    static let border = Color(light: 0x141412, dark: 0xffffff).opacity(0.12)

    // ---- Rayons (--ds-radius-*) -------------------------------------------
    static let radiusSm: CGFloat = 8
    static let radius: CGFloat = 10
    static let radiusLg: CGFloat = 12
}

/// Choix de thème de l'utilisateur : Auto (suit le système) / Clair / Sombre.
/// Mémorisé via @AppStorage ; appliqué avec .preferredColorScheme (nil = auto).
enum ThemeChoice: String, CaseIterable, Identifiable {
    case auto, light, dark

    var id: String { rawValue }

    var libelle: String {
        switch self {
        case .auto: return "Auto"
        case .light: return "Clair"
        case .dark: return "Sombre"
        }
    }

    var colorScheme: ColorScheme? {
        switch self {
        case .auto: return nil
        case .light: return .light
        case .dark: return .dark
        }
    }
}
