import AVFoundation

/// Receives speech lifecycle callbacks from a `VoiceProvider`.
@MainActor
protocol VoiceProviderDelegate: AnyObject {
    func voiceProviderDidStart(_ provider: any VoiceProvider)
    func voiceProviderDidFinish(_ provider: any VoiceProvider)
    func voiceProviderDidCancel(_ provider: any VoiceProvider)
    func voiceProvider(_ provider: any VoiceProvider, didFailWith error: any Error)
}

/// A text-to-speech transport. `NativeSpeechProvider` wraps `AVSpeechSynthesizer`;
/// a studio provider can conform later. Providers own their audio-session lifecycle.
@MainActor
protocol VoiceProvider: AnyObject {
    var delegate: (any VoiceProviderDelegate)? { get set }
    var isSpeaking: Bool { get }
    func speak(_ text: String)
    func stop()
    func cancel()
    func bargeIn(with text: String)
    func activateAudioSession() throws
    func deactivateAudioSession()
}

/// Native `AVSpeechSynthesizer` transport.
///
/// Ports the ordering from `NativeInterviewVoice` (the "409-fix ordering"):
/// the current utterance identity is cleared BEFORE `stopSpeaking` so a stale
/// `didFinish`/`didCancel` from the just-stopped utterance is ignored, and it is
/// set BEFORE `speak` so only the current utterance's callbacks reach the
/// delegate. An intentional stop therefore never restarts a follow-up action.
@MainActor
final class NativeSpeechProvider: NSObject, VoiceProvider, AVSpeechSynthesizerDelegate {
    weak var delegate: (any VoiceProviderDelegate)?
    private(set) var isSpeaking = false
    private let synthesizer = AVSpeechSynthesizer()
    private var utteranceID: ObjectIdentifier?

    override init() {
        super.init()
        synthesizer.delegate = self
    }

    func speak(_ text: String) {
        stop()
        do {
            try activateAudioSession()
            let utterance = AVSpeechUtterance(string: text)
            utterance.voice = AVSpeechSynthesisVoice(language: Locale.current.identifier)
            utterance.rate = AVSpeechUtteranceDefaultSpeechRate * 0.92
            utteranceID = ObjectIdentifier(utterance)
            isSpeaking = true
            synthesizer.speak(utterance)
        } catch {
            delegate?.voiceProvider(self, didFailWith: error)
        }
    }

    func stop() {
        // Ordering: clear the utterance identity before stopping so the
        // didFinish/didCancel callback for the just-stopped utterance is ignored.
        utteranceID = nil
        isSpeaking = false
        synthesizer.stopSpeaking(at: .immediate)
    }

    func cancel() {
        stop()
        delegate?.voiceProviderDidCancel(self)
    }

    func bargeIn(with text: String) {
        stop()
        speak(text)
    }

    func activateAudioSession() throws {
#if os(iOS)
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.playAndRecord, mode: .voiceChat,
                                options: [.defaultToSpeaker, .allowBluetoothHFP])
        try session.setActive(true)
#endif
    }

    func deactivateAudioSession() {
#if os(iOS)
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
#endif
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer,
                                       didStart utterance: AVSpeechUtterance) {
        let identifier = ObjectIdentifier(utterance)
        Task { @MainActor [weak self] in
            guard let self, self.utteranceID == identifier else { return }
            self.delegate?.voiceProviderDidStart(self)
        }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer,
                                       didFinish utterance: AVSpeechUtterance) {
        let identifier = ObjectIdentifier(utterance)
        Task { @MainActor [weak self] in
            guard let self, self.utteranceID == identifier else { return }
            self.utteranceID = nil
            self.isSpeaking = false
            self.delegate?.voiceProviderDidFinish(self)
        }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer,
                                       didCancel utterance: AVSpeechUtterance) {
        let identifier = ObjectIdentifier(utterance)
        Task { @MainActor [weak self] in
            guard let self, self.utteranceID == identifier else { return }
            self.utteranceID = nil
            self.isSpeaking = false
            self.delegate?.voiceProviderDidCancel(self)
        }
    }
}