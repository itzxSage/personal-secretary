import CryptoKit
import Foundation

/// Retains the exact proposal across interruption so recovery never creates new event identities.
public actor EncryptedPendingWeekPlan {
    private let fileURL: URL
    private let key: SymmetricKey
    public init(directory: URL, key: SymmetricKey) {
        self.fileURL = directory.appendingPathComponent("pending-week-plan.encrypted")
        self.key = key
    }
    public func save(_ proposal: WeekPlanProposal) throws {
        try FileManager.default.createDirectory(at: fileURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        let sealed = try AES.GCM.seal(encoder.encode(proposal), using: key)
        guard let data = sealed.combined else { throw EncryptedOutboxError.missingCombinedBox }
        try data.write(to: fileURL, options: [.atomic, .completeFileProtection])
    }
    public func load() throws -> WeekPlanProposal? {
        guard FileManager.default.fileExists(atPath: fileURL.path) else { return nil }
        let sealed = try AES.GCM.SealedBox(combined: Data(contentsOf: fileURL))
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .lifeOSISO8601
        return try decoder.decode(WeekPlanProposal.self, from: AES.GCM.open(sealed, using: key))
    }
    public func clear() throws {
        if FileManager.default.fileExists(atPath: fileURL.path) { try FileManager.default.removeItem(at: fileURL) }
    }
}
