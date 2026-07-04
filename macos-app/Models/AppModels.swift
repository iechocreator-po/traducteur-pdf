import Foundation

nonisolated struct HealthResponse: Codable {
    let statut: String
    let ollamaAccessible: String

    enum CodingKeys: String, CodingKey {
        case statut
        case ollamaAccessible = "ollama_accessible"
    }
}

nonisolated struct ModelesResponse: Codable {
    let modeles: [String]
}

nonisolated struct EtatJob: Codable {
    let jobId: String
    let cheminPdf: String
    let cheminSortie: String
    let langueSource: String
    let langueCible: String
    let modeleOllama: String
    let statut: String
    let derniereSectionCompletee: Int
    let totalSections: Int
    let totalPages: Int
    let totalMots: Int
    let motsTraduits: Int
    let tempsEcouleSecondes: Double
    let estimationTempsTotalSecondes: Double?
    let erreurs: [String]
    let avertissements: [String]
    let journal: [String]
    let chapitresTraduits: [Int]

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        jobId = try c.decode(String.self, forKey: .jobId)
        cheminPdf = try c.decode(String.self, forKey: .cheminPdf)
        cheminSortie = try c.decode(String.self, forKey: .cheminSortie)
        langueSource = try c.decode(String.self, forKey: .langueSource)
        langueCible = try c.decode(String.self, forKey: .langueCible)
        modeleOllama = try c.decode(String.self, forKey: .modeleOllama)
        statut = try c.decode(String.self, forKey: .statut)
        derniereSectionCompletee = try c.decode(Int.self, forKey: .derniereSectionCompletee)
        totalSections = try c.decode(Int.self, forKey: .totalSections)
        totalPages = try c.decode(Int.self, forKey: .totalPages)
        totalMots = try c.decode(Int.self, forKey: .totalMots)
        motsTraduits = try c.decode(Int.self, forKey: .motsTraduits)
        tempsEcouleSecondes = try c.decode(Double.self, forKey: .tempsEcouleSecondes)
        estimationTempsTotalSecondes = try c.decodeIfPresent(Double.self, forKey: .estimationTempsTotalSecondes)
        erreurs = try c.decode([String].self, forKey: .erreurs)
        avertissements = (try? c.decode([String].self, forKey: .avertissements)) ?? []
        journal = try c.decode([String].self, forKey: .journal)
        chapitresTraduits = (try? c.decode([Int].self, forKey: .chapitresTraduits)) ?? []
    }

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
        case totalPages = "total_pages"
        case totalMots = "total_mots"
        case motsTraduits = "mots_traduits"
        case tempsEcouleSecondes = "temps_ecoule_secondes"
        case estimationTempsTotalSecondes = "estimation_temps_total_secondes"
        case erreurs
        case avertissements
        case journal
        case chapitresTraduits = "chapitres_traduits"
    }

    var pagesTraduites: Int {
        guard totalSections > 0, totalPages > 0 else { return 0 }
        let ratio = Double(derniereSectionCompletee) / Double(totalSections)
        return min(totalPages, Int((Double(totalPages) * ratio).rounded()))
    }
}

nonisolated struct TranslateResponse: Codable {
    let jobId: String
    enum CodingKeys: String, CodingKey { case jobId = "job_id" }
}

nonisolated struct ResultatAnalyse: Codable {
    let nbPagesAnalysees: Int
    let texteExtractible: Bool
    let langueDetectee: String?
    let avertissements: [String]
    let recommandation: String
    let estimationNbChunks: Int
    let estimationTempsSecondes: Int

    enum CodingKeys: String, CodingKey {
        case nbPagesAnalysees = "nb_pages_analysees"
        case texteExtractible = "texte_extractible"
        case langueDetectee = "langue_detectee"
        case avertissements
        case recommandation
        case estimationNbChunks = "estimation_nb_chunks"
        case estimationTempsSecondes = "estimation_temps_secondes"
    }
}

nonisolated struct ExtracteurConfig: Codable, Identifiable {
    let id: String
    let nom: String
    let disponible: Bool
}

nonisolated struct ExtracteursResponse: Codable {
    let extracteurs: [ExtracteurConfig]
    let defaut: String
}

nonisolated struct ResultatConversion: Codable {
    let cheminSortie: String
    let nbCaracteres: Int

    enum CodingKeys: String, CodingKey {
        case cheminSortie = "chemin_sortie"
        case nbCaracteres = "nb_caracteres"
    }
}

nonisolated struct JobPlanifie: Codable, Identifiable {
    let id: String
    let cheminPdf: String
    let langueSource: String
    let langueCible: String
    let modeleOllama: String
    let extracteurPdf: String
    let executer_a: String
    let creeA: String
    let statut: String

    enum CodingKeys: String, CodingKey {
        case id
        case cheminPdf = "chemin_pdf"
        case langueSource = "langue_source"
        case langueCible = "langue_cible"
        case modeleOllama = "modele_ollama"
        case extracteurPdf = "extracteur_pdf"
        case executer_a
        case creeA = "cree_a"
        case statut
    }

    var dateExecution: Date? {
        ISO8601DateFormatter().date(from: executer_a)
    }
}

nonisolated struct JobsPlanifiesResponse: Codable {
    let jobs: [JobPlanifie]
}

nonisolated struct Chapitre: Codable, Identifiable {
    let index: Int
    let titre: String
    let niveau: Int
    let page: Int?
    var id: Int { index }
    // `contenu`, `ligne_debut`, `ligne_fin` ignorés (backend uniquement)
}

nonisolated struct ChapitresResponse: Codable {
    let chapitres: [Chapitre]
    let source: String  // "signets_pdf" | "titres_markdown"
}

enum ModeSource: String, CaseIterable, Identifiable {
    case pdf = "PDF"
    case markdown = "Markdown"
    var id: String { rawValue }
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
