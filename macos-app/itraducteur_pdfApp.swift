import SwiftUI

@main
struct itraducteur_pdfApp: App {
    init() {
        BackendLauncher.shared.start()
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
                .tint(DS.accent) // accent du design system, propagé à tous les contrôles
        }
        .commands {
            CommandGroup(replacing: .appInfo) {
                Button("À propos de iTraducteur PDF") {
                    NSApp.orderFrontStandardAboutPanel(nil)
                }
            }
        }
    }
}
