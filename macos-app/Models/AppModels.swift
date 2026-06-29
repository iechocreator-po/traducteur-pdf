import Foundation

struct HealthResponse: Codable {
    let statut: String
    let ollamaAccessible: String

    enum CodingKeys: String, CodingKey {
        case statut
        case ollamaAccessible = "ollama_accessible"
    }
}

struct ModelesResponse: Codable {
    let modeles: [String]
}

struct EtatJob: Codable {
    let jobId: String
    let cheminPdf: String
    let cheminSortie: String
    let langueSource: String
    let langueCible: String
    let modeleOllama: String
    let statut: String
    let derniereSectionCompletee: Int
    let totalSections: Int

    enum CodingKeys: String, CodingKey {
        case jobId = "job_id"
        case cheminPdf = "chemin_pdf"
        case cheminSortie = "chemin_sortie"
        case langueSource = "langue_source"
        case langueCible = "langue_cible"
        case modeleOllama = "modele_ollama"
        case statut
        case derniereSectionCompletee = "derniere_section_completee"
        case totalSections = "total_sections"
    }
}

struct ResultatTraduction: Codable {
    let sectionTraitees: Int?
    let cheminSortie: String?
    let detail: String?

    enum CodingKeys: String, CodingKey {
        case sectionTraitees = "sections_traitees"
        case cheminSortie = "chemin_sortie"
        case detail
    }
}

struct ResultatAnalyse: Codable {
    let nbPagesAnalysees: Int
    let texteExtractible: Bool
    let langueDetectee: String?
    let avertissements: [String]
    let recommandation: String

    enum CodingKeys: String, CodingKey {
        case nbPagesAnalysees = "nb_pages_analysees"
        case texteExtractible = "texte_extractible"
        case langueDetectee = "langue_detectee"
        case avertissements
        case recommandation
    }
}

enum Langue: String, CaseIterable, Identifiable {
    case anglais, francais = "français", espagnol

    var id: String { rawValue }
    var label: String {
        switch self {
        case .anglais: return "Anglais"
        case .francais: return "Français"
        case .espagnol: return "Espagnol"
        }
    }
}
