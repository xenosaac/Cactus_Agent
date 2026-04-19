import Foundation

enum TaskStatus: String, Codable {
    case pending = "Pending"
    case completed = "Completed"
}

struct TaskMetadata: Identifiable, Equatable, Codable {
    let id: UUID
    let rawTranscript: String
    let taskName: String
    let status: TaskStatus
    let spokenConfirmation: String
    let createdAt: Date

    init(
        id: UUID = UUID(),
        rawTranscript: String,
        taskName: String,
        status: TaskStatus,
        spokenConfirmation: String,
        createdAt: Date = .now
    ) {
        self.id = id
        self.rawTranscript = rawTranscript
        self.taskName = taskName
        self.status = status
        self.spokenConfirmation = spokenConfirmation
        self.createdAt = createdAt
    }

    func withStatus(_ status: TaskStatus) -> TaskMetadata {
        TaskMetadata(
            id: id,
            rawTranscript: rawTranscript,
            taskName: taskName,
            status: status,
            spokenConfirmation: spokenConfirmation,
            createdAt: createdAt
        )
    }
}
