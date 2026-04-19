import Foundation

enum AppEnvironment {
    private static let compiledSourceRoot: URL = {
        let fileURL = URL(fileURLWithPath: #filePath)
        return fileURL
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
    }()

    private static let cachedValues: [String: String] = {
        let candidatePaths = [
            Bundle.main.infoDictionary?["ELEVENLABS_ENV_PATH"] as? String,
            compiledSourceRoot.appendingPathComponent(".env").path(percentEncoded: false),
            URL(fileURLWithPath: FileManager.default.currentDirectoryPath).appendingPathComponent(".env").path(percentEncoded: false)
        ].compactMap { $0 }

        for path in candidatePaths {
            if let contents = try? String(contentsOfFile: path, encoding: .utf8) {
                return parse(contents: contents)
            }
        }

        return [:]
    }()

    static func value(for key: String) -> String? {
        if let processValue = ProcessInfo.processInfo.environment[key], !processValue.isEmpty {
            return processValue
        }

        if let fileValue = cachedValues[key], !fileValue.isEmpty {
            return fileValue
        }

        return nil
    }

    private static func parse(contents: String) -> [String: String] {
        var result: [String: String] = [:]
        for rawLine in contents.components(separatedBy: .newlines) {
            let line = rawLine.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !line.isEmpty, !line.hasPrefix("#"), let separatorIndex = line.firstIndex(of: "=") else {
                continue
            }

            let key = String(line[..<separatorIndex]).trimmingCharacters(in: .whitespacesAndNewlines)
            let value = String(line[line.index(after: separatorIndex)...]).trimmingCharacters(in: .whitespacesAndNewlines)
            result[key] = value
        }
        return result
    }
}
