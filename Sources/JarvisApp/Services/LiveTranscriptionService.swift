import AVFoundation
import CactusFFI
import Foundation

struct TranscriptSnapshot: Equatable, Sendable {
    let confirmed: String
    let pending: String

    var combined: String {
        [confirmed, pending]
            .filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
            .joined(separator: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

enum LiveTranscriptionMode: Sendable {
    case wakeWord
    case command
}

private struct CactusStreamingResponse: Decodable {
    let success: Bool?
    let confirmed: String?
    let pending: String?
    let error: String?
}

@MainActor
final class LiveTranscriptionService {
    typealias TranscriptHandler = @MainActor (TranscriptSnapshot) -> Void
    typealias FailureHandler = @MainActor (String) -> Void

    private let audioCaptureService: AudioCaptureService
    private let environment: CactusEnvironment
    private let processingQueue = DispatchQueue(label: "cactus.transcription.processing")
    private let targetFormat = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16_000, channels: 1, interleaved: true)!

    private var model: cactus_model_t?
    private var stream: cactus_stream_transcribe_t?
    private var converter: AVAudioConverter?
    private var currentSnapshot = TranscriptSnapshot(confirmed: "", pending: "")
    private var onTranscript: TranscriptHandler?
    private var onFailure: FailureHandler?

    init(audioCaptureService: AudioCaptureService, environment: CactusEnvironment = .probe()) {
        self.audioCaptureService = audioCaptureService
        self.environment = environment
    }

    func requestSpeechPermission() async -> Bool {
        true
    }

    func start(mode: LiveTranscriptionMode, onTranscript: @escaping TranscriptHandler, onFailure: @escaping FailureHandler) throws {
        stop()

        guard environment.isInstalled, let modelPath = environment.modelPath else {
            onFailure("Cactus transcription model is missing. Run: \(environment.suggestedSetup)")
            return
        }

        self.onTranscript = onTranscript
        self.onFailure = onFailure
        currentSnapshot = TranscriptSnapshot(confirmed: "", pending: "")

        try initializeModelIfNeeded(modelPath: modelPath)
        try startStream(mode: mode)

        audioCaptureService.onBuffer = { [weak self] buffer, _ in
            self?.process(buffer: buffer)
        }

        try audioCaptureService.start()
    }

    func stop() {
        processingQueue.sync {
            if let stream {
                var buffer = [CChar](repeating: 0, count: 4096)
                _ = cactus_stream_transcribe_stop(stream, &buffer, buffer.count)
            }
            stream = nil
            converter = nil
            currentSnapshot = TranscriptSnapshot(confirmed: "", pending: "")
        }

        audioCaptureService.onBuffer = nil
        audioCaptureService.stop()
    }

    deinit {
        if let model {
            cactus_destroy(model)
        }
    }

    private func initializeModelIfNeeded(modelPath: String) throws {
        guard model == nil else { return }

        cactus_log_set_level(2)

        let createdModel = modelPath.withCString { pathPointer in
            cactus_init(pathPointer, nil, false)
        }

        guard let createdModel else {
            let reason = cactus_get_last_error().flatMap { String(validatingUTF8: $0) } ?? "Unknown Cactus initialization error."
            throw NSError(domain: "Cactus.Transcription", code: 1, userInfo: [NSLocalizedDescriptionKey: reason])
        }

        model = createdModel
    }

    private func startStream(mode: LiveTranscriptionMode) throws {
        guard let model else {
            throw NSError(domain: "Cactus.Transcription", code: 2, userInfo: [NSLocalizedDescriptionKey: "Cactus model was not initialized."])
        }

        let options = switch mode {
        case .wakeWord:
            #"{"min_chunk_size":1600,"language":"en","custom_vocabulary":["cactus","catcus","caktus","hey cactus","hey catcus"],"vocabulary_boost":10.0}"#
        case .command:
            #"{"min_chunk_size":2400,"language":"en","custom_vocabulary":["cactus","catcus","caktus"],"vocabulary_boost":4.0}"#
        }

        let createdStream = options.withCString { optionsPointer in
            cactus_stream_transcribe_start(model, optionsPointer)
        }

        guard let createdStream else {
            let reason = cactus_get_last_error().flatMap { String(validatingUTF8: $0) } ?? "Unable to start Cactus streaming transcription."
            throw NSError(domain: "Cactus.Transcription", code: 3, userInfo: [NSLocalizedDescriptionKey: reason])
        }

        stream = createdStream
    }

    private func process(buffer: AVAudioPCMBuffer) {
        processingQueue.async { [weak self] in
            guard let self, let stream = self.stream else { return }

            do {
                let pcmData = try self.convertToPCM16(buffer: buffer)
                guard !pcmData.isEmpty else { return }

                var responseBuffer = [CChar](repeating: 0, count: 32768)
                let result = pcmData.withUnsafeBytes { bytes in
                    cactus_stream_transcribe_process(
                        stream,
                        bytes.bindMemory(to: UInt8.self).baseAddress,
                        bytes.count,
                        &responseBuffer,
                        responseBuffer.count
                    )
                }

                guard result >= 0 else {
                    self.emitFailure(cactus_get_last_error().flatMap { String(validatingUTF8: $0) } ?? "Cactus transcription failed.")
                    return
                }

                let json = String(cString: responseBuffer)
                try self.handle(responseJSON: json)
            } catch {
                self.emitFailure(error.localizedDescription)
            }
        }
    }

    private func convertToPCM16(buffer: AVAudioPCMBuffer) throws -> Data {
        if converter == nil || converter?.inputFormat != buffer.format {
            converter = AVAudioConverter(from: buffer.format, to: targetFormat)
        }

        guard let converter else {
            throw NSError(domain: "Cactus.Transcription", code: 4, userInfo: [NSLocalizedDescriptionKey: "Unable to create audio converter for Cactus input."])
        }

        let ratio = targetFormat.sampleRate / buffer.format.sampleRate
        let outputCapacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio + 64)
        guard let outputBuffer = AVAudioPCMBuffer(pcmFormat: targetFormat, frameCapacity: max(outputCapacity, 256)) else {
            throw NSError(domain: "Cactus.Transcription", code: 5, userInfo: [NSLocalizedDescriptionKey: "Unable to allocate PCM buffer for Cactus input."])
        }

        var didProvideInput = false
        var conversionError: NSError?
        let status = converter.convert(to: outputBuffer, error: &conversionError) { _, outStatus in
            if didProvideInput {
                outStatus.pointee = .noDataNow
                return nil
            }

            didProvideInput = true
            outStatus.pointee = .haveData
            return buffer
        }

        if let conversionError {
            throw conversionError
        }

        guard status != .error else {
            throw NSError(domain: "Cactus.Transcription", code: 6, userInfo: [NSLocalizedDescriptionKey: "AVAudioConverter failed to produce Cactus PCM input."])
        }

        guard let channelData = outputBuffer.int16ChannelData else {
            return Data()
        }

        let frameLength = Int(outputBuffer.frameLength)
        let sampleCount = frameLength * Int(targetFormat.channelCount)
        return Data(bytes: channelData[0], count: sampleCount * MemoryLayout<Int16>.size)
    }

    private func handle(responseJSON: String) throws {
        guard let jsonData = responseJSON.data(using: .utf8) else { return }
        let response = try JSONDecoder().decode(CactusStreamingResponse.self, from: jsonData)

        if let error = response.error, !error.isEmpty {
            emitFailure(error)
            return
        }

        let confirmed = mergeConfirmed(existing: currentSnapshot.confirmed, incoming: response.confirmed ?? "")
        let pending = (response.pending ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let snapshot = TranscriptSnapshot(confirmed: confirmed, pending: pending)
        currentSnapshot = snapshot

        guard let onTranscript else { return }
        Task { @MainActor in
            onTranscript(snapshot)
        }
    }

    private func mergeConfirmed(existing: String, incoming: String) -> String {
        let trimmedIncoming = incoming.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedIncoming.isEmpty else { return existing }
        guard !existing.localizedCaseInsensitiveContains(trimmedIncoming) else { return existing }

        if existing.isEmpty {
            return trimmedIncoming
        }

        return "\(existing) \(trimmedIncoming)".trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func emitFailure(_ message: String) {
        guard let onFailure else { return }
        Task { @MainActor in
            onFailure(message)
        }
    }
}
