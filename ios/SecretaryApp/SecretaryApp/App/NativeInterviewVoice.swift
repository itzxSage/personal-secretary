#if os(iOS)
@preconcurrency import AVFoundation
@preconcurrency import Speech
import Observation

/// Native voice transport. Conversation facts and interview progress live in Life Engine.
@Observable @MainActor
final class NativeInterviewVoice: NSObject, AVSpeechSynthesizerDelegate {
    enum State { case idle, speaking, listening, processing, error }
    private(set) var state: State = .idle
    private(set) var partial = ""
    private(set) var errorMessage: String?
    var onAnswer: (@MainActor (String) -> Void)?
    private let synthesizer = AVSpeechSynthesizer()
    private let engine = AVAudioEngine()
    private var recognition: SFSpeechRecognitionTask?
    private var audioRequest: SFSpeechAudioBufferRecognitionRequest?
    private var tapInstalled = false
    private var generation = 0
    private var endpointTask: Task<Void, Never>?
    private var utteranceID: ObjectIdentifier?
    private(set) var conversationEnabled = false

    override init() {
        super.init()
        synthesizer.delegate = self
    }

    func start(question: String) async {
        let speech = await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { continuation.resume(returning: $0) }
        }
        let microphone = await AVAudioApplication.requestRecordPermission()
        guard speech == .authorized, microphone else {
            fail("Microphone and speech access are needed for voice. You can still type.")
            return
        }
        conversationEnabled = true
        errorMessage = nil
        speak(question)
    }

    func speak(_ text: String) {
        guard conversationEnabled else { return }
        stopCapture()
        synthesizer.stopSpeaking(at: .immediate)
        do {
            try AVAudioSession.sharedInstance().setCategory(.playAndRecord, mode: .voiceChat,
                                                           options: [.defaultToSpeaker, .allowBluetoothHFP])
            try AVAudioSession.sharedInstance().setActive(true)
            let utterance = AVSpeechUtterance(string: text)
            utterance.voice = AVSpeechSynthesisVoice(language: Locale.current.identifier)
            utterance.rate = AVSpeechUtteranceDefaultSpeechRate * 0.92
            utteranceID = ObjectIdentifier(utterance)
            state = .speaking
            synthesizer.speak(utterance)
        } catch { fail("Audio is unavailable right now. You can type your answer.") }
    }

    func interruptAndListen() {
        guard conversationEnabled else { return }
        utteranceID = nil
        synthesizer.stopSpeaking(at: .immediate)
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
            let request = SFSpeechAudioBufferRecognitionRequest()
            request.requiresOnDeviceRecognition = true
            request.shouldReportPartialResults = true
            audioRequest = request
            partial = ""
            let input = engine.inputNode
            let sink = InterviewAudioSink(request)
            input.installTap(onBus: 0, bufferSize: 1024, format: input.outputFormat(forBus: 0)) {
                buffer, _ in sink.append(buffer)
            }
            tapInstalled = true
            engine.prepare()
            try engine.start()
            state = .listening
            let current = generation
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
        guard !text.isEmpty else { state = .idle; return }
        state = .processing
        onAnswer?(text)
    }

    func pause() {
        conversationEnabled = false
        utteranceID = nil
        synthesizer.stopSpeaking(at: .immediate)
        stopCapture()
        state = .idle
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    private func stopCapture() {
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

    private func fail(_ message: String) {
        pause()
        errorMessage = message
        state = .error
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer,
                                      didFinish utterance: AVSpeechUtterance) {
        let identifier = ObjectIdentifier(utterance)
        Task { @MainActor [weak self] in
            guard let self, self.conversationEnabled, self.utteranceID == identifier else { return }
            self.listen()
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
