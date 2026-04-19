import AppKit
import SwiftUI

@MainActor
final class OverlayWindowController {
    private let controller: VoiceAssistantController
    private lazy var bubblePanel = makePanel(size: AppConfig.bubbleSize)
    private lazy var taskPanel = makePanel(size: AppConfig.tabSize)

    init(controller: VoiceAssistantController) {
        self.controller = controller
        bubblePanel.contentViewController = NSHostingController(rootView: AssistantBubbleView(controller: controller))
        taskPanel.contentViewController = NSHostingController(rootView: taskView())
    }

    func refreshTaskContent() {
        taskPanel.contentViewController = NSHostingController(rootView: taskView())
    }

    func showBubble() {
        positionBubble()
        taskPanel.orderOut(nil)
        bubblePanel.orderFrontRegardless()
    }

    func hideBubble() {
        bubblePanel.orderOut(nil)
    }

    func showTaskTab() {
        refreshTaskContent()
        positionTaskTab()
        taskPanel.orderFrontRegardless()
    }

    func hideTaskTab() {
        taskPanel.orderOut(nil)
    }

    private func makePanel(size: CGSize) -> NSPanel {
        let panel = NSPanel(
            contentRect: NSRect(origin: .zero, size: size),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        panel.isFloatingPanel = true
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
        panel.backgroundColor = .clear
        panel.isOpaque = false
        panel.hasShadow = false
        panel.hidesOnDeactivate = false
        panel.ignoresMouseEvents = true
        return panel
    }

    private func taskView() -> some View {
        Group {
            if let task = controller.currentTask {
                TaskTabView(task: task)
            } else {
                EmptyView()
            }
        }
    }

    private func positionBubble() {
        guard let screen = NSScreen.main else { return }
        let frame = screen.visibleFrame
        let origin = CGPoint(
            x: frame.maxX - AppConfig.bubbleSize.width - AppConfig.bubbleTrailingMargin,
            y: frame.minY + AppConfig.bubbleBottomMargin
        )
        bubblePanel.setFrameOrigin(origin)
    }

    private func positionTaskTab() {
        guard let screen = NSScreen.main else { return }
        let frame = screen.visibleFrame
        let origin = CGPoint(
            x: frame.maxX - AppConfig.tabSize.width - AppConfig.tabTrailingMargin,
            y: frame.midY - AppConfig.tabVerticalOffset
        )
        taskPanel.setFrameOrigin(origin)
    }
}
