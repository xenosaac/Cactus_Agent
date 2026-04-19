import AVFoundation
import Foundation

final class AudioCaptureService {
    typealias BufferHandler = (AVAudioPCMBuffer, AVAudioTime) -> Void

    private let audioEngine = AVAudioEngine()
    private let engineQueue = DispatchQueue(label: "cactus.audio.capture")
    private var tapInstalled = false

    var onBuffer: BufferHandler?

    var isRunning: Bool {
        audioEngine.isRunning
    }

    func requestMicrophonePermission() async -> Bool {
        await withCheckedContinuation { continuation in
            AVCaptureDevice.requestAccess(for: .audio) { granted in
                continuation.resume(returning: granted)
            }
        }
    }

    func start() throws {
        if audioEngine.isRunning {
            return
        }

        let inputNode = audioEngine.inputNode
        let format = inputNode.outputFormat(forBus: 0)

        if !tapInstalled {
            inputNode.installTap(onBus: 0, bufferSize: 256, format: format) { [weak self] buffer, time in
                guard let self else { return }
                self.onBuffer?(buffer, time)
            }
            tapInstalled = true
        }

        audioEngine.prepare()
        try audioEngine.start()
    }

    func stop() {
        engineQueue.sync {
            if audioEngine.isRunning {
                audioEngine.stop()
            }
            if tapInstalled {
                audioEngine.inputNode.removeTap(onBus: 0)
                tapInstalled = false
            }
        }
    }
}
