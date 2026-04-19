import Foundation

struct CactusEnvironment: Sendable {
    let executablePath: String?
    let isInstalled: Bool
    let modelPath: String?
    let suggestedSetup: String

    static func probe() -> CactusEnvironment {
        let candidates = [
            "/opt/homebrew/bin/cactus",
            "/usr/local/bin/cactus"
        ]

        let executable = candidates.first(where: { FileManager.default.isExecutableFile(atPath: $0) })
        let modelCandidates = [
            "/opt/homebrew/opt/cactus/libexec/weights/parakeet-tdt-0.6b-v3",
            "/usr/local/opt/cactus/libexec/weights/parakeet-tdt-0.6b-v3"
        ]
        let modelPath = modelCandidates.first(where: { FileManager.default.fileExists(atPath: $0) })
        return CactusEnvironment(
            executablePath: executable,
            isInstalled: executable != nil,
            modelPath: modelPath,
            suggestedSetup: "brew install cactus-compute/cactus/cactus && cactus download nvidia/parakeet-tdt-0.6b-v3"
        )
    }
}

final class CactusTranscriptionService {
    private let environment: CactusEnvironment

    init(environment: CactusEnvironment = .probe()) {
        self.environment = environment
    }

    func environmentStatus() -> CactusEnvironment {
        environment
    }
}
