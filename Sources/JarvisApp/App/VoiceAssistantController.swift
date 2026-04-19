import Foundation
import SwiftUI

@MainActor
final class VoiceAssistantController: ObservableObject {
    static let shared = VoiceAssistantController()

    @Published private(set) var state: VoiceAssistantState = .idle
    @Published private(set) var currentTask: TaskMetadata?
    @Published private(set) var liveTranscript = ""
    @Published private(set) var menuStatusText = "Starting..."
    @Published private(set) var isRunning = false
    @Published private(set) var levelBarWidth: CGFloat = 42

    var titleText: String {
        switch state {
        case .idle, .wakeListening:
            return "Say Cactus"
        case .activeListening:
            return "Listening"
        case .parsing:
            return "Parsing Task"
        case .speakingConfirmation:
            return "Confirming"
        case .collapsedTaskTab:
            return "Task Ready"
        case let .error(message):
            return message
        }
    }

    var subtitleText: String {
        switch state {
        case .idle, .wakeListening:
            return "Waiting for the wake word"
        case .activeListening:
            return "Speak your command"
        case .parsing:
            return "Turning your words into a task"
        case .speakingConfirmation:
            return "Reading your task back"
        case .collapsedTaskTab:
            return "Pinned to the right edge"
        case .error:
            return "Check microphone and speech permissions"
        }
    }

    var liveTranscriptDisplay: String {
        if liveTranscript.isEmpty {
            return state == .idle || state == .wakeListening ? "Cactus is listening quietly in the background." : "Listening for your task..."
        }
        return liveTranscript
    }

    private let audioCaptureService = AudioCaptureService()
    private let cactusService = CactusTranscriptionService()
    private lazy var liveTranscriptionService = LiveTranscriptionService(
        audioCaptureService: audioCaptureService,
        environment: cactusService.environmentStatus()
    )
    private let parser: TaskParsingService
    private let speechFeedbackService = SpeechFeedbackService()
    private var overlayWindowController: OverlayWindowController?
    private var silenceTask: Task<Void, Never>?
    private var lastCommandText = ""

    init(parser: TaskParsingService = HeuristicTaskParsingService()) {
        self.parser = parser
    }

    func start() {
        guard !isRunning else { return }
        overlayWindowController = OverlayWindowController(controller: self)

        Task {
            await requestPermissionsAndStart()
        }
    }

    func restart() {
        stopListening()
        currentTask = nil
        overlayWindowController?.hideTaskTab()
        start()
    }

    private func requestPermissionsAndStart() async {
        let micGranted = await audioCaptureService.requestMicrophonePermission()
        let speechGranted = await liveTranscriptionService.requestSpeechPermission()

        guard micGranted, speechGranted else {
            state = .error("Permissions Required")
            liveTranscript = "Cactus needs microphone permission to work."
            menuStatusText = "Permissions missing"
            overlayWindowController?.showBubble()
            return
        }

        let cactus = cactusService.environmentStatus()
        menuStatusText = cactus.modelPath != nil ? "Listening with Cactus transcription" : "Cactus model missing"
        isRunning = true
        await startWakeListening()
    }

    private func startWakeListening() async {
        state = .wakeListening
        liveTranscript = ""
        lastCommandText = ""
        overlayWindowController?.hideBubble()

        do {
            try liveTranscriptionService.start(mode: .wakeWord) { [weak self] snapshot in
                self?.handleWakeTranscript(snapshot)
            } onFailure: { [weak self] message in
                self?.setError(message)
            }
        } catch {
            setError(error.localizedDescription)
        }
    }

    private func handleWakeTranscript(_ snapshot: TranscriptSnapshot) {
        let combined = snapshot.combined
        liveTranscript = combined
        menuStatusText = combined.isEmpty ? "Listening for 'cactus'" : "Heard: \(combined)"

        if wakeWordDetected(in: combined), state == .wakeListening {
            activateAssistant()
        }
    }

    private func activateAssistant() {
        state = .activeListening
        liveTranscript = ""
        lastCommandText = ""
        menuStatusText = "Wake word detected"
        overlayWindowController?.showBubble()
        startCommandListening()
    }

    private func startCommandListening() {
        silenceTask?.cancel()

        do {
            try liveTranscriptionService.start(mode: .command) { [weak self] snapshot in
                self?.handleCommandTranscript(snapshot)
            } onFailure: { [weak self] message in
                self?.setError(message)
            }
        } catch {
            setError(error.localizedDescription)
        }
    }

    private func handleCommandTranscript(_ snapshot: TranscriptSnapshot) {
        let commandText = cleanedCommandText(from: snapshot.combined)
        liveTranscript = commandText
        menuStatusText = commandText.isEmpty ? "Listening for command" : "Command: \(commandText)"
        levelBarWidth = max(42, min(AppConfig.bubbleSize.width - 48, CGFloat(commandText.count * 8)))

        guard !commandText.isEmpty else { return }

        let normalizedCommandText = normalizedTranscript(commandText)
        let transcriptChanged = normalizedCommandText != normalizedTranscript(lastCommandText)
        let textGrew = normalizedCommandText.count > normalizedTranscript(lastCommandText).count

        if transcriptChanged {
            lastCommandText = commandText
        }

        if transcriptChanged || textGrew {
            resetSilenceTimer()
        }
    }

    private func resetSilenceTimer() {
        silenceTask?.cancel()
        silenceTask = Task { [weak self] in
            try? await Task.sleep(for: .seconds(AppConfig.silenceTimeout))
            guard !Task.isCancelled else { return }
            await self?.finalizeCurrentCommand()
        }
    }

    private func finalizeCurrentCommand() async {
        guard !liveTranscript.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            await startWakeListening()
            return
        }

        liveTranscriptionService.stop()
        state = .parsing
        overlayWindowController?.showBubble()

        let parsed = await parser.parseTask(from: liveTranscript)
        currentTask = parsed
        overlayWindowController?.showTaskTab()
        overlayWindowController?.hideBubble()

        speechFeedbackService.speak(parsed.spokenConfirmation) { [weak self] in
            guard let self else { return }
            self.state = .speakingConfirmation
            self.currentTask = parsed.withStatus(.completed)
            self.overlayWindowController?.showTaskTab()
        } onFinish: { [weak self] in
            guard let self else { return }
            self.state = .collapsedTaskTab
            Task {
                await self.startWakeListening()
            }
        }
    }

    private func cleanedCommandText(from transcript: String) -> String {
        var cleaned = transcript

        for alias in AppConfig.wakeWordAliases {
            cleaned = cleaned.replacingOccurrences(of: alias, with: "", options: [.caseInsensitive, .diacriticInsensitive])
        }

        return cleaned
            .replacingOccurrences(of: #"\s+"#, with: " ", options: .regularExpression)
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func normalizedTranscript(_ transcript: String) -> String {
        transcript
            .lowercased()
            .replacingOccurrences(of: #"[^a-z0-9\s]"#, with: " ", options: .regularExpression)
            .replacingOccurrences(of: #"\s+"#, with: " ", options: .regularExpression)
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func wakeWordDetected(in transcript: String) -> Bool {
        let normalizedWords = transcript
            .lowercased()
            .replacingOccurrences(of: #"[^a-z\s]"#, with: " ", options: .regularExpression)
            .split(separator: " ")
            .map(String.init)

        for word in normalizedWords {
            if AppConfig.wakeWordAliases.contains(word) {
                return true
            }

            if editDistance(word, AppConfig.wakeWord) <= 1 {
                return true
            }
        }

        return false
    }

    private func editDistance(_ lhs: String, _ rhs: String) -> Int {
        let lhsChars = Array(lhs)
        let rhsChars = Array(rhs)

        guard !lhsChars.isEmpty else { return rhsChars.count }
        guard !rhsChars.isEmpty else { return lhsChars.count }

        var previous = Array(0...rhsChars.count)

        for (i, lhsChar) in lhsChars.enumerated() {
            var current = [i + 1]

            for (j, rhsChar) in rhsChars.enumerated() {
                let substitutionCost = lhsChar == rhsChar ? 0 : 1
                current.append(
                    min(
                        current[j] + 1,
                        previous[j + 1] + 1,
                        previous[j] + substitutionCost
                    )
                )
            }

            previous = current
        }

        return previous[rhsChars.count]
    }

    private func setError(_ message: String) {
        stopListening()
        state = .error("Listening Failed")
        liveTranscript = message
        menuStatusText = "Error"
        overlayWindowController?.showBubble()
    }

    private func stopListening() {
        silenceTask?.cancel()
        liveTranscriptionService.stop()
        audioCaptureService.stop()
        isRunning = false
    }
}
