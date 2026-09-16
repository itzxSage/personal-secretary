import AVFoundation
import Foundation

/// Configuration for the VoiceStudio OpenAI-compatible TTS endpoint.
///
/// Loaded from LaunchArguments / Info.plist / UserDefaults — never hardcoded
/// in source. Precedence: launch arguments (`-VoiceStudioBaseURL http://…`)
/// override Info.plist, which overrides UserDefaults. This matches the standard
/// UI-test injection pattern and keeps credentials out of the source tree.
struct VoiceStudioConfig {
    var baseURL: URL
    var model: String
    var voice: String
    var timeout: TimeInterval
    var apiKey: String?

    static let baseURLKey = "VoiceStudioBaseURL"
    static let modelKey = "VoiceStudioModel"
    static let voiceKey = "VoiceStudioVoice"
    static let apiKeyKey = "VoiceStudioAPIKey"
    static let timeoutKey = "VoiceStudioTimeout"

    /// OpenAI-compatible defaults; VoiceStudio maps `tts-1`/`alloy` to its
    /// active engine and default voice profile.
    static let defaultModel = "tts-1"
    static let defaultVoice = "alloy"
    static let defaultTimeout: TimeInterval = 5

    static func fromEnvironment(defaults: UserDefaults = .standard,
                                arguments: [String] = ProcessInfo.processInfo.arguments,
                                bundle: Bundle = .main) -> VoiceStudioConfig? {
        func value(_ key: String) -> String? {
            if let index = arguments.firstIndex(of: "-\(key)"), index + 1 < arguments.count {
                return arguments[index + 1]
            }
            if let plistValue = bundle.object(forInfoDictionaryKey: key) as? String {
                return plistValue
            }
            return defaults.string(forKey: key)
        }
        guard let baseURLString = value(baseURLKey), let baseURL = URL(string: baseURLString) else {
            return nil
        }
        let timeout = defaults.object(forKey: timeoutKey) as? Double ?? defaultTimeout
        return VoiceStudioConfig(
            baseURL: baseURL,
            model: value(modelKey) ?? defaultModel,
            voice: value(voiceKey) ?? defaultVoice,
            timeout: timeout,
            apiKey: value(apiKeyKey)
        )
    }
}

/// Typed transport errors for the VoiceStudio adapter.
enum VoiceStudioError: Error, Equatable {
    case invalidResponse
    case httpStatus(Int)
    case transport
    case playbackFailed

    /// True when the endpoint is reachable but refused the request (4xx/5xx),
    /// or unreachable — the signal that the caller should fall back to the
    /// native provider.
    var shouldFallbackToNative: Bool {
        switch self {
        case .httpStatus(let code): return (400..<600).contains(code)
        case .transport: return true
        default: return false
        }
    }
}

/// Measures time-to-first-byte (TTFB) for a TTS request.
struct LatencyProbe {
    private(set) var ttfb: TimeInterval?
    private let start: Date

    init(start: Date = Date()) {
        self.start = start
    }

    /// Records the first body byte's arrival; later calls are ignored.
    mutating func recordFirstByte(at now: Date = Date()) {
        guard ttfb == nil else { return }
        ttfb = now.timeIntervalSince(start)
    }
}

/// Minimal audio-playback seam so the provider is testable without audio hardware.
@MainActor
protocol AudioPlaying: AnyObject {
    var delegate: (any AudioPlayerDelegate)? { get set }
    var isPlaying: Bool { get }
    func play(data: Data) throws
    func stop()
}

@MainActor
protocol AudioPlayerDelegate: AnyObject {
    func audioPlayerDidFinish(_ player: any AudioPlaying)
}

/// Production `AudioPlaying` backed by `AVAudioPlayer`.
@MainActor
final class AVAudioPlayerWrapper: NSObject, AudioPlaying, AVAudioPlayerDelegate {
    weak var delegate: (any AudioPlayerDelegate)?
    private(set) var isPlaying = false
    private var player: AVAudioPlayer?

    func play(data: Data) throws {
        let player = try AVAudioPlayer(data: data)
        player.delegate = self
        self.player = player
        player.play()
        isPlaying = true
    }

    func stop() {
        player?.stop()
        player = nil
        isPlaying = false
    }

    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        let identifier = ObjectIdentifier(player)
        Task { @MainActor [weak self] in
            guard let self, let current = self.player, ObjectIdentifier(current) == identifier else { return }
            self.player = nil
            self.isPlaying = false
            self.delegate?.audioPlayerDidFinish(self)
        }
    }
}

/// A `VoiceProvider` that speaks through a VoiceStudio (or any OpenAI-compatible)
/// `POST {baseURL}/v1/audio/speech` endpoint.
///
/// The endpoint returns the full rendered buffer as an HTTP body; the provider
/// streams that body via `URLSession.bytes(for:)`, records time-to-first-byte
/// with `LatencyProbe`, then plays the assembled audio through an `AudioPlaying`
/// seam. `stop()` cancels the in-flight request and stops playback immediately
/// (barge-in); `cancel()` additionally reports `voiceProviderDidCancel`.
/// 4xx/5xx responses surface as `VoiceStudioError.httpStatus` with
/// `shouldFallbackToNative == true`.
@MainActor
final class VoiceStudioProvider: VoiceProvider {
    weak var delegate: (any VoiceProviderDelegate)?
    private(set) var isSpeaking = false
    /// TTFB of the most recent completed request, in seconds.
    private(set) var lastTTFB: TimeInterval?
    /// Set when the last failure was a 4xx/5xx (or transport error) the native
    /// provider can recover from.
    private(set) var shouldFallbackToNative = false

    private let config: VoiceStudioConfig
    private let session: URLSession
    private let playerFactory: () -> any AudioPlaying
    private var player: (any AudioPlaying)?
    private var task: Task<Void, Never>?
    private var generation = 0
    private var probe: LatencyProbe?

    init(config: VoiceStudioConfig,
         session: URLSession = .shared,
         playerFactory: @escaping () -> any AudioPlaying = { AVAudioPlayerWrapper() }) {
        self.config = config
        self.session = session
        self.playerFactory = playerFactory
    }

    func speak(_ text: String) {
        stop()
        do {
            try activateAudioSession()
        } catch {
            delegate?.voiceProvider(self, didFailWith: error)
            return
        }
        generation += 1
        let current = generation
        isSpeaking = true
        shouldFallbackToNative = false
        probe = LatencyProbe()
        task = Task { [weak self] in
            await self?.performRequest(text: text, generation: current)
        }
    }

    func stop() {
        // Ordering mirrors `NativeSpeechProvider`: invalidate the current
        // utterance identity before cancelling so stale terminal callbacks
        // from the just-stopped request/audio are ignored.
        generation += 1
        task?.cancel()
        task = nil
        player?.stop()
        player = nil
        isSpeaking = false
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

    // MARK: - Private

    private func performRequest(text: String, generation: Int) async {
        do {
            let data = try await fetchAudio(text: text)
            guard generation == self.generation else { return }
            lastTTFB = probe?.ttfb
            let player = playerFactory()
            player.delegate = self
            self.player = player
            do {
                try player.play(data: data)
            } catch {
                self.player = nil
                throw VoiceStudioError.playbackFailed
            }
            delegate?.voiceProviderDidStart(self)
        } catch {
            guard generation == self.generation else { return }
            isSpeaking = false
            if let studioError = error as? VoiceStudioError {
                shouldFallbackToNative = studioError.shouldFallbackToNative
                delegate?.voiceProvider(self, didFailWith: studioError)
            } else {
                delegate?.voiceProvider(self, didFailWith: error)
            }
        }
    }

    private func fetchAudio(text: String) async throws -> Data {
        var request = URLRequest(url: config.baseURL.appendingPathComponent("v1/audio/speech"))
        request.httpMethod = "POST"
        request.timeoutInterval = config.timeout
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let apiKey = config.apiKey {
            request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")
        }
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "model": config.model,
            "voice": config.voice,
            "input": text,
        ])

        let (bytes, response): (URLSession.AsyncBytes, URLResponse)
        do {
            (bytes, response) = try await session.bytes(for: request)
        } catch {
            throw VoiceStudioError.transport
        }
        guard let http = response as? HTTPURLResponse else {
            throw VoiceStudioError.invalidResponse
        }
        guard (200..<300).contains(http.statusCode) else {
            throw VoiceStudioError.httpStatus(http.statusCode)
        }

        var data = Data()
        do {
            try await withTaskCancellationHandler {
                for try await byte in bytes {
                    if data.isEmpty { probe?.recordFirstByte() }
                    data.append(byte)
                }
            } onCancel: {
                bytes.task.cancel()
            }
        } catch {
            throw VoiceStudioError.transport
        }
        return data
    }
}

extension VoiceStudioProvider: AudioPlayerDelegate {
    func audioPlayerDidFinish(_ player: any AudioPlaying) {
        guard player === self.player else { return }
        self.player = nil
        isSpeaking = false
        delegate?.voiceProviderDidFinish(self)
    }
}