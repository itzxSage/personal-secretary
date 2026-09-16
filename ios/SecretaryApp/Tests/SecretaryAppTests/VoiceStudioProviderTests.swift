import XCTest
@testable import SecretaryApp

// MARK: - In-process HTTP stub (no real network)

/// Hermetic HTTP stub serving WAV fixtures through `URLProtocol`.
/// `handler` serves a single response; `chunkedHandler` streams the body in
/// chunks with a per-chunk delay (for mid-stream stop tests).
private final class StubURLProtocol: URLProtocol {
    nonisolated(unsafe) static var handler: ((URLRequest) throws -> (HTTPURLResponse, Data))?
    nonisolated(unsafe) static var chunkedHandler: ((URLRequest) throws -> (HTTPURLResponse, [Data], TimeInterval))?
    nonisolated(unsafe) static var onFirstChunk: (() -> Void)?

    private var stopped = false

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        if let chunkedHandler = Self.chunkedHandler {
            do {
                let (response, chunks, delay) = try chunkedHandler(request)
                client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
                for (index, chunk) in chunks.enumerated() {
                    DispatchQueue.global().asyncAfter(deadline: .now() + delay * Double(index + 1)) { [weak self] in
                        guard let self, !self.stopped else { return }
                        self.client?.urlProtocol(self, didLoad: chunk)
                        if index == 0 { Self.onFirstChunk?() }
                        if index == chunks.count - 1 {
                            self.client?.urlProtocolDidFinishLoading(self)
                        }
                    }
                }
            } catch {
                client?.urlProtocol(self, didFailWithError: error)
            }
            return
        }
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

    override func stopLoading() {
        stopped = true
    }
}

// MARK: - Fakes

/// Recording fake `AudioPlaying` — never touches audio hardware.
@MainActor
private final class FakeAudioPlayer: AudioPlaying {
    weak var delegate: (any AudioPlayerDelegate)?
    private(set) var isPlaying = false
    private(set) var playedData: [Data] = []
    private(set) var stopCount = 0
    var failOnPlay: (any Error)?

    func play(data: Data) throws {
        if let failOnPlay { throw failOnPlay }
        playedData.append(data)
        isPlaying = true
    }

    func stop() {
        stopCount += 1
        isPlaying = false
    }

    /// Simulates the real player's terminal callback for the current audio.
    func simulateFinish() {
        guard isPlaying else { return }
        isPlaying = false
        delegate?.audioPlayerDidFinish(self)
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
    var onStarted: (() -> Void)?
    var onFailed: (() -> Void)?

    func voiceProviderDidStart(_ provider: any VoiceProvider) {
        events.append(.started)
        onStarted?()
    }
    func voiceProviderDidFinish(_ provider: any VoiceProvider) { events.append(.finished) }
    func voiceProviderDidCancel(_ provider: any VoiceProvider) { events.append(.cancelled) }
    func voiceProvider(_ provider: any VoiceProvider, didFailWith error: any Error) {
        events.append(.failed)
        onFailed?()
    }
}

// MARK: - Fixtures

/// Minimal valid WAV: 44-byte RIFF header + 8 bytes of 8-bit silence.
private func makeWAVFixture() -> Data {
    var data = Data()
    data.append(contentsOf: Array("RIFF".utf8))
    data.append(contentsOf: [0x24, 0x00, 0x00, 0x00]) // chunk size = 36 + 8
    data.append(contentsOf: Array("WAVE".utf8))
    data.append(contentsOf: Array("fmt ".utf8))
    data.append(contentsOf: [0x10, 0x00, 0x00, 0x00]) // fmt chunk size = 16
    data.append(contentsOf: [0x01, 0x00])             // PCM
    data.append(contentsOf: [0x01, 0x00])             // mono
    data.append(contentsOf: [0x40, 0x1F, 0x00, 0x00]) // 8000 Hz
    data.append(contentsOf: [0x40, 0x1F, 0x00, 0x00]) // byte rate
    data.append(contentsOf: [0x01, 0x00])             // block align
    data.append(contentsOf: [0x08, 0x00])             // bits per sample
    data.append(contentsOf: Array("data".utf8))
    data.append(contentsOf: [0x08, 0x00, 0x00, 0x00]) // data size = 8
    data.append(contentsOf: [0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80])
    return data
}

private func makeStubSession() -> URLSession {
    let configuration = URLSessionConfiguration.ephemeral
    configuration.protocolClasses = [StubURLProtocol.self]
    configuration.timeoutIntervalForRequest = 5
    return URLSession(configuration: configuration)
}

/// URLSession moves a request body from `httpBody` to `httpBodyStream` before
/// the URLProtocol sees it; read either representation.
private func readRequestBody(_ request: URLRequest) -> Data? {
    if let body = request.httpBody { return body }
    guard let stream = request.httpBodyStream else { return nil }
    stream.open()
    defer { stream.close() }
    var data = Data()
    let bufferSize = 1024
    let buffer = UnsafeMutablePointer<UInt8>.allocate(capacity: bufferSize)
    defer { buffer.deallocate() }
    while stream.hasBytesAvailable {
        let read = stream.read(buffer, maxLength: bufferSize)
        if read <= 0 { break }
        data.append(buffer, count: read)
    }
    return data
}

// MARK: - Tests

final class VoiceStudioProviderTests: XCTestCase {
    private var config: VoiceStudioConfig {
        VoiceStudioConfig(baseURL: URL(string: "http://stub")!,
                          model: "tts-1", voice: "alloy", timeout: 5, apiKey: nil)
    }

    override func tearDown() {
        StubURLProtocol.handler = nil
        StubURLProtocol.chunkedHandler = nil
        StubURLProtocol.onFirstChunk = nil
        super.tearDown()
    }

    @MainActor
    func testHappyPathPlaysAndReportsStartedThenFinished() async throws {
        StubURLProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(request.url?.path, "/v1/audio/speech")
            let body = try XCTUnwrap(readRequestBody(request))
            let json = try XCTUnwrap(try JSONSerialization.jsonObject(with: body) as? [String: String])
            XCTAssertEqual(json["model"], "tts-1")
            XCTAssertEqual(json["voice"], "alloy")
            XCTAssertEqual(json["input"], "Hello")
            let response = HTTPURLResponse(url: request.url!, statusCode: 200,
                                           httpVersion: nil,
                                           headerFields: ["Content-Type": "audio/wav"])!
            return (response, makeWAVFixture())
        }
        let fakePlayer = FakeAudioPlayer()
        let provider = VoiceStudioProvider(config: config, session: makeStubSession(),
                                           playerFactory: { fakePlayer })
        let delegate = RecordingVoiceDelegate()
        provider.delegate = delegate

        let started = expectation(description: "started")
        delegate.onStarted = { started.fulfill() }

        provider.speak("Hello")
        await fulfillment(of: [started], timeout: 2)

        XCTAssertEqual(delegate.events, [.started])
        XCTAssertTrue(provider.isSpeaking)
        XCTAssertEqual(fakePlayer.playedData, [makeWAVFixture()])
        XCTAssertNotNil(provider.lastTTFB)

        fakePlayer.simulateFinish()
        XCTAssertEqual(delegate.events, [.started, .finished])
        XCTAssertFalse(provider.isSpeaking)
    }

    @MainActor
    func testMidStreamStopStopsCleanlyWithNoFurtherCallbacks() async throws {
        StubURLProtocol.handler = { request in
            let response = HTTPURLResponse(url: request.url!, statusCode: 200,
                                           httpVersion: nil, headerFields: nil)!
            return (response, makeWAVFixture())
        }
        let fakePlayer = FakeAudioPlayer()
        let provider = VoiceStudioProvider(config: config, session: makeStubSession(),
                                           playerFactory: { fakePlayer })
        let delegate = RecordingVoiceDelegate()
        provider.delegate = delegate

        let started = expectation(description: "started")
        delegate.onStarted = { started.fulfill() }

        provider.speak("Hello")
        await fulfillment(of: [started], timeout: 2)

        provider.stop()

        XCTAssertFalse(provider.isSpeaking)
        XCTAssertEqual(fakePlayer.stopCount, 1)
        XCTAssertEqual(delegate.events, [.started])

        // A stale terminal callback from the stopped audio must be ignored.
        fakePlayer.simulateFinish()
        XCTAssertEqual(delegate.events, [.started])
        XCTAssertFalse(provider.isSpeaking)
    }

    @MainActor
    func testStopDuringStreamingCancelsRequestAndSuppressesCallbacks() async throws {
        let wav = makeWAVFixture()
        let split = wav.count / 2
        StubURLProtocol.chunkedHandler = { request in
            let response = HTTPURLResponse(url: request.url!, statusCode: 200,
                                           httpVersion: nil, headerFields: nil)!
            return (response, [Data(wav.prefix(split)), Data(wav.suffix(from: split))], 0.3)
        }
        let fakePlayer = FakeAudioPlayer()
        let provider = VoiceStudioProvider(config: config, session: makeStubSession(),
                                           playerFactory: { fakePlayer })
        let delegate = RecordingVoiceDelegate()
        provider.delegate = delegate

        let firstChunk = expectation(description: "first chunk delivered")
        StubURLProtocol.onFirstChunk = { firstChunk.fulfill() }

        provider.speak("Hello")
        await fulfillment(of: [firstChunk], timeout: 2)

        provider.stop()

        XCTAssertFalse(provider.isSpeaking)
        XCTAssertEqual(delegate.events, [])
        XCTAssertEqual(fakePlayer.playedData, [])
        XCTAssertEqual(fakePlayer.stopCount, 0)
    }

    @MainActor
    func testHTTP429ReportsErrorAndSetsFallbackSignal() async throws {
        StubURLProtocol.handler = { request in
            let response = HTTPURLResponse(url: request.url!, statusCode: 429,
                                           httpVersion: nil, headerFields: nil)!
            return (response, Data())
        }
        let provider = VoiceStudioProvider(config: config, session: makeStubSession(),
                                           playerFactory: { FakeAudioPlayer() })
        let delegate = RecordingVoiceDelegate()
        provider.delegate = delegate

        let failed = expectation(description: "failed")
        delegate.onFailed = { failed.fulfill() }

        provider.speak("Hello")
        await fulfillment(of: [failed], timeout: 2)

        XCTAssertEqual(delegate.events, [.failed])
        XCTAssertTrue(provider.shouldFallbackToNative)
        XCTAssertFalse(provider.isSpeaking)
    }

    @MainActor
    func testHTTP500ReportsErrorAndSetsFallbackSignal() async throws {
        StubURLProtocol.handler = { request in
            let response = HTTPURLResponse(url: request.url!, statusCode: 500,
                                           httpVersion: nil, headerFields: nil)!
            return (response, Data())
        }
        let provider = VoiceStudioProvider(config: config, session: makeStubSession(),
                                           playerFactory: { FakeAudioPlayer() })
        let delegate = RecordingVoiceDelegate()
        provider.delegate = delegate

        let failed = expectation(description: "failed")
        delegate.onFailed = { failed.fulfill() }

        provider.speak("Hello")
        await fulfillment(of: [failed], timeout: 2)

        XCTAssertEqual(delegate.events, [.failed])
        XCTAssertTrue(provider.shouldFallbackToNative)
        XCTAssertFalse(provider.isSpeaking)
    }

    @MainActor
    func testCancelReportsCancelledAndStops() async throws {
        StubURLProtocol.handler = { request in
            let response = HTTPURLResponse(url: request.url!, statusCode: 200,
                                           httpVersion: nil, headerFields: nil)!
            return (response, makeWAVFixture())
        }
        let fakePlayer = FakeAudioPlayer()
        let provider = VoiceStudioProvider(config: config, session: makeStubSession(),
                                           playerFactory: { fakePlayer })
        let delegate = RecordingVoiceDelegate()
        provider.delegate = delegate

        let started = expectation(description: "started")
        delegate.onStarted = { started.fulfill() }

        provider.speak("Hello")
        await fulfillment(of: [started], timeout: 2)

        provider.cancel()

        XCTAssertEqual(delegate.events, [.started, .cancelled])
        XCTAssertFalse(provider.isSpeaking)
        XCTAssertEqual(fakePlayer.stopCount, 1)
    }

    func testLatencyProbeRecordsFirstByteOnce() {
        var probe = LatencyProbe(start: Date(timeIntervalSinceNow: -0.5))
        XCTAssertNil(probe.ttfb)
        probe.recordFirstByte()
        XCTAssertNotNil(probe.ttfb)
        XCTAssertGreaterThanOrEqual(probe.ttfb!, 0.4)
        let first = probe.ttfb
        probe.recordFirstByte(at: Date())
        XCTAssertEqual(probe.ttfb, first)
    }

    func testConfigLoadsFromUserDefaults() {
        let suiteName = "VoiceStudioConfigTests-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suiteName)!
        defaults.set("http://stub:3900", forKey: VoiceStudioConfig.baseURLKey)
        defaults.set("nova", forKey: VoiceStudioConfig.voiceKey)
        let config = VoiceStudioConfig.fromEnvironment(defaults: defaults, arguments: [])
        XCTAssertEqual(config?.baseURL, URL(string: "http://stub:3900"))
        XCTAssertEqual(config?.model, "tts-1")
        XCTAssertEqual(config?.voice, "nova")
        XCTAssertNil(config?.apiKey)
        defaults.removePersistentDomain(forName: suiteName)
    }

    func testLaunchArgumentsOverrideUserDefaults() {
        let suiteName = "VoiceStudioConfigTests-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suiteName)!
        defaults.set("http://defaults:3900", forKey: VoiceStudioConfig.baseURLKey)
        let config = VoiceStudioConfig.fromEnvironment(
            defaults: defaults,
            arguments: ["-VoiceStudioBaseURL", "http://launch:3900", "-VoiceStudioVoice", "nova"]
        )
        XCTAssertEqual(config?.baseURL, URL(string: "http://launch:3900"))
        XCTAssertEqual(config?.voice, "nova")
        XCTAssertEqual(config?.model, "tts-1")
        defaults.removePersistentDomain(forName: suiteName)
    }

    func testConfigNilWithoutBaseURL() {
        let suiteName = "VoiceStudioConfigTests-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suiteName)!
        XCTAssertNil(VoiceStudioConfig.fromEnvironment(defaults: defaults, arguments: []))
        defaults.removePersistentDomain(forName: suiteName)
    }
}