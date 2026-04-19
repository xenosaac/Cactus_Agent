import AVFoundation
import Foundation

@MainActor
final class SpeechFeedbackService: NSObject, AVSpeechSynthesizerDelegate, AVAudioPlayerDelegate {
    private let synthesizer = AVSpeechSynthesizer()
    private let preferredVoice = SpeechVoiceSelector.bestAvailableVoice()
    private let elevenLabs = ElevenLabsSpeechClient()
    private let prefersElevenLabsOnly = AppEnvironment.value(for: "ELEVEN_LABS_API") != nil
    private var audioPlayer: AVAudioPlayer?
    private var onStart: (() -> Void)?
    private var onFinish: (() -> Void)?
    private var pendingSpeechTask: Task<Void, Never>?

    override init() {
        super.init()
        synthesizer.delegate = self
    }

    func speak(_ text: String, onStart: @escaping () -> Void, onFinish: @escaping () -> Void) {
        self.onStart = onStart
        self.onFinish = onFinish

        pendingSpeechTask?.cancel()
        audioPlayer?.stop()
        synthesizer.stopSpeaking(at: .immediate)

        pendingSpeechTask = Task { [weak self] in
            guard let self else { return }

            if let audioData = try? await self.elevenLabs.synthesizeSpeech(from: text) {
                self.playElevenLabsAudio(audioData)
            } else {
                if self.prefersElevenLabsOnly {
                    self.finishWithoutSpeech()
                } else {
                    self.speakWithSystemVoice(text)
                }
            }
        }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didStart utterance: AVSpeechUtterance) {
        Task { @MainActor in
            self.onStart?()
        }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        Task { @MainActor in
            self.onFinish?()
        }
    }

    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor in
            self.onFinish?()
        }
    }

    private func playElevenLabsAudio(_ audioData: Data) {
        do {
            let player = try AVAudioPlayer(data: audioData)
            player.delegate = self
            player.prepareToPlay()
            audioPlayer = player

            if player.play() {
                onStart?()
            } else {
                if prefersElevenLabsOnly {
                    finishWithoutSpeech()
                } else {
                    speakWithSystemVoice("Sorry, I could not start audio playback.")
                }
            }
        } catch {
            if prefersElevenLabsOnly {
                finishWithoutSpeech()
            } else {
                speakWithSystemVoice("Sorry, I could not decode the generated speech.")
            }
        }
    }

    private func speakWithSystemVoice(_ text: String) {
        let utterance = AVSpeechUtterance(string: text)
        utterance.rate = 0.44
        utterance.pitchMultiplier = 1.0
        utterance.volume = 1.0
        utterance.preUtteranceDelay = 0.05
        utterance.postUtteranceDelay = 0.05
        utterance.voice = preferredVoice ?? AVSpeechSynthesisVoice(language: "en-US")
        synthesizer.speak(utterance)
    }

    private func finishWithoutSpeech() {
        onStart?()
        onFinish?()
    }
}

private enum SpeechVoiceSelector {
    private static let noveltyNames: Set<String> = [
        "bad news", "bahh", "bells", "boing", "bubbles", "cellos", "good news",
        "jester", "organ", "superstar", "wobble", "albert", "fred", "junior",
        "ralph", "trinoids", "whisper", "zarvox"
    ]

    static func bestAvailableVoice() -> AVSpeechSynthesisVoice? {
        let englishVoices = AVSpeechSynthesisVoice.speechVoices()
            .filter { $0.language.lowercased().hasPrefix("en") }
            .filter { !noveltyNames.contains($0.name.lowercased()) }

        guard !englishVoices.isEmpty else {
            return AVSpeechSynthesisVoice(language: "en-US")
        }

        return englishVoices.max(by: { score($0) < score($1) })
    }

    private static func score(_ voice: AVSpeechSynthesisVoice) -> Int {
        var value = 0

        if voice.language.lowercased() == "en-us" {
            value += 40
        }

        if voice.name.localizedCaseInsensitiveContains("eddy") ||
            voice.name.localizedCaseInsensitiveContains("flo") ||
            voice.name.localizedCaseInsensitiveContains("reed") {
            value += 25
        }

        if voice.name.localizedCaseInsensitiveContains("siri") {
            value += 20
        }

        if #available(macOS 10.15, *) {
            switch voice.quality {
            case .premium:
                value += 100
            case .enhanced:
                value += 60
            default:
                value += 10
            }
        }

        return value
    }
}

private struct ElevenLabsSpeechClient {
    private let session = URLSession.shared
    private let baseURL = URL(string: "https://api.elevenlabs.io/v1/text-to-speech")!

    func synthesizeSpeech(from text: String) async throws -> Data {
        guard let apiKey = AppEnvironment.value(for: "ELEVEN_LABS_API") else {
            throw ElevenLabsError.missingAPIKey
        }

        let voiceID = AppEnvironment.value(for: "ELEVEN_LABS_VOICE_ID") ?? "JBFqnCBsd6RMkjVDRZzb"
        let requestURL = baseURL
            .appendingPathComponent(voiceID)
            .appendingPathComponent("stream")
            .appending(queryItems: [
                URLQueryItem(name: "output_format", value: "mp3_44100_128")
            ])

        var request = URLRequest(url: requestURL)
        request.httpMethod = "POST"
        request.setValue(apiKey, forHTTPHeaderField: "xi-api-key")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 20

        let payload = ElevenLabsRequest(
            text: text,
            modelID: "eleven_multilingual_v2",
            voiceSettings: ElevenLabsVoiceSettings(
                stability: 0.35,
                similarityBoost: 0.8,
                style: 0.35,
                useSpeakerBoost: true
            )
        )

        request.httpBody = try JSONEncoder().encode(payload)

        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw ElevenLabsError.invalidResponse
        }

        guard (200 ..< 300).contains(httpResponse.statusCode) else {
            throw ElevenLabsError.httpFailure(httpResponse.statusCode)
        }

        guard !data.isEmpty else {
            throw ElevenLabsError.emptyAudio
        }

        return data
    }
}

private struct ElevenLabsRequest: Encodable {
    let text: String
    let modelID: String
    let voiceSettings: ElevenLabsVoiceSettings

    enum CodingKeys: String, CodingKey {
        case text
        case modelID = "model_id"
        case voiceSettings = "voice_settings"
    }
}

private struct ElevenLabsVoiceSettings: Encodable {
    let stability: Double
    let similarityBoost: Double
    let style: Double
    let useSpeakerBoost: Bool

    enum CodingKeys: String, CodingKey {
        case stability
        case similarityBoost = "similarity_boost"
        case style
        case useSpeakerBoost = "use_speaker_boost"
    }
}

private enum ElevenLabsError: Error {
    case missingAPIKey
    case invalidResponse
    case httpFailure(Int)
    case emptyAudio
}

private extension URL {
    func appending(queryItems: [URLQueryItem]) -> URL {
        guard var components = URLComponents(url: self, resolvingAgainstBaseURL: false) else {
            return self
        }
        components.queryItems = queryItems
        return components.url ?? self
    }
}
