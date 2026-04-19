import SwiftUI

@main
struct JarvisApp: App {
    @StateObject private var controller = VoiceAssistantController.shared

    init() {
        NSApplication.shared.setActivationPolicy(.accessory)
        DispatchQueue.main.async {
            VoiceAssistantController.shared.start()
        }
    }

    var body: some Scene {
        MenuBarExtra("Cactus", systemImage: "waveform.circle.fill") {
            ControlPanelView(controller: controller)
        }
        .menuBarExtraStyle(.window)

        Settings {
            EmptyView()
        }
    }
}
