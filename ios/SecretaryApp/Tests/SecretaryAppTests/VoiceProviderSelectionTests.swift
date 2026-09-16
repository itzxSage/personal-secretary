import XCTest
@testable import SecretaryApp

// MARK: - Fakes

@MainActor
private final class FakeHealthCheck: VoiceStudioHealthChecking {
    var reachable = false
    private(set) var checkCount = 0

    func isReachable(config: VoiceStudioConfig) async -> Bool {
        checkCount += 1
        return reachable
    }
}

@MainActor
private final class FakeVoiceProvider: VoiceProvider {
    weak var delegate: (any VoiceProviderDelegate)?
    private(set) var isSpeaking = false
    private(set) var spokenTexts: [String] = []
    private(set) var stopCount = 0
    private(set) var cancelCount = 0
    private(set) var activationCount = 0
    private(set) var deactivationCount = 0

    func speak(_ text: String) {
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
        stop()
        speak(text)
    }

    func activateAudioSession() throws {
        activationCount += 1
    }

    func deactivateAudioSession() {
        deactivationCount += 1
    }

    /// Simulates a 500-class failure that signals native fallback.
    func simulateFallbackFailure() {
        isSpeaking = false
        delegate?.voiceProvider(self, didFailWith: VoiceStudioError.httpStatus(500))
    }
}

// MARK: - Hermetic HTTP stub for the production health check

private final class HealthCheckStubURLProtocol: URLProtocol {
    nonisolated(unsafe) static var handler: ((URLRequest) throws -> (HTTPURLResponse, Data))?

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        guard let handler = Self.handler else {
            client?.urlProtocol(self, didFailWithError: URLError(.badServerResponse))
            return
        }
        do {
            let (response, data) = try handler(request)
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}

private func makeStubSession() -> URLSession {
    let configuration = URLSessionConfiguration.ephemeral
    configuration.protocolClasses = [HealthCheckStubURLProtocol.self]
    configuration.timeoutIntervalForRequest = 5
    return URLSession(configuration: configuration)
}

// MARK: - Tests

final class VoiceProviderSelectionTests: XCTestCase {
    private var studioConfig: VoiceStudioConfig {
        VoiceStudioConfig(baseURL: URL(string: "http://stub:3900")!,
                          model: "tts-1", voice: "alloy", timeout: 5, apiKey: nil)
    }

    override func tearDown() {
        HealthCheckStubURLProtocol.handler = nil
        super.tearDown()
    }

    @MainActor
    func testSelectionMatrix() {
        let cases: [(config: VoiceStudioConfig?, reachable: Bool, expected: VoiceProviderChoice)] = [
            (studioConfig, true, .studio),
            (studioConfig, false, .native),
            (nil, true, .native),
            (nil, false, .native),
        ]
        for (index, testCase) in cases.enumerated() {
            let choice = VoiceProviderSelection.choose(config: testCase.config, reachable: testCase.reachable)
            XCTAssertEqual(choice, testCase.expected, "matrix case \(index)")
        }
    }

    @MainActor
    func testHealthCheckReachableOnHTTPResponse() async {
        HealthCheckStubURLProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "HEAD")
            let response = HTTPURLResponse(url: request.url!, statusCode: 200,
                                           httpVersion: nil, headerFields: nil)!
            return (response, Data())
        }
        let health = VoiceStudioHealthCheck(session: makeStubSession())
        let reachable = await health.isReachable(config: studioConfig)
        XCTAssertTrue(reachable)
    }

    @MainActor
    func testHealthCheckUnreachableOnTransportError() async {
        HealthCheckStubURLProtocol.handler = { _ in throw URLError(.cannotConnectToHost) }
        let health = VoiceStudioHealthCheck(session: makeStubSession())
        let reachable = await health.isReachable(config: studioConfig)
        XCTAssertFalse(reachable)
    }

#if os(iOS)
    @MainActor
    func testFlowSelectsStudioWhenConfiguredAndReachable() async {
        let health = FakeHealthCheck()
        health.reachable = true
        let studio = FakeVoiceProvider()
        var factoryCalls = 0
        let voice = NativeInterviewVoice(healthCheck: health,
                                         studioProviderFactory: { _ in
            factoryCalls += 1
            return studio
        })

        await voice.resolveProviderIfNeeded(config: studioConfig)

        XCTAssertEqual(health.checkCount, 1)
        XCTAssertEqual(factoryCalls, 1)
        XCTAssertEqual(voice.resolvedChoice, .studio)
    }

    @MainActor
    func testFlowSelectsNativeWhenConfiguredButUnreachable() async {
        let health = FakeHealthCheck()
        health.reachable = false
        let voice = NativeInterviewVoice(healthCheck: health,
                                         studioProviderFactory: { _ in FakeVoiceProvider() })

        await voice.resolveProviderIfNeeded(config: studioConfig)

        XCTAssertEqual(health.checkCount, 1)
        XCTAssertEqual(voice.resolvedChoice, .native)
    }

    @MainActor
    func testFlowSelectsNativeWhenNotConfigured() async {
        let health = FakeHealthCheck()
        let voice = NativeInterviewVoice(healthCheck: health)

        await voice.resolveProviderIfNeeded(config: nil)

        XCTAssertEqual(health.checkCount, 0)
        XCTAssertEqual(voice.resolvedChoice, .native)
    }

    @MainActor
    func testStudioFailureFallsBackToNative() {
        let studio = FakeVoiceProvider()
        let native = FakeVoiceProvider()
        let voice = NativeInterviewVoice(provider: studio, fallbackProvider: { native })
        voice.conversationEnabled = true

        voice.speak("Hello")
        XCTAssertEqual(studio.spokenTexts, ["Hello"])

        studio.simulateFallbackFailure()

        XCTAssertEqual(native.spokenTexts, ["Hello"])
        XCTAssertEqual(voice.state, .speaking)
        XCTAssertNil(voice.errorMessage)
    }

    @MainActor
    func testStudioFailureWhenPausedSwapsProviderWithoutCrash() {
        let studio = FakeVoiceProvider()
        let native = FakeVoiceProvider()
        let voice = NativeInterviewVoice(provider: studio, fallbackProvider: { native })

        studio.simulateFallbackFailure()

        voice.pause()
        XCTAssertEqual(native.stopCount, 1)
        XCTAssertEqual(voice.state, .idle)
    }
#endif
}