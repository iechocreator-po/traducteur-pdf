import SwiftUI

// ============================================================================
// « Vos traductions » — la liste PERSISTANTE des documents du registre.
//
// Corrige F5, l'écart web ↔ macOS le plus coûteux de l'audit du 27/7 : le lot
// d'import macOS vivait EN MÉMOIRE SEULEMENT. Fermer l'app effaçait la seule vue
// sur les traductions en cours ; le seul retour possible était d'aller taper le
// chemin absolu du document dans le Laboratoire, lui-même masqué hors mode
// avancé. Le travail survivait sur le disque, l'accès au travail non.
//
// Principe cible ④ — le client ne détient jamais d'état de job : cette vue est
// une simple projection de `GET /api/bibliotheque`. Rien n'est mémorisé ici, donc
// rien ne peut diverger de ce que le backend sait, ni disparaître à la fermeture.
// ============================================================================

@MainActor
final class VosTraductionsViewModel: ObservableObject {
    @Published var documents: [DocumentBiblio] = []
    @Published var erreur: String? = nil
    @Published var chargement = false

    private var pollTask: Task<Void, Never>? = nil

    /// Documents à afficher : tout le registre, du plus récent au plus ancien.
    /// Le backend trie déjà — on ne retrie pas, sinon les deux clients
    /// afficheraient un ordre différent pour la même donnée.
    var aDesJobsActifs: Bool { documents.contains { $0.estActif } }

    func rafraichir() async {
        chargement = documents.isEmpty
        do {
            documents = try await APIService.shared.bibliotheque()
            erreur = nil
        } catch let e as APIErreur {
            // F4 : le message du backend est desormais lisible, remédiation comprise.
            erreur = e.errorDescription
        } catch {
            erreur = error.localizedDescription
        }
        chargement = false
        armerPoll()
    }

    /// Poll tant qu'un document bouge, arrêté dès que tout est au repos.
    ///
    /// macOS n'a pas d'`EventSource` : le flux SSE de la phase 6 est propre au
    /// web. On garde donc le poll ici, mais BORNÉ par une condition d'arrêt —
    /// c'est précisément ce qui manquait aux boucles du web avant la phase 6.
    private func armerPoll() {
        pollTask?.cancel()
        guard aDesJobsActifs else { pollTask = nil; return }
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(2))
                guard let self, !Task.isCancelled else { return }
                guard let docs = try? await APIService.shared.bibliotheque() else { continue }
                self.documents = docs
                if !self.aDesJobsActifs { return }
            }
        }
    }

    func mettreEnPause(_ doc: DocumentBiblio) async {
        guard let jobId = doc.jobId else { return }
        do {
            try await APIService.shared.pauseJob(jobId: jobId)
            await rafraichir()
        } catch let e as APIErreur {
            erreur = e.errorDescription
        } catch {
            erreur = error.localizedDescription
        }
    }

    func reprendre(_ doc: DocumentBiblio, env: AppEnvironment) async {
        do {
            // F6 : le modèle D'ORIGINE du document, jamais le menu courant.
            // `build_output_path()` dérive le nom de sortie de `modele[:2]` :
            // reprendre avec un autre modèle écrirait dans un fichier neuf
            // pendant que le moteur continue dans l'ancien.
            _ = try await APIService.shared.traduire(
                cheminPdf: estMarkdown(doc.cheminSource) ? nil : doc.cheminSource,
                cheminMd: estMarkdown(doc.cheminSource) ? doc.cheminSource : nil,
                modele: doc.modele,
                langueSource: Langue(rawValue: doc.langueSource) ?? env.langueSource,
                langueCible: Langue(rawValue: doc.langueCible) ?? env.langueCible,
                extracteur: env.extracteurChoisi,
                resume: true
            )
            await rafraichir()
        } catch let e as APIErreur {
            erreur = e.errorDescription
        } catch {
            erreur = error.localizedDescription
        }
    }

    func arreter() {
        pollTask?.cancel()
        pollTask = nil
    }
}

struct VosTraductionsView: View {
    @EnvironmentObject private var env: AppEnvironment
    @StateObject private var vm = VosTraductionsViewModel()

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Vos traductions")
                    .font(.headline)
                Spacer()
                Button {
                    Task { await vm.rafraichir() }
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .buttonStyle(.borderless)
                .help("Rafraîchir la liste")
            }

            if let erreur = vm.erreur {
                Text(erreur)
                    .font(.caption)
                    .foregroundStyle(DS.red)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if vm.chargement {
                ProgressView().controlSize(.small)
            } else if vm.documents.isEmpty {
                Text("Aucune traduction pour l'instant.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(vm.documents) { doc in
                    ligne(doc)
                    if doc.id != vm.documents.last?.id { Divider() }
                }
            }
        }
        .task { await vm.rafraichir() }
        .onDisappear { vm.arreter() }
    }

    @ViewBuilder
    private func ligne(_ doc: DocumentBiblio) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                Text(estMarkdown(doc.cheminSource) ? "MD" : "PDF")
                    .font(.caption2.weight(.semibold))
                    .padding(.horizontal, 5).padding(.vertical, 1)
                    .background(DS.accent.opacity(0.15))
                    .clipShape(RoundedRectangle(cornerRadius: DS.radiusSm))
                Text(doc.nom)
                    .font(.callout)
                    .lineLimit(1)
                    .truncationMode(.middle)
                Spacer()
                Text(libelleStatut(doc))
                    .font(.caption)
                    .foregroundStyle(couleurStatut(doc.statut))
            }

            if doc.estActif && doc.totalSections > 0 {
                ProgressView(value: doc.progression)
                    .progressViewStyle(.linear)
            }

            HStack(spacing: 12) {
                // « Pause » n'a de sens que sur un job qui tourne, et exige un
                // job_id — absent des traductions d'avant le registre.
                if doc.estActif, doc.jobId != nil {
                    Button("Pause") { Task { await vm.mettreEnPause(doc) } }
                        .buttonStyle(.borderless)
                        .font(.caption)
                }
                if doc.estReprenable {
                    Button("⏯ Reprendre") { Task { await vm.reprendre(doc, env: env) } }
                        .buttonStyle(.borderless)
                        .font(.caption)
                }
                Spacer()
            }
        }
        .padding(.vertical, 4)
    }

    private func libelleStatut(_ doc: DocumentBiblio) -> String {
        switch doc.statut {
        case "en_cours":
            return doc.totalSections > 0
                ? "\(doc.sectionsCompletees)/\(doc.totalSections)"
                : "en cours"
        case "en_attente": return "en file"
        case "en_pause":   return "en pause"
        case "termine":    return "terminé"
        case "erreur":     return "erreur"
        case "annule":     return "annulé"
        default:           return doc.statut
        }
    }

    private func couleurStatut(_ statut: String) -> Color {
        switch statut {
        case "termine":            return DS.green
        case "erreur":             return DS.red
        case "en_pause", "annule": return DS.amber
        default:                   return .secondary
        }
    }
}
