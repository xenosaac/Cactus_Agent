import Foundation

enum VoiceAssistantState: Equatable {
    case idle
    case wakeListening
    case activeListening
    case parsing
    case speakingConfirmation
    case collapsedTaskTab
    case error(String)
}
