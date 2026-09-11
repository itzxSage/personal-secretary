import Foundation

/// Closed realtime operations implemented by the owned Life Engine relay.
public enum LifeEngineRealtimeTool: String, CaseIterable, Sendable {
  case restOfDay = "life_engine.plan.rest_of_day"
  case replan = "life_engine.plan.replan"

  public static func allows(endpoint: String) -> Bool {
    allCases.contains { $0.rawValue == endpoint }
  }
}

/// A continuous voice turn returned by the owned relay.
public struct VoiceRelayTurn: Sendable, Equatable {
  public let eventIDs: [Int]
  public let transcript: String
  public let spokenProgress: String
  public let spokenReply: String
  public let confirmationID: UUID?

  public init(
    eventIDs: [Int],
    transcript: String,
    spokenProgress: String,
    spokenReply: String,
    confirmationID: UUID? = nil
  ) {
    self.eventIDs = eventIDs
    self.transcript = transcript
    self.spokenProgress = spokenProgress
    self.spokenReply = spokenReply
    self.confirmationID = confirmationID
  }
}

/// Device-bound proof created by an enrolled iPhone UI, never by speech.
public struct DeviceSignedApprovalProof: Sendable, Equatable {
  public let deviceID: UUID
  public let payloadHash: String
  public let signature: String

  public init(deviceID: UUID, payloadHash: String, signature: String) {
    self.deviceID = deviceID
    self.payloadHash = payloadHash
    self.signature = signature
  }
}

/// Result of one device-approved replan.
public struct VoiceRelayApproval: Sendable, Equatable {
  public let eventIDs: [Int]
  public let spokenReply: String
  public let executionLeaseID: UUID

  public init(eventIDs: [Int], spokenReply: String, executionLeaseID: UUID) {
    self.eventIDs = eventIDs
    self.spokenReply = spokenReply
    self.executionLeaseID = executionLeaseID
  }
}

/// Canonical events missed by another owned device.
public struct VoiceRelayResume: Sendable, Equatable {
  public let deviceID: UUID
  public let eventIDs: [Int]
  public let spokenReply: String?

  public init(deviceID: UUID, eventIDs: [Int], spokenReply: String?) {
    self.deviceID = deviceID
    self.eventIDs = eventIDs
    self.spokenReply = spokenReply
  }
}

/// Only the Life Engine implements this relay. No provider or OpenClaw URL is exposed.
public protocol LifeEngineVoiceRelay: Sendable {
  func request(_ tool: LifeEngineRealtimeTool, text: String) async throws -> VoiceRelayTurn
  func cancelConfirmation(_ confirmationID: UUID) async throws
  func approveConfirmation(
    _ confirmationID: UUID,
    proof: DeviceSignedApprovalProof
  ) async throws -> VoiceRelayApproval
  func resume(afterEventID: Int, deviceID: UUID) async throws -> VoiceRelayResume
}

/// Output boundary for spoken progress, confirmations, and final replies.
public protocol SpokenReplyOutput: Sendable {
  func speak(_ text: String) async
  func stop() async
}

public enum VoiceConversationClientError: Error, Sendable, Equatable {
  case discontinuousEventID(expected: Int, received: Int)
  case confirmationUnavailable
}

public enum VoiceConfirmationResult: Sendable, Equatable {
  case cancelled
  case approved(VoiceRelayApproval)
}

/// Maintains one continuous transcript while Life Engine remains authoritative.
public actor VoiceConversationClient {
  private let invocationAdapter: InvocationAdapter
  private let relay: any LifeEngineVoiceRelay
  private let speaker: any SpokenReplyOutput
  private var pendingConfirmationID: UUID?
  private var approvedConfirmations: [UUID: VoiceRelayApproval] = [:]
  public private(set) var eventIDs: [Int] = []

  public init(
    invocationAdapter: InvocationAdapter,
    relay: any LifeEngineVoiceRelay,
    speaker: any SpokenReplyOutput
  ) {
    self.invocationAdapter = invocationAdapter
    self.relay = relay
    self.speaker = speaker
  }

  public func start(
    _ source: InvocationSource,
    values: InvocationValues
  ) async -> InvocationOutcome {
    await invocationAdapter.invoke(source, values: values)
  }

  @discardableResult
  public func send(_ text: String, tool: LifeEngineRealtimeTool) async throws -> VoiceRelayTurn {
    let turn = try await relay.request(tool, text: text)
    try appendContinuous(turn.eventIDs)
    await speaker.speak(turn.spokenProgress)
    await speaker.speak(turn.spokenReply)
    pendingConfirmationID = turn.confirmationID
    return turn
  }

  public func interruptConfirmation() async throws -> VoiceConfirmationResult {
    guard let confirmationID = pendingConfirmationID else {
      throw VoiceConversationClientError.confirmationUnavailable
    }
    try await relay.cancelConfirmation(confirmationID)
    await speaker.stop()
    pendingConfirmationID = nil
    return .cancelled
  }

  public func approveConfirmation(
    _ confirmationID: UUID,
    proof: DeviceSignedApprovalProof
  ) async throws -> VoiceConfirmationResult {
    if let existing = approvedConfirmations[confirmationID] {
      return .approved(existing)
    }
    guard pendingConfirmationID == confirmationID else {
      throw VoiceConversationClientError.confirmationUnavailable
    }
    let approval = try await relay.approveConfirmation(confirmationID, proof: proof)
    try appendContinuous(approval.eventIDs)
    approvedConfirmations[confirmationID] = approval
    pendingConfirmationID = nil
    await speaker.speak(approval.spokenReply)
    return .approved(approval)
  }

  public func resume(on deviceID: UUID, afterEventID: Int) async throws -> VoiceRelayResume {
    let resumed = try await relay.resume(afterEventID: afterEventID, deviceID: deviceID)
    try Self.requireContinuous(resumed.eventIDs, after: afterEventID)
    if let spokenReply = resumed.spokenReply {
      await speaker.speak(spokenReply)
    }
    return resumed
  }

  private func appendContinuous(_ incoming: [Int]) throws {
    let cursor = eventIDs.last ?? 0
    try Self.requireContinuous(incoming, after: cursor)
    eventIDs.append(contentsOf: incoming)
  }

  private static func requireContinuous(_ incoming: [Int], after cursor: Int) throws {
    var expected = cursor + 1
    for eventID in incoming {
      guard eventID == expected else {
        throw VoiceConversationClientError.discontinuousEventID(
          expected: expected,
          received: eventID
        )
      }
      expected += 1
    }
  }
}
