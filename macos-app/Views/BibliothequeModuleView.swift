import SwiftUI
import Combine
import AVFoundation
import AppKit

// ============================================================================
// Module B — « Bibliothèque » : lecture des documents traduits, chapitre par
// chapitre, avec lecture audio (WAV lu directement depuis le disque via
// AVAudioPlayer) et panneau IA « points clés + quiz » servi par le backend
// Étude. La sélection moteur/voix TTS vient du Laboratoire (@AppStorage).
// ============================================================================

/// Ligne de contenu d'un chapitre après parsing Markdown minimal.
struct LigneContenu: Identifiable {
    let id = UUID()
    let estTitre: Bool
    let texte: String
}

@MainActor
final class BibliothequeViewModel: ObservableObject {
    @Published var docs: [DocumentBiblio] = []
    @Published var docActif: DocumentBiblio? = nil
    @Published var chapitres: [Chapitre] = []
    @Published var chapActif: Chapitre? = nil
    @Published var lignesContenu: [LigneContenu] = []
    @Published var messageLecture: String? = nil

    // Panneau IA
    @Published var fiches: [Int: FicheChapitre] = [:]
    @Published var iaEnCours = false
    @Published var iaStatut: String? = nil

    // Audio
    @Published var cheminWav: String? = nil
    @Published var audioEnLecture = false
    @Published var audioPosition: Double = 0
    @Published var audioDuree: Double = 0
    @Published var audioStatut: String? = nil
    @Published var audioGenerationEnCours = false

    private var lecteur: AVAudioPlayer? = nil
    private var timerAudio: Task<Void, Never>? = nil
    private var pollFiche: Task<Void, Never>? = nil
    private var pollAudio: Task<Void, Never>? = nil

    // MARK: - Documents

    func chargerDocs() async {
        docs = (try? await APIService.shared.bibliotheque()) ?? []
        // Rafraîchit le document actif (sa progression peut avoir changé)
        if let actif = docActif {
            docActif = docs.first { $0.cheminSortie == actif.cheminSortie } ?? actif
        }
    }

    func selectionner(doc: DocumentBiblio) async {
        guard doc.estTermine else { return }
        docActif = doc
        chapActif = nil
        lignesContenu = []
        fiches = [:]
        iaStatut = nil
        pollFiche?.cancel()
        arreterAudio()

        let res = try? await APIService.shared.chapitres(cheminMd: doc.cheminSortie, extracteur: "pymupdf4llm")
        chapitres = res?.chapitres ?? []
        if let premier = chapitres.first {
            await selectionner(chapitre: premier)
        } else {
            messageLecture = "Ce document ne contient aucun titre de chapitre — utilise la lecture audio ou ouvre le fichier :\n\(doc.cheminSortie)"
        }

        await chargerFicheExistante()
        await rafraichirAudio()
    }

    func selectionner(chapitre: Chapitre) async {
        guard let doc = docActif else { return }
        chapActif = chapitre
        messageLecture = nil
        do {
            let contenu = try await APIService.shared.chapitreContenu(cheminMd: doc.cheminSortie, index: chapitre.index)
            lignesContenu = Self.parserMarkdown(contenu.contenu)
        } catch {
            lignesContenu = []
            messageLecture = "Impossible de charger le chapitre : \(error.localizedDescription)"
        }
    }

    /// Parsing Markdown minimal : titres et paragraphes (le premier titre est
    /// sauté, il est déjà affiché en en-tête de la zone de lecture).
    static func parserMarkdown(_ markdown: String) -> [LigneContenu] {
        var lignes: [LigneContenu] = []
        var paragraphe: [String] = []
        var premierTitreSaute = false

        func viderParagraphe() {
            guard !paragraphe.isEmpty else { return }
            lignes.append(LigneContenu(estTitre: false, texte: paragraphe.joined(separator: " ")))
            paragraphe = []
        }

        for brute in markdown.components(separatedBy: "\n") {
            let ligne = brute.trimmingCharacters(in: .whitespaces)
            if ligne.hasPrefix("#") {
                if !premierTitreSaute {
                    premierTitreSaute = true
                    continue
                }
                viderParagraphe()
                let titre = ligne.drop(while: { $0 == "#" }).trimmingCharacters(in: .whitespaces)
                lignes.append(LigneContenu(estTitre: true, texte: titre))
            } else if ligne.isEmpty {
                viderParagraphe()
            } else {
                paragraphe.append(ligne)
            }
        }
        viderParagraphe()
        return lignes
    }

    // MARK: - Panneau IA (points clés + quiz)

    func chargerFicheExistante() async {
        guard let doc = docActif else { return }
        if let etat = try? await APIService.shared.etudeStatut(cheminSource: doc.cheminSortie) {
            for chap in etat.chapitres where chap.etape == "termine" {
                fiches[chap.index] = chap
            }
            if etat.statut == "en_cours" || etat.statut == "en_attente" {
                demarrerPollFiche()
            }
        }
    }

    func genererFiche(env: AppEnvironment) async {
        guard let doc = docActif, let chap = chapActif else { return }
        guard await env.santeOk() else { return }
        do {
            try await APIService.shared.genererEtude(
                cheminMd: doc.cheminSortie,
                chapitres: [chap.index],
                modele: env.modeleChoisi,
                langueFiche: env.langueCible.rawValue)
            iaEnCours = true
            iaStatut = "⏳ Génération en cours…"
            demarrerPollFiche()
        } catch {
            iaStatut = "❌ \(error.localizedDescription)"
        }
    }

    private func demarrerPollFiche() {
        iaEnCours = true
        pollFiche?.cancel()
        pollFiche = Task { [weak self] in
            while !Task.isCancelled {
                guard let self, let doc = self.docActif else { break }
                guard let etat = try? await APIService.shared.etudeStatut(cheminSource: doc.cheminSortie) else {
                    try? await Task.sleep(for: .seconds(2))
                    continue
                }
                for chap in etat.chapitres where chap.etape == "termine" {
                    self.fiches[chap.index] = chap
                }
                if ["termine", "erreur", "annule"].contains(etat.statut) {
                    self.iaEnCours = false
                    self.iaStatut = etat.statut == "termine" ? nil : "❌ \(etat.erreurs.last ?? "Erreur")"
                    break
                }
                if let enCours = etat.chapitres.first(where: { ["points", "questions"].contains($0.etape) }) {
                    self.iaStatut = enCours.etape == "points" ? "⏳ Points clés en cours…" : "⏳ Questions en cours…"
                } else {
                    self.iaStatut = "⏳ En file d'attente…"
                }
                try? await Task.sleep(for: .seconds(2))
            }
        }
    }

    // MARK: - Audio

    func rafraichirAudio() async {
        pollAudio?.cancel()
        arreterAudio()
        cheminWav = nil
        audioStatut = nil
        audioGenerationEnCours = false
        guard let doc = docActif else { return }
        if let etat = try? await APIService.shared.statutAudio(cheminMd: doc.cheminSortie) {
            if etat.statut == "termine", FileManager.default.fileExists(atPath: etat.cheminSortie) {
                cheminWav = etat.cheminSortie
            } else if etat.statut == "en_cours" || etat.statut == "en_attente" {
                audioGenerationEnCours = true
                demarrerPollAudio()
            }
        }
    }

    func genererAudio(env: AppEnvironment, moteur: String, voix: String) async {
        guard let doc = docActif else { return }
        guard !moteur.isEmpty, !voix.isEmpty else {
            audioStatut = "Choisis d'abord un moteur et une voix dans le Laboratoire."
            return
        }
        do {
            _ = try await APIService.shared.genererAudio(cheminMd: doc.cheminSortie, moteur: moteur, voix: voix)
            audioGenerationEnCours = true
            demarrerPollAudio()
        } catch {
            audioStatut = "❌ \(error.localizedDescription)"
        }
    }

    private func demarrerPollAudio() {
        pollAudio?.cancel()
        pollAudio = Task { [weak self] in
            while !Task.isCancelled {
                guard let self, let doc = self.docActif else { break }
                if let etat = try? await APIService.shared.statutAudio(cheminMd: doc.cheminSortie) {
                    let pct = etat.totalSections > 0
                        ? Int(Double(etat.sectionsCompletees) / Double(etat.totalSections) * 100) : 0
                    switch etat.statut {
                    case "en_attente":
                        self.audioStatut = "⏳ En file d'attente…"
                    case "en_cours":
                        self.audioStatut = "🔊 Génération — \(pct)%"
                    case "termine":
                        self.audioStatut = nil
                        self.audioGenerationEnCours = false
                        self.cheminWav = etat.cheminSortie
                        return
                    default:
                        self.audioStatut = etat.statut == "erreur" ? "❌ \(etat.erreur ?? "Erreur")" : "✕ Annulé"
                        self.audioGenerationEnCours = false
                        return
                    }
                }
                try? await Task.sleep(for: .seconds(2))
            }
        }
    }

    func basculerLecture() {
        if let lecteur {
            if lecteur.isPlaying {
                lecteur.pause()
                audioEnLecture = false
            } else {
                lecteur.play()
                audioEnLecture = true
                demarrerTimerAudio()
            }
            return
        }
        guard let chemin = cheminWav else { return }
        do {
            let l = try AVAudioPlayer(contentsOf: URL(fileURLWithPath: chemin))
            lecteur = l
            audioDuree = l.duration
            l.play()
            audioEnLecture = true
            demarrerTimerAudio()
        } catch {
            audioStatut = "❌ Lecture impossible : \(error.localizedDescription)"
        }
    }

    func chercherPosition(_ secondes: Double) {
        lecteur?.currentTime = secondes
        audioPosition = secondes
    }

    func revelerAudioDansFinder() {
        guard let chemin = cheminWav else { return }
        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: chemin)])
    }

    private func demarrerTimerAudio() {
        timerAudio?.cancel()
        timerAudio = Task { [weak self] in
            while !Task.isCancelled {
                guard let self, let lecteur = self.lecteur else { break }
                self.audioPosition = lecteur.currentTime
                self.audioEnLecture = lecteur.isPlaying
                if !lecteur.isPlaying && lecteur.currentTime == 0 { break }  // fin de lecture
                try? await Task.sleep(for: .milliseconds(400))
            }
        }
    }

    private func arreterAudio() {
        timerAudio?.cancel()
        lecteur?.stop()
        lecteur = nil
        audioEnLecture = false
        audioPosition = 0
        audioDuree = 0
    }
}

// MARK: - Vue

struct BibliothequeModuleView: View {
    @EnvironmentObject private var env: AppEnvironment
    @StateObject private var vm = BibliothequeViewModel()
    @AppStorage("ttsMoteur") private var ttsMoteur: String = ""
    @AppStorage("ttsVoix") private var ttsVoix: String = ""

    var body: some View {
        HStack(spacing: 0) {
            sidebar
                .frame(width: 230)
            Divider()
            zoneLecture
                .frame(maxWidth: .infinity)
            Divider()
            panneauIA
                .frame(width: 300)
        }
        .task { await vm.chargerDocs() }
    }

    // MARK: - Sidebar

    private var sidebar: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                VStack(alignment: .leading, spacing: 4) {
                    titreSection("Document")
                    if vm.docs.isEmpty {
                        Text("Aucun document traduit pour l'instant.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .padding(.horizontal, 8)
                    }
                    ForEach(vm.docs) { doc in
                        boutonDoc(doc)
                    }
                }
                if !vm.chapitres.isEmpty {
                    VStack(alignment: .leading, spacing: 2) {
                        titreSection("Chapitres")
                        ForEach(vm.chapitres) { chap in
                            boutonChapitre(chap)
                        }
                    }
                }
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .background(.background.secondary)
    }

    private func titreSection(_ texte: String) -> some View {
        Text(texte.uppercased())
            .font(.system(size: 10, weight: .bold))
            .foregroundStyle(.secondary)
            .padding(.horizontal, 8)
            .padding(.bottom, 4)
    }

    private func boutonDoc(_ doc: DocumentBiblio) -> some View {
        let actif = vm.docActif?.cheminSortie == doc.cheminSortie
        return Button {
            Task { await vm.selectionner(doc: doc) }
        } label: {
            HStack(spacing: 7) {
                Text(estMarkdown(doc.cheminSource) ? "MD" : "PDF")
                    .font(.system(size: 8.5, weight: .heavy))
                    .padding(.horizontal, 5)
                    .padding(.vertical, 2)
                    .background(DS.accent.opacity(0.14), in: RoundedRectangle(cornerRadius: 4))
                    .foregroundStyle(DS.accent)
                Text(doc.nom)
                    .font(.system(size: 11.5, weight: .semibold))
                    .lineLimit(1)
                    .truncationMode(.middle)
                Spacer(minLength: 0)
                if !doc.estTermine {
                    Text(doc.statut == "en_cours" ? "\(doc.sectionsCompletees)/\(doc.totalSections)" : doc.statut)
                        .font(.system(size: 9, weight: .bold))
                        .padding(.horizontal, 5)
                        .padding(.vertical, 2)
                        .background(DS.amber.opacity(0.15), in: RoundedRectangle(cornerRadius: 4))
                        .foregroundStyle(DS.amber)
                }
            }
            .padding(.horizontal, 8)
            .padding(.vertical, 6)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(actif ? DS.accent.opacity(0.14) : .clear, in: RoundedRectangle(cornerRadius: DS.radiusSm))
            .foregroundStyle(actif ? DS.accent : .secondary)
        }
        .buttonStyle(.plain)
        .help(doc.estTermine ? doc.cheminSortie : "Encore en traduction — lisible une fois terminé")
    }

    private func boutonChapitre(_ chap: Chapitre) -> some View {
        let actif = vm.chapActif?.index == chap.index
        return Button {
            Task { await vm.selectionner(chapitre: chap) }
        } label: {
            Text(chap.titre)
                .font(.system(size: 11.5, weight: .semibold))
                .lineLimit(1)
                .padding(.horizontal, 8)
                .padding(.vertical, 6)
                .padding(.leading, CGFloat((chap.niveau - 1) * 10))
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(actif ? DS.accent.opacity(0.14) : .clear, in: RoundedRectangle(cornerRadius: DS.radiusSm))
                .foregroundStyle(actif ? DS.accent : .secondary)
        }
        .buttonStyle(.plain)
        .help(chap.titre)
    }

    // MARK: - Zone de lecture

    private var zoneLecture: some View {
        VStack(spacing: 0) {
            if let doc = vm.docActif {
                HStack(spacing: 10) {
                    Text(estMarkdown(doc.cheminSource) ? "MD" : "PDF")
                        .font(.system(size: 10, weight: .heavy))
                        .padding(.horizontal, 7)
                        .padding(.vertical, 4)
                        .background(DS.accent.opacity(0.14), in: RoundedRectangle(cornerRadius: 6))
                        .foregroundStyle(DS.accent)
                    Text(doc.nom)
                        .font(.system(size: 14, weight: .bold))
                        .lineLimit(1)
                        .truncationMode(.middle)
                    Spacer()
                    Button {
                        Task { await vm.chargerDocs() }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                    .buttonStyle(.borderless)
                    .help("Rafraîchir la bibliothèque")
                }
                .padding(.horizontal, 28)
                .padding(.vertical, 14)

                ScrollView {
                    VStack(alignment: .leading, spacing: 12) {
                        if let chap = vm.chapActif {
                            Text(chap.titre)
                                .font(.system(size: 21, weight: .heavy))
                        }
                        if let message = vm.messageLecture {
                            Text(message)
                                .font(.callout)
                                .foregroundStyle(.secondary)
                        }
                        ForEach(vm.lignesContenu) { ligne in
                            if ligne.estTitre {
                                Text(ligne.texte)
                                    .font(.system(size: 15, weight: .bold))
                                    .padding(.top, 6)
                            } else {
                                Text(ligne.texte)
                                    .font(.system(size: 13.5))
                                    .lineSpacing(5)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                    .frame(maxWidth: 640, alignment: .leading)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 28)
                    .padding(.bottom, 24)
                }

                Divider()
                barreAudio
            } else {
                Spacer()
                VStack(spacing: 6) {
                    Text("Sélectionne un document dans la colonne de gauche.")
                        .foregroundStyle(.secondary)
                    Text("Les documents apparaissent ici une fois traduits dans « Nouveau document ».")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                }
                .frame(maxWidth: .infinity)
                Spacer()
            }
        }
    }

    // MARK: - Barre audio

    private var barreAudio: some View {
        HStack(spacing: 14) {
            Button {
                vm.basculerLecture()
            } label: {
                Image(systemName: vm.audioEnLecture ? "pause.fill" : "play.fill")
                    .font(.system(size: 12, weight: .bold))
                    .foregroundStyle(.white)
                    .frame(width: 32, height: 32)
                    .background(Circle().fill(vm.cheminWav != nil ? DS.accent : DS.text3))
            }
            .buttonStyle(.plain)
            .disabled(vm.cheminWav == nil)

            Slider(
                value: Binding(
                    get: { vm.audioPosition },
                    set: { vm.chercherPosition($0) }
                ),
                in: 0...max(vm.audioDuree, 1)
            )
            .controlSize(.small)
            .disabled(vm.cheminWav == nil)

            Text(vm.audioDuree > 0
                 ? "\(formaterDureeDouble(vm.audioPosition)) / \(formaterDureeDouble(vm.audioDuree))"
                 : "—")
                .font(.system(size: 10.5, weight: .semibold))
                .foregroundStyle(.secondary)
                .frame(width: 84)

            if vm.cheminWav != nil {
                Button("⬇ Révéler l'audio") { vm.revelerAudioDansFinder() }
                    .buttonStyle(.borderless)
                    .font(.caption)
            } else if !vm.audioGenerationEnCours {
                Button("🔊 Générer l'audio") {
                    Task { await vm.genererAudio(env: env, moteur: ttsMoteur, voix: ttsVoix) }
                }
                .buttonStyle(.borderless)
                .font(.caption)
            }

            if let statut = vm.audioStatut {
                Text(statut)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 10)
        .background(.bar)
    }

    // MARK: - Panneau IA

    private var panneauIA: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                Text("RÉSUMÉ & QUIZ")
                    .font(.system(size: 10, weight: .bold))
                    .foregroundStyle(.secondary)
                Text("Générés par l'IA locale pour le chapitre affiché.")
                    .font(.caption)
                    .foregroundStyle(.tertiary)

                let fiche = vm.chapActif.flatMap { vm.fiches[$0.index] }

                Button(fiche != nil ? "↻ Régénérer points clés + quiz" : "Générer les 5 points clés + quiz") {
                    Task { await vm.genererFiche(env: env) }
                }
                .buttonStyle(.bordered)
                .tint(DS.accent)
                .disabled(vm.chapActif == nil || vm.iaEnCours)
                .frame(maxWidth: .infinity)

                if let statut = vm.iaStatut {
                    Text(statut)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                if let fiche {
                    Text("POINTS CLÉS")
                        .font(.system(size: 10, weight: .bold))
                        .foregroundStyle(.secondary)
                        .padding(.top, 8)
                    ForEach(fiche.points, id: \.self) { point in
                        HStack(alignment: .top, spacing: 8) {
                            Circle()
                                .fill(DS.accent)
                                .frame(width: 5, height: 5)
                                .padding(.top, 5)
                            Text(point)
                                .font(.system(size: 12))
                                .foregroundStyle(.secondary)
                        }
                    }

                    Text("QUIZ")
                        .font(.system(size: 10, weight: .bold))
                        .foregroundStyle(.secondary)
                        .padding(.top, 8)
                    ForEach(Array(fiche.questions.enumerated()), id: \.offset) { i, q in
                        VStack(alignment: .leading, spacing: 6) {
                            Text("Q\(i + 1). \(q.question)")
                                .font(.system(size: 12, weight: .bold))
                            DisclosureGroup("Voir la réponse") {
                                Text(q.reponse)
                                    .font(.system(size: 12))
                                    .foregroundStyle(.secondary)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .padding(.top, 4)
                            }
                            .font(.system(size: 11, weight: .semibold))
                            .tint(DS.accent)
                        }
                        .padding(10)
                        .background(.background.secondary, in: RoundedRectangle(cornerRadius: DS.radiusSm))
                    }
                }
            }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .background(.background.secondary)
    }
}
