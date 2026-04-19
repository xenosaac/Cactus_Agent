import Foundation

protocol TaskParsingService {
    func parseTask(from transcript: String) async -> TaskMetadata
}

struct HeuristicTaskParsingService: TaskParsingService {
    func parseTask(from transcript: String) async -> TaskMetadata {
        let normalized = transcript
            .replacingOccurrences(of: AppConfig.wakeWord, with: "", options: [.caseInsensitive, .diacriticInsensitive])
            .trimmingCharacters(in: .whitespacesAndNewlines)

        let cleaned = normalized
            .replacingOccurrences(of: #"^(please|can you|could you|would you|hey)\s+"#, with: "", options: .regularExpression)
            .replacingOccurrences(of: #"\s+"#, with: " ", options: .regularExpression)
            .trimmingCharacters(in: .whitespacesAndNewlines)

        let fallbackTask = cleaned.isEmpty ? "Follow up on the latest request" : cleaned
        let taskName = clippedTaskName(from: fallbackTask)
        let spokenConfirmation = "Got it. Task: \(taskName)."

        return TaskMetadata(
            rawTranscript: transcript,
            taskName: taskName,
            status: .pending,
            spokenConfirmation: spokenConfirmation
        )
    }

    private func clippedTaskName(from transcript: String) -> String {
        let trimmed = transcript.trimmingCharacters(in: CharacterSet(charactersIn: " .,!?:;"))
        let words = trimmed.split(separator: " ")
        guard words.count > 8 else {
            return trimmed.capitalizedSentence()
        }

        return words.prefix(8).joined(separator: " ").capitalizedSentence()
    }
}

private extension String {
    func capitalizedSentence() -> String {
        guard let first else { return self }
        return String(first).uppercased() + dropFirst()
    }
}
