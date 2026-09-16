import Foundation

enum VoiceProviderChoice: Equatable {
    case studio
    case native
}

enum VoiceProviderSelection {
    static func choose(config: VoiceStudioConfig?, reachable: Bool) -> VoiceProviderChoice {
        guard let config, reachable else { return .native }
        return .studio
    }
}

/// Injectable so tests never touch the network.
@MainActor
protocol VoiceStudioHealthChecking: AnyObject {
    func isReachable(config: VoiceStudioConfig) async -> Bool
}

/// Any HTTP response means the endpoint is reachable; transport errors mean not.
@MainActor
final class VoiceStudioHealthCheck: VoiceStudioHealthChecking {
    private let session: URLSession
    init(session: URLSession = .shared) { self.session = session }

    func isReachable(config: VoiceStudioConfig) async -> Bool {
        var request = URLRequest(url: config.baseURL)
        request.httpMethod = "HEAD"
        request.timeoutInterval = min(config.timeout, 2)
        do {
            let (_, response) = try await session.data(for: request)
            return response is HTTPURLResponse
        } catch {
            return false
        }
    }
}

#if os(iOS)
@preconcurrency import AVFoundation
@preconcurrency import Speech
import Observation

/// Native voice transport. Conversation facts and interview progress live in Life Engine.
@Observable @MainActor
final class NativeInterviewVoice: NSObject, VoiceProviderDelegate {
    enum State { case idle, speaking, listening, processing, error }
    private(set) var state: State = .idle
    private(set) var partial = ""
    private(set) var errorMessage: String?
    var onAnswer: (@MainActor (String) -> Void)?
    private var provider: any VoiceProvider
    private let healthCheck: any VoiceStudioHealthChecking
    private let studioProviderFactory: (VoiceStudioConfig) -> any VoiceProvider
    private let fallbackProviderFactory: () -> any VoiceProvider
    private var providerResolved = false
    private(set) var resolvedChoice: VoiceProviderChoice?
    private var pendingText: String?
    private let engine = AVAudioEngine()
    private var recognition: SFSpeechRecognitionTask?
    private var audioRequest: SFSpeechAudioBufferRecognitionRequest?
    private var tapInstalled = false
    private var generation = 0
    private var endpointTask: Task<Void, Never>?
    private var rolloverTask: Task<Void, Never>?
    private var permissionGeneration = 0
    var conversationEnabled = false

    init(provider: (any VoiceProvider)? = nil,
         healthCheck: (any VoiceStudioHealthChecking)? = nil,
         studioProviderFactory: @escaping (VoiceStudioConfig) -> any VoiceProvider = { VoiceStudioProvider(config: $0) },
         fallbackProvider: @escaping () -> any VoiceProvider = { NativeSpeechProvider() }) {
        if let provider {
            self.provider = provider
            self.providerResolved = true
        } else {
            self.provider = NativeSpeechProvider()
            self.providerResolved = false
        }
        self.healthCheck = healthCheck ?? VoiceStudioHealthCheck()
        self.studioProviderFactory = studioProviderFactory
        self.fallbackProviderFactory = fallbackProvider
        super.init()
        self.provider.delegate = self
    }

    func start(question: String) async {
        permissionGeneration += 1
        let requestedGeneration = permissionGeneration
        let speech = await Self.requestSpeechAuthorization()
        let microphone = await AVAudioApplication.requestRecordPermission()
        guard permissionGeneration == requestedGeneration else { return }
        guard speech == .authorized, microphone else {
            fail("Microphone and speech access are needed for voice. You can still type.")
            return
        }
        conversationEnabled = true
        errorMessage = nil
        await resolveProviderIfNeeded()
        if question.isEmpty { listen() } else { speak(question) }
    }

    /// Resolves the speech provider once: VoiceStudio when configured and
    /// reachable, otherwise native. `config` defaults to the environment
    /// (UserDefaults/plist/launch arguments); tests pass an explicit value.
    func resolveProviderIfNeeded(config: VoiceStudioConfig? = nil) async {
        guard !providerResolved else { return }
        providerResolved = true
        let resolvedConfig = config ?? VoiceStudioConfig.fromEnvironment()
        var reachable = false
        if let resolvedConfig {
            reachable = await healthCheck.isReachable(config: resolvedConfig)
        }
        let choice = VoiceProviderSelection.choose(config: resolvedConfig, reachable: reachable)
        resolvedChoice = choice
        guard choice == .studio, let resolvedConfig else { return }
        let studio = studioProviderFactory(resolvedConfig)
        provider.delegate = nil
        provider = studio
        provider.delegate = self
    }

    func speak(_ text: String) {
        guard conversationEnabled else { return }
        stopCapture()
        state = .speaking
        pendingText = text
        provider.speak(text)
    }

    func interruptAndListen() {
        guard conversationEnabled else { return }
        pendingText = nil
        provider.stop()
        listen()
    }

    private func listen() {
        stopCapture()
        guard let recognizer = SFSpeechRecognizer(locale: .current), recognizer.isAvailable,
              recognizer.supportsOnDeviceRecognition else {
            fail("On-device speech recognition is unavailable. You can type your answer.")
            return
        }
        do {
            try provider.activateAudioSession()
            let request = SFSpeechAudioBufferRecognitionRequest()
            request.requiresOnDeviceRecognition = true
            request.shouldReportPartialResults = true
            audioRequest = request
            partial = ""
            let input = engine.inputNode
            let format = input.outputFormat(forBus: 0)
            guard format.sampleRate > 0, format.channelCount > 0 else {
                fail("Microphone is unavailable right now. You can type your answer.")
                return
            }
            let sink = InterviewAudioSink(request)
            input.installTap(onBus: 0, bufferSize: 1024, format: format) { @Sendable
                buffer, _ in sink.append(buffer)
            }
            tapInstalled = true
            engine.prepare()
            try engine.start()
            state = .listening
            let current = generation
            // Renew recognizers before the platform's bounded request lifetime. Preserve a
            // nonempty partial as one turn; silence simply starts another capture window.
            rolloverTask = Task { @MainActor [weak self] in
                do { try await Task.sleep(for: .seconds(45)) } catch { return }
                guard let self, self.generation == current, self.conversationEnabled else { return }
                self.finishAnswer()
            }
            recognition = recognizer.recognitionTask(with: request) { [weak self] result, error in
                let text = result?.bestTranscription.formattedString
                let final = result?.isFinal == true
                let failed = error != nil
                Task { @MainActor [weak self] in
                    guard let self, self.generation == current, self.conversationEnabled else { return }
                    if let text, text != self.partial {
                        self.partial = text
                        self.endpointTask?.cancel()
                        self.endpointTask = Task { @MainActor [weak self] in
                            do { try await Task.sleep(for: .seconds(3)) } catch { return }
                            guard let self, self.generation == current, self.state == .listening else { return }
                            self.finishAnswer()
                        }
                    }
                    if final { self.finishAnswer() }
                    else if failed { self.fail("I couldn't hear that clearly. Try again or type your answer.") }
                }
            }
        } catch { fail("I couldn't start listening. You can type your answer.") }
    }

    func finishAnswer() {
        let text = partial.trimmingCharacters(in: .whitespacesAndNewlines)
        stopCapture()
        guard !text.isEmpty else {
            if conversationEnabled { listen() } else { state = .idle }
            return
        }
        state = .processing
        onAnswer?(text)
    }

    func pause() {
        permissionGeneration += 1
        conversationEnabled = false
        pendingText = nil
        provider.stop()
        stopCapture()
        state = .idle
        provider.deactivateAudioSession()
    }

    private func stopCapture() {
        rolloverTask?.cancel()
        rolloverTask = nil
        endpointTask?.cancel()
        endpointTask = nil
        generation += 1
        engine.stop()
        if tapInstalled { engine.inputNode.removeTap(onBus: 0); tapInstalled = false }
        audioRequest?.endAudio()
        recognition?.cancel()
        recognition = nil
        audioRequest = nil
    }

    nonisolated private static func requestSpeechAuthorization() async -> SFSpeechRecognizerAuthorizationStatus {
        await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { status in
                continuation.resume(returning: status)
            }
        }
    }

    private func fail(_ message: String) {
        pause()
        errorMessage = message
        state = .error
    }

    // MARK: - VoiceProviderDelegate

    func voiceProviderDidStart(_ provider: any VoiceProvider) {
        state = .speaking
    }

    func voiceProviderDidFinish(_ provider: any VoiceProvider) {
        pendingText = nil
        guard conversationEnabled else { return }
        listen()
    }

    func voiceProviderDidCancel(_ provider: any VoiceProvider) {
        pendingText = nil
        // An intentional stop/cancel must not restart listening.
    }

    func voiceProvider(_ provider: any VoiceProvider, didFailWith error: any Error) {
        if let studioError = error as? VoiceStudioError, studioError.shouldFallbackToNative {
            fallbackToNative()
            return
        }
        fail("Audio is unavailable right now. You can type your answer.")
    }

    private func fallbackToNative() {
        let text = pendingText
        pendingText = nil
        provider.delegate = nil
        let native = fallbackProviderFactory()
        native.delegate = self
        provider = native
        guard conversationEnabled else { return }
        if let text {
            speak(text)
        } else {
            fail("Audio is unavailable right now. You can type your answer.")
        }
    }
}

/// AVAudioEngine invokes its tap off-main; Speech's append API accepts audio on that thread.
private final class InterviewAudioSink: @unchecked Sendable {
    let request: SFSpeechAudioBufferRecognitionRequest
    init(_ request: SFSpeechAudioBufferRecognitionRequest) { self.request = request }
    func append(_ buffer: AVAudioPCMBuffer) { request.append(buffer) }
}
#endif