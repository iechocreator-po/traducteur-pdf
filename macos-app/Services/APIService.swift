import Foundation

actor APIService {
    static let shared = APIService()
    private let base = URL(string: "http://localhost:8000/api")!
    private let decoder: JSONDecoder = {
        let d = JSONDecoder()
        return d
    }()

    func health() async throws -> HealthResponse {
        try await get("health")
    }

    func modeles() async throws -> ModelesResponse {
        try await get("modeles")
    }

    func checkResume(cheminPdf: String? = nil, cheminMd: String? = nil) async throws -> EtatJob? {
        var body: [String: String] = [:]
        if let md = cheminMd { body["chemin_md"] = md } else if let pdf = cheminPdf { body["chemin_pdf"] = pdf }
        let data = try await post("check-resume", body: body)
        if data.isEmpty || data == Data("null".utf8) { return nil }
        return try decoder.decode(EtatJob.self, from: data)
    }

    func analyser(cheminPdf: String, modele: String, langueSource: Langue, langueCible: Langue) async throws -> ResultatAnalyse {
        let body: [String: String] = [
            "chemin_pdf": cheminPdf,
            "modele_ollama": modele,
            "langue_source": langueSource.rawValue,
            "langue_cible": langueCible.rawValue,
        ]
        let data = try await post("analyser", body: body)
        return try decoder.decode(ResultatAnalyse.self, from: data)
    }

    func extracteurs() async throws -> ExtracteursResponse {
        try await get("config/extracteurs")
    }

    func chapitres(cheminPdf: String? = nil, cheminMd: String? = nil, extracteur: String) async throws -> (chapitres: [Chapitre], source: String) {
        var body: [String: String] = ["extracteur_pdf": extracteur]
        if let md = cheminMd { body["chemin_md"] = md } else if let pdf = cheminPdf { body["chemin_pdf"] = pdf }
        let data = try await post("chapitres", body: body)
        let rep = try decoder.decode(ChapitresResponse.self, from: data)
        return (rep.chapitres, rep.source)
    }

    func convertir(cheminPdf: String, extracteur: String) async throws -> ResultatConversion {
        let body: [String: String] = [
            "chemin_pdf": cheminPdf,
            "extracteur_pdf": extracteur,
        ]
        let data = try await post("convert", body: body)
        return try decoder.decode(ResultatConversion.self, from: data)
    }

    func traduire(cheminPdf: String? = nil, cheminMd: String? = nil, modele: String, langueSource: Langue, langueCible: Langue, extracteur: String, resume: Bool, chapitresSelectionnes: [Int]? = nil) async throws -> String {
        var body: [String: Any] = [
            "modele_ollama": modele,
            "langue_source": langueSource.rawValue,
            "langue_cible": langueCible.rawValue,
            "extracteur_pdf": extracteur,
            "resume": resume,
        ]
        if let md = cheminMd { body["chemin_md"] = md } else if let pdf = cheminPdf { body["chemin_pdf"] = pdf }
        if let chapitres = chapitresSelectionnes, !chapitres.isEmpty {
            body["chapitres_selectionnes"] = chapitres
        }
        let data = try await postAny("translate", body: body)
        let rep = try decoder.decode(TranslateResponse.self, from: data)
        return rep.jobId
    }

    func planifier(
        cheminPdf: String? = nil,
        cheminMd: String? = nil,
        modele: String,
        langueSource: Langue,
        langueCible: Langue,
        extracteur: String,
        executer_a: Date,
        chapitresSelectionnes: [Int]? = nil
    ) async throws -> JobPlanifie {
        let fmt = ISO8601DateFormatter()
        var body: [String: Any] = [
            "modele_ollama": modele,
            "langue_source": langueSource.rawValue,
            "langue_cible": langueCible.rawValue,
            "extracteur_pdf": extracteur,
            "executer_a": fmt.string(from: executer_a),
        ]
        if let md = cheminMd { body["chemin_md"] = md } else if let pdf = cheminPdf { body["chemin_pdf"] = pdf }
        if let chapitres = chapitresSelectionnes, !chapitres.isEmpty {
            body["chapitres_selectionnes"] = chapitres
        }
        let data = try await postAny("schedule", body: body)
        return try decoder.decode(JobPlanifie.self, from: data)
    }

    func planifierBatch(
        chemins: [String],
        modele: String,
        langueSource: Langue,
        langueCible: Langue,
        extracteur: String,
        executerA: Date
    ) async throws -> [JobPlanifie] {
        let fmt = ISO8601DateFormatter()
        let body: [String: Any] = [
            "chemins": chemins,
            "modele_ollama": modele,
            "langue_source": langueSource.rawValue,
            "langue_cible": langueCible.rawValue,
            "extracteur_pdf": extracteur,
            "executer_a": fmt.string(from: executerA),
        ]
        let data = try await postAny("schedule/batch", body: body)
        if let rep = try? decoder.decode(JobsPlanifiesResponse.self, from: data) {
            return rep.jobs
        }
        // Le backend renvoie {"detail": …} en cas d'erreur (ex. fichier introuvable)
        if let err = try? decoder.decode(APIDetailErreur.self, from: data) {
            throw NSError(domain: "APIService", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: err.detail])
        }
        throw NSError(domain: "APIService", code: 2,
                      userInfo: [NSLocalizedDescriptionKey: "Réponse inattendue du backend."])
    }

    func jobsPlanifies() async throws -> [JobPlanifie] {
        let rep: JobsPlanifiesResponse = try await get("scheduled")
        return rep.jobs
    }

    func tousJobsPlanifies() async throws -> [JobPlanifie] {
        let rep: JobsPlanifiesResponse = try await get("scheduled/tous")
        return rep.jobs
    }

    func glossaire() async throws -> [String] {
        let rep: GlossaireResponse = try await get("glossaire")
        return rep.termes
    }

    func sauvegarderGlossaire(termes: [String]) async throws -> [String] {
        var req = URLRequest(url: base.appendingPathComponent("glossaire"))
        req.httpMethod = "PUT"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: ["termes": termes])
        let (data, _) = try await URLSession.shared.data(for: req)
        let rep = try decoder.decode(GlossaireResponse.self, from: data)
        return rep.termes
    }

    // MARK: - Text-to-Speech

    func moteursTts() async throws -> [MoteurTTS] {
        let rep: MoteursTTSResponse = try await get("tts/moteurs")
        return rep.moteurs
    }

    /// Synthétise un court extrait et retourne les octets WAV (à jouer via AVAudioPlayer).
    func ecouterExtrait(texte: String, moteur: String, voix: String) async throws -> Data {
        let data = try await postAny("tts/extrait", body: [
            "texte": texte, "moteur": moteur, "voix": voix,
        ])
        // Une réponse JSON signale une erreur ; un WAV commence par « RIFF »
        if data.starts(with: Array("RIFF".utf8)) {
            return data
        }
        if let err = try? decoder.decode(APIDetailErreur.self, from: data) {
            throw NSError(domain: "APIService", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: err.detail])
        }
        throw NSError(domain: "APIService", code: 2,
                      userInfo: [NSLocalizedDescriptionKey: "Réponse audio inattendue."])
    }

    func genererAudio(cheminMd: String, moteur: String, voix: String) async throws -> TTSGenerationResponse {
        let data = try await postAny("tts", body: [
            "chemin_md": cheminMd, "moteur": moteur, "voix": voix,
        ])
        if let rep = try? decoder.decode(TTSGenerationResponse.self, from: data) {
            return rep
        }
        if let err = try? decoder.decode(APIDetailErreur.self, from: data) {
            throw NSError(domain: "APIService", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: err.detail])
        }
        throw NSError(domain: "APIService", code: 2,
                      userInfo: [NSLocalizedDescriptionKey: "Réponse inattendue du backend."])
    }

    func statutAudio(cheminMd: String) async throws -> EtatAudio? {
        var components = URLComponents(url: base.appendingPathComponent("tts/statut"), resolvingAgainstBaseURL: false)!
        components.queryItems = [URLQueryItem(name: "chemin_md", value: cheminMd)]
        let (data, _) = try await URLSession.shared.data(from: components.url!)
        if data.isEmpty || data == Data("null".utf8) { return nil }
        return try decoder.decode(EtatAudio.self, from: data)
    }

    func annulerJobPlanifie(id: String) async throws {
        var req = URLRequest(url: base.appendingPathComponent("scheduled/\(id)"))
        req.httpMethod = "DELETE"
        _ = try await URLSession.shared.data(for: req)
    }

    func annulerJob(jobId: String) async throws {
        var req = URLRequest(url: base.appendingPathComponent("job/\(jobId)/annuler"))
        req.httpMethod = "POST"
        _ = try await URLSession.shared.data(for: req)
    }

    func statutJob(jobId: String, cheminPdf: String) async throws -> EtatJob {
        var components = URLComponents(url: base.appendingPathComponent("job/\(jobId)/statut"), resolvingAgainstBaseURL: false)!
        components.queryItems = [URLQueryItem(name: "chemin_pdf", value: cheminPdf)]
        let (data, _) = try await URLSession.shared.data(from: components.url!)
        return try decoder.decode(EtatJob.self, from: data)
    }

    // MARK: - Bibliothèque & fiche d'étude (refonte Workflow)

    func bibliotheque() async throws -> [DocumentBiblio] {
        let rep: BibliothequeResponse = try await get("bibliotheque")
        return rep.documents
    }

    func chapitreContenu(cheminMd: String, index: Int) async throws -> ChapitreContenu {
        let data = try await postAny("chapitres/contenu", body: [
            "chemin_md": cheminMd, "index": index,
        ])
        if let rep = try? decoder.decode(ChapitreContenu.self, from: data) {
            return rep
        }
        if let err = try? decoder.decode(APIDetailErreur.self, from: data) {
            throw NSError(domain: "APIService", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: err.detail])
        }
        throw NSError(domain: "APIService", code: 2,
                      userInfo: [NSLocalizedDescriptionKey: "Réponse inattendue du backend."])
    }

    /// Enfile la génération points clés + questions d'un ou plusieurs chapitres.
    func genererEtude(cheminMd: String, chapitres: [Int], modele: String,
                      langueFiche: String, nbPoints: Int = 5, nbQuestions: Int = 3) async throws {
        let data = try await postAny("etude", body: [
            "chemin_md": cheminMd,
            "chapitres_selectionnes": chapitres,
            "modele_ollama": modele,
            "langue_fiche": langueFiche,
            "nb_points": nbPoints,
            "nb_questions": nbQuestions,
        ])
        if let err = try? decoder.decode(APIDetailErreur.self, from: data) {
            throw NSError(domain: "APIService", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: err.detail])
        }
    }

    func etudeStatut(cheminSource: String) async throws -> EtatJobEtude? {
        var components = URLComponents(url: base.appendingPathComponent("etude/statut"), resolvingAgainstBaseURL: false)!
        components.queryItems = [URLQueryItem(name: "chemin_source", value: cheminSource)]
        let (data, _) = try await URLSession.shared.data(from: components.url!)
        if data.isEmpty || data == Data("null".utf8) { return nil }
        return try decoder.decode(EtatJobEtude.self, from: data)
    }

    func featureFlags() async throws -> [String: Bool] {
        let (data, _) = try await URLSession.shared.data(from: base.appendingPathComponent("feature-flags"))
        return (try? decoder.decode([String: Bool].self, from: data)) ?? [:]
    }

    /// Capture d'intérêt pour une fonctionnalité en développement (teasers).
    func manifesterInteret(fonctionnalite: String, email: String) async throws {
        let data = try await postAny("interet", body: [
            "fonctionnalite": fonctionnalite, "email": email,
        ])
        if let err = try? decoder.decode(APIDetailErreur.self, from: data) {
            throw NSError(domain: "APIService", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: err.detail])
        }
    }

    func pauseJob(jobId: String) async throws {
        var req = URLRequest(url: base.appendingPathComponent("job/\(jobId)/pause"))
        req.httpMethod = "POST"
        _ = try await URLSession.shared.data(for: req)
    }

    // MARK: - Helpers

    private func get<T: Decodable>(_ path: String) async throws -> T {
        let (data, _) = try await URLSession.shared.data(from: base.appendingPathComponent(path))
        return try decoder.decode(T.self, from: data)
    }

    private func post(_ path: String, body: [String: String]) async throws -> Data {
        var req = URLRequest(url: base.appendingPathComponent(path))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(body)
        let (data, _) = try await URLSession.shared.data(for: req)
        return data
    }

    private func postAny(_ path: String, body: [String: Any]) async throws -> Data {
        var req = URLRequest(url: base.appendingPathComponent(path))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, _) = try await URLSession.shared.data(for: req)
        return data
    }
}
