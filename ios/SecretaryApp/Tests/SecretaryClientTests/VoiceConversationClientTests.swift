import Foundation
import SecretaryClient
import SecretaryContract
import Testing

private actor VoiceSpeakerStub: SpokenReplyOutput {
  private var utterances: [String] = []
  private var stopCount = 0

  func speak(_ text: String) async {
    utterances.append(text)
  }

  func stop() async {
    stopCount += 1
  }

  func snapshot() -> ([String], Int) {
    (utterances, stopCount)
  }
}

private actor VoiceRelayStub: LifeEngineVoiceRelay {
  private(set) var requestedTools: [LifeEngineRealtimeTool] = []
  private(set) var approvalCount = 0
  private(set) var cancellationCount = 0

  func request(_ tool: LifeEngineRealtimeTool, text: String) async throws -> VoiceRelayTurn {
    requestedTools.append(tool)
    let firstEventID = (requestedTools.count - 1) * 3 + 1
    let eventIDs = Array(firstEventID...firstEventID + 2)
    switch tool {
    case .restOfDay:
      return VoiceRelayTurn(
        eventIDs: eventIDs,
        transcript: text,
        spokenProgress: "Checking your Life Plan.",
        spokenReply: "Workout is the only flexible item left today."
      )
    case .replan:
      return VoiceRelayTurn(
        eventIDs: eventIDs,
        transcript: text,
        spokenProgress: "Checking the workout constraints.",
        spokenReply: "I can move the workout to 6 PM. Confirm on your iPhone.",
        confirmationID: UUID(uuidString: "99999999-9999-4999-8999-999999999999")
      )
    }
  }

  func cancelConfirmation(_ confirmationID: UUID) async throws {
    cancellationCount += 1
  }

  func approveConfirmation(
    _ confirmationID: UUID,
    proof: DeviceSignedApprovalProof
  ) async throws -> VoiceRelayApproval {
    approvalCount += 1
    return VoiceRelayApproval(
      eventIDs: [requestedTools.count * 3 + 1],
      spokenReply: "Workout moved to 6 PM.",
      executionLeaseID: UUID(uuidString: "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa")!
    )
  }

  func resume(afterEventID: Int, deviceID: UUID) async throws -> VoiceRelayResume {
    VoiceRelayResume(deviceID: deviceID, eventIDs: [5, 6], spokenReply: nil)
  }

  func snapshot() -> ([LifeEngineRealtimeTool], Int, Int) {
    (requestedTools, approvalCount, cancellationCount)
  }
}

private struct VoiceStartCommandStub: ConversationCommand {
  let event: ConversationEvent

  func execute(values: InvocationValues) async throws -> ConversationEvent {
    event
  }
}

private let voiceStartEvent: ConversationEvent = {
  let data = Data(
    """
    {
      "event_id": "66666666-6666-4666-8666-000000000001",
      "conversation_id": "55555555-5555-4555-8555-555555555555",
      "participant_id": "11111111-1111-4111-8111-111111111111",
      "device_id": "33333333-3333-4333-8333-333333333333",
      "sequence": 1,
      "occurred_at": "2026-09-06T12:00:00Z",
      "kind": "transcript_partial",
      "payload": {
        "kind": "transcript_partial",
        "turn_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "text": "voice_session_started",
        "is_final": false
      }
    }
    """.utf8
  )
  let decoder = JSONDecoder()
  decoder.dateDecodingStrategy = .lifeOSISO8601
  return try! decoder.decode(ConversationEvent.self, from: data)
}()

private let invocationValues = InvocationValues(
  eventID: voiceStartEvent.eventId,
  turnID: UUID(uuidString: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")!,
  occurredAt: voiceStartEvent.occurredAt,
  sequence: 1
)

@Test("continuous voice turns use only typed Life Engine tools and speak replies")
func continuousVoiceTurnsSpeakReplies() async throws {
  // Given: a client whose start path is InvocationAdapter -> StartVoiceSession command.
  let relay = VoiceRelayStub()
  let speaker = VoiceSpeakerStub()
  let client = VoiceConversationClient(
    invocationAdapter: InvocationAdapter(
      startVoiceSession: VoiceStartCommandStub(event: voiceStartEvent)
    ),
    relay: relay,
    speaker: speaker
  )

  // When: one invocation continues through two voice turns.
  _ = await client.start(.pushToTalk, values: invocationValues)
  _ = try await client.send("what's left", tool: .restOfDay)
  _ = try await client.send("move workout", tool: .replan)

  // Then: event IDs are continuous and progress plus replies are spoken in order.
  #expect(await client.eventIDs == [1, 2, 3, 4, 5, 6])
  #expect(await relay.snapshot().0 == [.restOfDay, .replan])
  #expect(
    await speaker.snapshot().0 == [
      "Checking your Life Plan.",
      "Workout is the only flexible item left today.",
      "Checking the workout constraints.",
      "I can move the workout to 6 PM. Confirm on your iPhone.",
    ])
  #expect(!LifeEngineRealtimeTool.allows(endpoint: "openclaw.worker.execute"))
}

@Test("interrupted spoken approval is invalid and creates no lease")
func interruptedApprovalCreatesNoLease() async throws {
  // Given: a pending replan confirmation.
  let relay = VoiceRelayStub()
  let speaker = VoiceSpeakerStub()
  let client = VoiceConversationClient(
    invocationAdapter: InvocationAdapter(
      startVoiceSession: VoiceStartCommandStub(event: voiceStartEvent)
    ),
    relay: relay,
    speaker: speaker
  )
  _ = try await client.send("move workout", tool: .replan)

  // When: speech playback is interrupted rather than device-approved.
  let result = try await client.interruptConfirmation()

  // Then: no identity proof or execution lease is created.
  #expect(result == .cancelled)
  #expect(await relay.snapshot().1 == 0)
  #expect(await relay.snapshot().2 == 1)
  #expect(await speaker.snapshot().1 == 1)
}

@Test("device-signed confirmation approves a replan exactly once")
func deviceConfirmationApprovesExactlyOnce() async throws {
  // Given: a pending replan and proof created by the enrolled iPhone UI.
  let relay = VoiceRelayStub()
  let speaker = VoiceSpeakerStub()
  let client = VoiceConversationClient(
    invocationAdapter: InvocationAdapter(
      startVoiceSession: VoiceStartCommandStub(event: voiceStartEvent)
    ),
    relay: relay,
    speaker: speaker
  )
  let turn = try await client.send("move workout", tool: .replan)
  let confirmationID = try #require(turn.confirmationID)
  let proof = DeviceSignedApprovalProof(
    deviceID: voiceStartEvent.deviceId,
    payloadHash: "fixture-payload-hash",
    signature: "fixture-device-signature"
  )

  // When: the same device confirmation is delivered twice.
  let first = try await client.approveConfirmation(confirmationID, proof: proof)
  let replay = try await client.approveConfirmation(confirmationID, proof: proof)

  // Then: one relay approval creates one lease and the golden result is spoken once.
  #expect(first == replay)
  #expect(await relay.snapshot().1 == 1)
  #expect(await speaker.snapshot().0.last == "Workout moved to 6 PM.")
}

@Test("cross-device resume preserves the Life Engine event cursor")
func crossDeviceResumePreservesCursor() async throws {
  // Given: a client that has received the first continuous voice turn.
  let relay = VoiceRelayStub()
  let speaker = VoiceSpeakerStub()
  let client = VoiceConversationClient(
    invocationAdapter: InvocationAdapter(
      startVoiceSession: VoiceStartCommandStub(event: voiceStartEvent)
    ),
    relay: relay,
    speaker: speaker
  )
  _ = try await client.send("what's left", tool: .restOfDay)
  let deviceID = UUID(uuidString: "77777777-7777-4777-8777-777777777777")!

  // When: another owned device resumes after event four.
  let resume = try await client.resume(on: deviceID, afterEventID: 4)

  // Then: the same canonical cursor continues without a new local transcript.
  #expect(resume.deviceID == deviceID)
  #expect(resume.eventIDs == [5, 6])
}
