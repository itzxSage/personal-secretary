import CryptoKit
import Foundation
import SecretaryContract

/// Errors produced by the encrypted outbox.
public enum EncryptedOutboxError: Error, Sendable {
    case missingCombinedBox
}

/// Encrypted offline outbox. Events are sealed with AES-GCM before touching
/// disk, and actor isolation serializes queue updates.
public actor EncryptedOutbox: ConversationEventBatchSink {
    public nonisolated let directory: URL
    private let key: SymmetricKey

    public init(directory: URL, key: SymmetricKey) {
        self.directory = directory
        self.key = key
    }

    public nonisolated var fileURL: URL {
        directory.appendingPathComponent("outbox.encrypted")
    }

    public func send(_ event: ConversationEvent) async throws {
        try await send([event])
    }

    public func send(_ newEvents: [ConversationEvent]) async throws {
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true
        )
        var events = try await pendingEvents()
        events.append(contentsOf: newEvents)
        try write(events)
    }

    public func pendingEvents() async throws -> [ConversationEvent] {
        guard FileManager.default.fileExists(atPath: fileURL.path) else {
            return []
        }
        let sealed = try Data(contentsOf: fileURL)
        let box = try AES.GCM.SealedBox(combined: sealed)
        let data = try AES.GCM.open(box, using: key)
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .lifeOSISO8601
        return try decoder.decode([ConversationEvent].self, from: data)
    }

    public func acknowledge(eventIDs: [UUID]) async throws {
        let acknowledged = Set(eventIDs)
        let pending = try await pendingEvents()
        var cursors = try deliveryCursors()
        for event in pending where acknowledged.contains(event.eventId) {
            let conversation = event.conversationId.uuidString
            cursors[conversation] = max(cursors[conversation] ?? 0, event.sequence)
        }
        // Persist the high-water mark BEFORE removing ciphertext. A crash between writes
        // leaves retryable duplicate events, never a forgotten sequence or lost delivery.
        if !cursors.isEmpty { try writeCursors(cursors) }
        let remaining = pending.filter { !acknowledged.contains($0.eventId) }
        if remaining.isEmpty {
            if FileManager.default.fileExists(atPath: fileURL.path) {
                try FileManager.default.removeItem(at: fileURL)
            }
            return
        }
        try write(remaining)
    }

    public func nextSequence(conversationID: UUID) async throws -> Int {
        let delivered = try deliveryCursors()[conversationID.uuidString] ?? 0
        let queued = try await pendingEvents()
            .filter { $0.conversationId == conversationID }.map(\.sequence).max() ?? 0
        return max(delivered, queued) + 1
    }

    public func purge(conversationID: UUID) async throws {
        let remaining = try await pendingEvents().filter { $0.conversationId != conversationID }
        var cursors = try deliveryCursors()
        cursors.removeValue(forKey: conversationID.uuidString)
        if remaining.isEmpty {
            if FileManager.default.fileExists(atPath: fileURL.path) {
                try FileManager.default.removeItem(at: fileURL)
            }
        } else {
            try write(remaining)
        }
        if cursors.isEmpty {
            if FileManager.default.fileExists(atPath: cursorURL.path) {
                try FileManager.default.removeItem(at: cursorURL)
            }
        } else {
            try writeCursors(cursors)
        }
    }

    private var cursorURL: URL { directory.appendingPathComponent("delivery-cursors.encrypted") }

    private func deliveryCursors() throws -> [String: Int] {
        guard FileManager.default.fileExists(atPath: cursorURL.path) else { return [:] }
        let box = try AES.GCM.SealedBox(combined: Data(contentsOf: cursorURL))
        return try JSONDecoder().decode([String: Int].self, from: AES.GCM.open(box, using: key))
    }

    private func writeCursors(_ cursors: [String: Int]) throws {
        let sealed = try AES.GCM.seal(JSONEncoder().encode(cursors), using: key)
        guard let combined = sealed.combined else { throw EncryptedOutboxError.missingCombinedBox }
        try combined.write(to: cursorURL, options: [.atomic, .completeFileProtection])
    }

    private func write(_ events: [ConversationEvent]) throws {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        let data = try encoder.encode(events)
        let sealed = try AES.GCM.seal(data, using: key)
        guard let combined = sealed.combined else {
            throw EncryptedOutboxError.missingCombinedBox
        }
        try combined.write(to: fileURL, options: [.atomic, .completeFileProtection])
    }
}
