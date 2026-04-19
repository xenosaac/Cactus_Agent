import SwiftUI

struct ControlPanelView: View {
    @ObservedObject var controller: VoiceAssistantController

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Cactus")
                .font(.headline)

            Text(controller.menuStatusText)
                .font(.subheadline)
                .foregroundStyle(.secondary)

            Text(controller.titleText)
                .font(.caption)
                .foregroundStyle(.secondary)

            Text(controller.liveTranscriptDisplay)
                .font(.caption)
                .lineLimit(4)

            if let task = controller.currentTask {
                Divider()
                Text(task.status.rawValue)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Text(task.taskName)
                    .font(.body)
            }

            Divider()

            Button(controller.isRunning ? "Restart Listening" : "Start Listening") {
                controller.restart()
            }

            Button("Quit") {
                NSApplication.shared.terminate(nil)
            }
        }
        .padding(14)
        .frame(width: 260)
    }
}
