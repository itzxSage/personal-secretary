import Foundation
import Testing
import SecretaryContract

/// Canonical cross-language exchange document produced by the Python SDK.
private struct ExpectedResult: Codable, Equatable, Sendable {
    let accepted: Bool
    let rejection: ConversationRejection?
    let nextSequence: Int

    private enum CodingKeys: String, CodingKey {
        case accepted
        case rejection
        case nextSequence = "next_sequence"
    }
}

private struct ExchangeCase: Codable, Sendable {
    let name: String
    let events: [ConversationEvent]
    let expected: [ExpectedResult]
}

private struct ExchangeDocument: Codable, Sendable {
    let contractVersion: String
    let conversation: Conversation
    let devices: [ConversationDevice]
    let participants: [ConversationParticipant]
    let channelBinding: ChannelBinding
    let cases: [ExchangeCase]

    private enum CodingKeys: String, CodingKey {
        case contractVersion = "contract_version"
        case conversation
        case devices
        case participants
        case channelBinding = "channel_binding"
        case cases
    }
}

private enum ExchangeFixture {
    /// Prefer the live artifact written by the Python test run; fall back to the
    /// versioned canonical fixture so `swift test` also passes standalone.
    static let url: URL = {
        let sourceFile = URL(fileURLWithPath: #filePath)
        let repoRoot = sourceFile
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        let live = repoRoot.appendingPathComponent(
            "artifacts/verification/task-4/exchange/conversation-contract.json"
        )
        if FileManager.default.fileExists(atPath: live.path) {
            return live
        }
        return repoRoot.appendingPathComponent(
            "contracts/v1/fixtures/conversation-contract.json"
        )
    }()

    static func load() throws -> ExchangeDocument {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .lifeOSISO8601
        let data = try Data(contentsOf: url)
        return try decoder.decode(ExchangeDocument.self, from: data)
    }
}

@Test("decodes canonical exchange document with typed conversation state")
func decodesCanonicalExchangeDocument() throws {
    // Given: the canonical fixture serialized by the Python SDK.
    let fixture = try ExchangeFixture.load()

    // Then: every typed conversation surface decodes with the expected values.
    #expect(fixture.contractVersion == "1.0.0")
    #expect(fixture.conversation.title == "Morning planning")
    #expect(fixture.conversation.participantIds.count == 2)
    #expect(fixture.conversation.deviceIds.count == 2)
    #expect(fixture.devices.count == 2)
    #expect(fixture.participants.count == 2)
    #expect(fixture.channelBinding.channelKind == .telegram)
    #expect(fixture.channelBinding.state == .active)
    #expect(fixture.cases.count == 12)
}

@Test("accepts cross-device conversation events")
func acceptsCrossDeviceConversationEvents() throws {
    // Given: a conversation bound to two devices of the same participant.
    let fixture = try ExchangeFixture.load()
    let exchangeCase = try #require(fixture.cases.first { $0.name == "accepted_cross_device" })
    let journal = ConversationJournal(conversation: fixture.conversation, devices: fixture.devices)

    // When: interleaved events from both devices arrive in sequence order.
    let results = exchangeCase.events.map { journal.accept($0) }

    // Then: every event is accepted and the sequence advances monotonically.
    #expect(results.map(\.accepted) == [true, true, true, true, true])
    #expect(results.map(\.nextSequence) == [2, 3, 4, 5, 6])
    #expect(results.allSatisfy { $0.rejection == nil })
}

@Test("rejects duplicate events")
func rejectsDuplicateEvents() throws {
    // Given: a journal that already accepted an event.
    let fixture = try ExchangeFixture.load()
    let exchangeCase = try #require(fixture.cases.first { $0.name == "rejected_duplicate" })
    let journal = ConversationJournal(conversation: fixture.conversation, devices: fixture.devices)

    // When: the identical event is delivered again.
    let results = exchangeCase.events.map { journal.accept($0) }

    // Then: the duplicate is rejected with a typed reason and no sequence advance.
    #expect(results[0].accepted)
    #expect(results[1].accepted == false)
    #expect(results[1].rejection == .duplicateEvent)
    #expect(results[1].nextSequence == 2)
}

@Test("rejects out-of-order events and preserves the expected sequence")
func rejectsOutOfOrderEvents() throws {
    // Given: a journal expecting the next sequence position.
    let fixture = try ExchangeFixture.load()
    let exchangeCase = try #require(fixture.cases.first { $0.name == "rejected_out_of_order" })
    let journal = ConversationJournal(conversation: fixture.conversation, devices: fixture.devices)

    // When: a gapped event arrives, then the missing event, then a replay.
    let results = exchangeCase.events.map { journal.accept($0) }

    // Then: the gap is rejected, the missing event recovers, and the replay is a duplicate.
    #expect(results[0].accepted)
    #expect(results[1].accepted == false)
    #expect(results[1].rejection == .outOfOrder)
    #expect(results[1].nextSequence == 2)
    #expect(results[2].accepted)
    #expect(results[2].nextSequence == 3)
    #expect(results[3].accepted == false)
    #expect(results[3].rejection == .duplicateEvent)
}

@Test("rejects foreign-user events")
func rejectsForeignUserEvents() throws {
    // Given: a conversation that does not include a foreign participant.
    let fixture = try ExchangeFixture.load()
    let exchangeCase = try #require(fixture.cases.first { $0.name == "rejected_foreign_participant" })
    let journal = ConversationJournal(conversation: fixture.conversation, devices: fixture.devices)

    // When: the foreign participant sends an event.
    let result = journal.accept(exchangeCase.events[0])

    // Then: the foreign-user event is rejected without advancing the sequence.
    #expect(result.accepted == false)
    #expect(result.rejection == .foreignParticipant)
    #expect(result.nextSequence == 1)
}

@Test("rejects foreign-device events")
func rejectsForeignDeviceEvents() throws {
    // Given: a conversation that does not include a foreign device.
    let fixture = try ExchangeFixture.load()
    let exchangeCase = try #require(fixture.cases.first { $0.name == "rejected_foreign_device" })
    let journal = ConversationJournal(conversation: fixture.conversation, devices: fixture.devices)

    // When: an unbound device sends an event.
    let result = journal.accept(exchangeCase.events[0])

    // Then: the foreign-device event is rejected.
    #expect(result.accepted == false)
    #expect(result.rejection == .foreignDevice)
    #expect(result.nextSequence == 1)
}

@Test("rejects device-participant binding mismatches")
func rejectsDeviceParticipantMismatch() throws {
    // Given: a conversation where the agent owns no device.
    let fixture = try ExchangeFixture.load()
    let exchangeCase = try #require(fixture.cases.first { $0.name == "rejected_device_participant_mismatch" })
    let journal = ConversationJournal(conversation: fixture.conversation, devices: fixture.devices)

    // When: the agent sends an event through the user's iPhone.
    let result = journal.accept(exchangeCase.events[0])

    // Then: the binding mismatch is rejected.
    #expect(result.accepted == false)
    #expect(result.rejection == .deviceParticipantMismatch)
    #expect(result.nextSequence == 1)
}

@Test("rejects envelope/payload kind mismatches")
func rejectsKindMismatch() throws {
    // Given: a journal expecting a transcript event.
    let fixture = try ExchangeFixture.load()
    let exchangeCase = try #require(fixture.cases.first { $0.name == "rejected_kind_mismatch" })
    let journal = ConversationJournal(conversation: fixture.conversation, devices: fixture.devices)

    // When: the envelope kind contradicts the payload kind.
    let result = journal.accept(exchangeCase.events[0])

    // Then: the contradictory event is rejected.
    #expect(result.accepted == false)
    #expect(result.rejection == .kindMismatch)
    #expect(result.nextSequence == 1)
}

@Test("rejects final transcripts without a preceding partial")
func rejectsFinalWithoutPartial() throws {
    // Given: a journal with no open transcript turn.
    let fixture = try ExchangeFixture.load()
    let exchangeCase = try #require(fixture.cases.first { $0.name == "rejected_final_without_partial" })
    let journal = ConversationJournal(conversation: fixture.conversation, devices: fixture.devices)

    // When: a final transcript arrives without any preceding partial.
    let result = journal.accept(exchangeCase.events[0])

    // Then: the orphan final is rejected.
    #expect(result.accepted == false)
    #expect(result.rejection == .finalWithoutPartial)
    #expect(result.nextSequence == 1)
}

@Test("rejects events after cancellation")
func rejectsEventAfterCancellation() throws {
    // Given: a turn that was cancelled by barge-in.
    let fixture = try ExchangeFixture.load()
    let exchangeCase = try #require(fixture.cases.first { $0.name == "rejected_event_after_cancellation" })
    let journal = ConversationJournal(conversation: fixture.conversation, devices: fixture.devices)

    // When: the cancelled turn produces another partial.
    let results = exchangeCase.events.map { journal.accept($0) }

    // Then: the cancelled turn is closed to further events.
    #expect(results[0].accepted)
    #expect(results[1].accepted)
    #expect(results[2].accepted == false)
    #expect(results[2].rejection == .eventAfterCancellation)
    #expect(results[2].nextSequence == 3)
}

@Test("rejects resume without a preceding cancellation")
func rejectsResumeWithoutCancellation() throws {
    // Given: a journal with no cancelled turn to resume from.
    let fixture = try ExchangeFixture.load()
    let exchangeCase = try #require(fixture.cases.first { $0.name == "rejected_resume_without_cancellation" })
    let journal = ConversationJournal(conversation: fixture.conversation, devices: fixture.devices)

    // When: a resume references a turn that was never cancelled.
    let result = journal.accept(exchangeCase.events[0])

    // Then: the orphan resume is rejected.
    #expect(result.accepted == false)
    #expect(result.rejection == .resumeWithoutCancellation)
    #expect(result.nextSequence == 1)
}

@Test("accepts cancellation then resume")
func acceptsCancellationThenResume() throws {
    // Given: a conversation journal.
    let fixture = try ExchangeFixture.load()
    let exchangeCase = try #require(fixture.cases.first { $0.name == "accepted_cancel_resume" })
    let journal = ConversationJournal(conversation: fixture.conversation, devices: fixture.devices)

    // When: a turn is cancelled and a new turn resumes from it.
    let results = exchangeCase.events.map { journal.accept($0) }

    // Then: cancellation closes the old turn and resume opens a new one.
    #expect(results.map(\.accepted) == [true, true, true, true, true])
    #expect(results.map(\.nextSequence) == [2, 3, 4, 5, 6])
}

@Test("accepts attachment, tool-result, and error events")
func acceptsAttachmentToolResultAndErrorEvents() throws {
    // Given: a conversation journal.
    let fixture = try ExchangeFixture.load()
    let exchangeCase = try #require(fixture.cases.first { $0.name == "accepted_mixed_payloads" })
    let journal = ConversationJournal(conversation: fixture.conversation, devices: fixture.devices)

    // When: attachment, tool-result, and error events arrive in order.
    let results = exchangeCase.events.map { journal.accept($0) }

    // Then: all three payload kinds are accepted in sequence.
    #expect(results.map(\.accepted) == [true, true, true])
    #expect(results.map(\.nextSequence) == [2, 3, 4])
}

@Test("every exchange case matches the canonical expected outcomes")
func everyExchangeCaseMatchesCanonicalOutcomes() throws {
    // Given: the canonical exchange document.
    let fixture = try ExchangeFixture.load()

    // When: every case is replayed through a fresh journal.
    for exchangeCase in fixture.cases {
        let journal = ConversationJournal(conversation: fixture.conversation, devices: fixture.devices)
        let results = exchangeCase.events.map { journal.accept($0) }

        // Then: each step matches the pinned expected outcome.
        #expect(results.count == exchangeCase.expected.count, "case \(exchangeCase.name)")
        for (result, expected) in zip(results, exchangeCase.expected) {
            #expect(result.accepted == expected.accepted, "case \(exchangeCase.name)")
            #expect(result.rejection == expected.rejection, "case \(exchangeCase.name)")
            #expect(result.nextSequence == expected.nextSequence, "case \(exchangeCase.name)")
        }
    }
}

@Test("stale ordering state rejects replayed events")
func staleOrderingStateRejectsReplayedEvents() throws {
    // Given: a journal that already consumed the full cross-device sequence.
    let fixture = try ExchangeFixture.load()
    let exchangeCase = try #require(fixture.cases.first { $0.name == "accepted_cross_device" })
    let journal = ConversationJournal(conversation: fixture.conversation, devices: fixture.devices)
    _ = exchangeCase.events.map { journal.accept($0) }

    // When: the first event is replayed against the stale ordering state.
    let replay = journal.accept(exchangeCase.events[0])

    // Then: the replay is rejected as a duplicate, not misreported as out of order.
    #expect(replay.accepted == false)
    #expect(replay.rejection == .duplicateEvent)
    #expect(replay.nextSequence == 6)
}

@Test("rejects malformed conversation event JSON")
func rejectsMalformedConversationEventJSON() {
    // Given: malformed event bytes that cannot satisfy the typed schema.
    let malformed = #"{"event_id": "not-a-uuid", "sequence": 0}"#
    let data = Data(malformed.utf8)
    let decoder = JSONDecoder()
    decoder.dateDecodingStrategy = .lifeOSISO8601

    // When: the malformed bytes reach the typed decoder.
    // Then: decoding fails closed instead of producing a partial event.
    #expect(throws: DecodingError.self) {
        _ = try decoder.decode(ConversationEvent.self, from: data)
    }
}