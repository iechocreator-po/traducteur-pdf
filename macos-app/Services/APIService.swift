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

    func jobsPlanifies() async throws -> [JobPlanifie] {
        let rep: JobsPlanifiesResponse = try await get("scheduled")
        return rep.jobs
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
