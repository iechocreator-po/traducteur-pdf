import Foundation

/// Launches the FastAPI backend (uvicorn) as a child process and stops it on quit.
final class BackendLauncher {
    static let shared = BackendLauncher()
    private var process: Process?

    func start() {
        guard process == nil else { return }

        // Locate the project root relative to the app bundle
        let appDir = Bundle.main.bundleURL
            .deletingLastPathComponent()   // .app
            .deletingLastPathComponent()   // macos-app/itraducteur-pdf
            .deletingLastPathComponent()   // macos-app
            .deletingLastPathComponent()   // 2000_DigitalProducts
        let backendPath = appDir
            .appendingPathComponent("traducteur-pdf")
            .appendingPathComponent("backend")
            .path

        // Find the venv python
        let python = backendPath + "/venv/bin/python3"
        let uvicorn = backendPath + "/venv/bin/uvicorn"

        guard FileManager.default.fileExists(atPath: uvicorn) else {
            print("[BackendLauncher] uvicorn not found at \(uvicorn) — start backend manually.")
            return
        }

        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = ["-m", "uvicorn", "app.main:app", "--port", "8000", "--no-reload"]
        p.currentDirectoryURL = URL(fileURLWithPath: backendPath)
        p.environment = ProcessInfo.processInfo.environment

        do {
            try p.run()
            process = p
            print("[BackendLauncher] Backend démarré (PID \(p.processIdentifier))")
        } catch {
            print("[BackendLauncher] Erreur au démarrage : \(error)")
        }
    }

    func stop() {
        process?.terminate()
        process = nil
    }
}
