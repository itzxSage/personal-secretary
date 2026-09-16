import XCTest
@testable import SecretaryApp

/// Recording fake `VoiceProvider` that mirrors `NativeSpeechProvider`'s
/// stale-callback guard: after stop/cancel, simulated terminal callbacks are
/// ignored, exactly like the real provider ignores a stopped utterance's
/// `didFinish`/`didCancel` because its identity was cleared before stopping.
@MainActor
private final class FakeVoiceProvider: VoiceProvider {
    weak var delegate: (any VoiceProviderDelegate)?
    private(set) var isSpeaking = false
    private(set) var spokenTexts: [String] = []
    private(set) var stopCount = 0
    private(set) var cancelCount = 0
    private(set) var bargeInCount = 0
    private(set) var activationCount = 0
    private(set) var deactivationCount = 0
    var failOnSpeak: (any Error)?

    func speak(_ text: String) {
        if let failOnSpeak {
            delegate?.voiceProvider(self, didFailWith: failOnSpeak)
            return
        }
        spokenTexts.append(text)
        isSpeaking = true
        delegate?.voiceProviderDidStart(self)
    }

    func stop() {
        stopCount += 1
        isSpeaking = false
    }

    func cancel() {
        cancelCount += 1
        isSpeaking = false
        delegate?.voiceProviderDidCancel(self)
    }

    func bargeIn(with text: String) {
        bargeInCount += 1
        stop()
        speak(text)
    }

    func activateAudioSession() throws {
        activationCount += 1
    }

    func deactivateAudioSession() {
        deactivationCount += 1
    }

    /// Simulates the real provider's terminal callback for the current utterance.
    func simulateFinish() {
        guard isSpeaking else { return }
        isSpeaking = false
        delegate?.voiceProviderDidFinish(self)
    }

    /// Simulates the real provider's terminal callback for the current utterance.
    func simulateCancel() {
        guard isSpeaking else { return }
        isSpeaking = false
        delegate?.voiceProviderDidCancel(self)
    }
}

/// Records every delegate callback a session observes from a provider.
@MainActor
private final class RecordingVoiceDelegate: VoiceProviderDelegate {
    enum Event: Equatable {
        case started
        case finished
        case cancelled
        case failed
    }
    private(set) var events: [Event] = []

    func voiceProviderDidStart(_ provider: any VoiceProvider) { events.append(.started) }
    func voiceProviderDidFinish(_ provider: any VoiceProvider) { events.append(.finished) }
    func voiceProviderDidCancel(_ provider: any VoiceProvider) { events.append(.cancelled) }
    func voiceProvider(_ provider: any VoiceProvider, didFailWith error: any Error) {
        events.append(.failed)
    }
}

private enum VoiceProviderTestError: Error {
    case audioUnavailable
}

final class VoiceProviderTests: XCTestCase {
    @MainActor
    func testFakeProviderRecordsDelegateCallbacks() {
        let provider = FakeVoiceProvider()
        let delegate = RecordingVoiceDelegate()
        provider.delegate = delegate

        provider.speak("Hello")
        XCTAssertEqual(delegate.events, [.started])
        XCTAssertEqual(provider.spokenTexts, ["Hello"])
        XCTAssertTrue(provider.isSpeaking)

        provider.simulateFinish()
        XCTAssertEqual(delegate.events, [.started, .finished])
        XCTAssertFalse(provider.isSpeaking)

        provider.speak("Again")
        provider.cancel()
        XCTAssertEqual(delegate.events, [.started, .finished, .started, .cancelled])
        XCTAssertFalse(provider.isSpeaking)

        provider.speak("Boom")
        provider.failOnSpeak = VoiceProviderTestError.audioUnavailable
        provider.speak("Fails")
        XCTAssertEqual(delegate.events, [.started, .finished, .started, .cancelled, .started, .failed])
    }

    @MainActor
    func testCancelStopsImmediatelyWithNoFurtherCallbacks() {
        let provider = FakeVoiceProvider()
        let delegate = RecordingVoiceDelegate()
        provider.delegate = delegate

        provider.speak("Hello")
        provider.cancel()
        // A stale terminal callback from the cancelled utterance must be ignored.
        provider.simulateFinish()
        provider.simulateCancel()

        XCTAssertEqual(delegate.events, [.started, .cancelled])
        XCTAssertFalse(provider.isSpeaking)
    }

    @MainActor
    func testBargeInRestartsWithNewStartedCallback() {
        let provider = FakeVoiceProvider()
        let delegate = RecordingVoiceDelegate()
        provider.delegate = delegate

        provider.speak("First")
        provider.bargeIn(with: "Second")

        XCTAssertEqual(provider.spokenTexts, ["First", "Second"])
        XCTAssertEqual(provider.bargeInCount, 1)
        XCTAssertEqual(delegate.events, [.started, .started])
        XCTAssertTrue(provider.isSpeaking)
    }

#if os(iOS)
    @MainActor
    func testNativeInterviewVoiceInjectsProviderAndWiresLifecycle() {
        let provider = FakeVoiceProvider()
        let voice = NativeInterviewVoice(provider: provider)

        // speak() is guarded by conversationEnabled.
        voice.speak("Ignored")
        XCTAssertTrue(provider.spokenTexts.isEmpty)

        // pause() stops the provider and deactivates the audio session.
        voice.pause()
        XCTAssertEqual(provider.stopCount, 1)
        XCTAssertEqual(provider.deactivationCount, 1)
        XCTAssertEqual(voice.state, .idle)
    }
#endif
}