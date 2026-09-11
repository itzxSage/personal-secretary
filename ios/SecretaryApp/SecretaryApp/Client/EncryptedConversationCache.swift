import CryptoKit
import Foundation
import SecretaryContract

/// Encrypted delivered history for offline display. The server remains canonical.
public actor EncryptedConversationCache {
    public nonisolated let fileURL: URL
    private let key: SymmetricKey

    public init(directory: URL, key: SymmetricKey) {
        self.fileURL = directory.appendingPathComponent("conversation-cache.encrypted")
        self.key = key
    }

    public func events(conversationID: UUID) throws -> [ConversationEvent] {
        try records()[conversationID.uuidString] ?? []
    }

    public func replace(conversationID: UUID, events: [ConversationEvent]) throws {
        guard events.allSatisfy({ $0.conversationId == conversationID }) else {
            throw ConversationRelayError.invalidResponse
        }
        try FileManager.default.createDirectory(
            at: fileURL.deletingLastPathComponent(), withIntermediateDirectories: true
        )
        var current = try records()
        current[conversationID.uuidString] = events
        try write(current)
    }

    public func purge(conversationID: UUID) throws {
        var current = try records()
        current.removeValue(forKey: conversationID.uuidString)
        if current.isEmpty {
            if FileManager.default.fileExists(atPath: fileURL.path) {
                try FileManager.default.removeItem(at: fileURL)
            }
        } else {
            try write(current)
        }
    }

    private func records() throws -> [String: [ConversationEvent]] {
        guard FileManager.default.fileExists(atPath: fileURL.path) else { return [:] }
        let box = try AES.GCM.SealedBox(combined: Data(contentsOf: fileURL))
        let data = try AES.GCM.open(box, using: key)
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .lifeOSISO8601
        return try decoder.decode([String: [ConversationEvent]].self, from: data)
    }

    private func write(_ records: [String: [ConversationEvent]]) throws {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        let sealed = try AES.GCM.seal(encoder.encode(records), using: key)
        guard let combined = sealed.combined else {
            throw EncryptedOutboxError.missingCombinedBox
        }
        try combined.write(to: fileURL, options: [.atomic, .completeFileProtection])
    }
}
