import Foundation

actor APIService {
    static let shared = APIService()
    private let base = URL(string: "http://localhost:8000/api")!
    private let decoder: JSONDecoder = {
        let d = JSONDecoder()
        return d
    }()
    private let session: URLSession = {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 3600  // 1h max par requête
        config.timeoutIntervalForResource = 3600
        return URLSession(configuration: config)
    }()

    func health() async throws -> HealthResponse {
        try await get("health")
    }

    func modeles() async throws -> ModelesResponse {
        try await get("modeles")
    }

    func checkResume(cheminPdf: String) async throws -> EtatJob? {
        let body = ["chemin_pdf": cheminPdf]
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

    func traduire(cheminPdf: String, modele: String, langueSource: Langue, langueCible: Langue, resume: Bool) async throws -> ResultatTraduction {
        let body: [String: Any] = [
            "chemin_pdf": cheminPdf,
            "modele_ollama": modele,
            "langue_source": langueSource.rawValue,
            "langue_cible": langueCible.rawValue,
            "resume": resume,
        ]
        let data = try await postAny("translate", body: body)
        return try decoder.decode(ResultatTraduction.self, from: data)
    }

    // MARK: - Helpers

    private func get<T: Decodable>(_ path: String) async throws -> T {
        let (data, _) = try await session.data(from: base.appendingPathComponent(path))
        return try decoder.decode(T.self, from: data)
    }

    private func post(_ path: String, body: [String: String]) async throws -> Data {
        var req = URLRequest(url: base.appendingPathComponent(path))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(body)
        let (data, _) = try await session.data(for: req)
        return data
    }

    private func postAny(_ path: String, body: [String: Any]) async throws -> Data {
        var req = URLRequest(url: base.appendingPathComponent(path))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, _) = try await session.data(for: req)
        return data
    }
}
