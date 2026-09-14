import CryptoKit
import Foundation

public enum WeekPlanStatus: String, Codable, Sendable {
    case feasible
    case partial
    case infeasible
}

public enum WeekPlanBlockKind: String, Codable, Sendable {
    case fixed
    case flexible
    case protected
    case travel
    case available
}

public enum WeekPlanExplanationCode: String, Codable, Sendable {
    case fixed
    case protected
    case scheduled
    case overdue
    case infeasible
}

public enum WeekPlanGapReason: String, Codable, Sendable {
    case unknownTime = "unknown_time"
}

public enum CalendarDryRunOperationKind: String, Codable, Sendable {
    case insert
    case update
    case noop
    case externalEdit = "external_edit"
    case conflict
    case delete
    case missing
}

public struct WeekPlanProposal: Codable, Sendable {
    public let proposalID: UUID
    public let payloadHash: String
    public let status: WeekPlanStatus
    public let planDate: String
    public let timezone: String
    public let windowStart: Date
    public let windowEnd: Date
    public let blocks: [WeekPlanBlock]
    public let calendarBlocks: [WeekPlanCalendarBlock]
    public let explanations: [WeekPlanExplanation]
    public let unscheduled: [WeekPlanUnscheduledActivity]
    public let gaps: [WeekPlanGap]
    public let calendarDryRun: CalendarDryRun

    enum CodingKeys: String, CodingKey {
        case proposalID = "proposal_id"
        case payloadHash = "payload_hash"
        case status
        case planDate = "plan_date"
        case timezone
        case windowStart = "window_start"
        case windowEnd = "window_end"
        case blocks
        case calendarBlocks = "calendar_blocks"
        case explanations, unscheduled, gaps
        case calendarDryRun = "calendar_dry_run"
    }
}

public struct WeekPlanBlock: Codable, Sendable {
    public let blockID: String
    public let activityID: String?
    public let title: String
    public let kind: WeekPlanBlockKind
    public let startsAt: Date
    public let endsAt: Date
    public let calendarEligible: Bool

    enum CodingKeys: String, CodingKey {
        case blockID = "block_id"
        case activityID = "activity_id"
        case title, kind
        case startsAt = "starts_at"
        case endsAt = "ends_at"
        case calendarEligible = "calendar_eligible"
    }
}

public struct WeekPlanCalendarBlock: Codable, Sendable {
    public let blockID: String
    public let displayGroup: String
    public let title: String
    public let startsAt: Date
    public let endsAt: Date
    public let sourceActivityIDs: [String]
    public let segmentTitles: [String]
    public let transitions: [String]
    public let explanationCodes: [String]

    enum CodingKeys: String, CodingKey {
        case blockID = "block_id"
        case displayGroup = "display_group"
        case title
        case startsAt = "starts_at"
        case endsAt = "ends_at"
        case sourceActivityIDs = "source_activity_ids"
        case segmentTitles = "segment_titles"
        case transitions
        case explanationCodes = "explanation_codes"
    }
}

public struct WeekPlanExplanation: Codable, Sendable {
    public let activityID: String
    public let code: WeekPlanExplanationCode
    public let summary: String
    public let score: WeekPlanScore
    public let scheduledBlockIDs: [String]
    public let constraints: [String]

    enum CodingKeys: String, CodingKey {
        case activityID = "activity_id"
        case code, summary, score
        case scheduledBlockIDs = "scheduled_block_ids"
        case constraints
    }
}

public struct WeekPlanScore: Codable, Sendable {
    public let deadline: Int
    public let dependency: Int
    public let travel: Int
    public let energy: Int
    public let goal: Int
    public let importance: Int
    public let total: Int
}

public struct WeekPlanUnscheduledActivity: Codable, Sendable {
    public let activityID: String
    public let reason: String
    public let detail: String
    public let blockingActivityIDs: [String]

    enum CodingKeys: String, CodingKey {
        case activityID = "activity_id"
        case reason, detail
        case blockingActivityIDs = "blocking_activity_ids"
    }
}

public struct WeekPlanGap: Codable, Sendable {
    public let memoryID: UUID
    public let title: String
    public let reason: WeekPlanGapReason
    public let explanation: String

    enum CodingKeys: String, CodingKey {
        case memoryID = "memory_id"
        case title, reason, explanation
    }
}

public struct CalendarDryRun: Codable, Sendable {
    public let calendarSummary: String
    public let operations: [CalendarDryRunOperation]

    enum CodingKeys: String, CodingKey {
        case calendarSummary = "calendar_summary"
        case operations
    }
}

public struct CalendarDryRunOperation: Codable, Sendable {
    public let operation: CalendarDryRunOperationKind
    public let eventID: String
    public let beforeSummary: String?
    public let afterSummary: String?

    enum CodingKeys: String, CodingKey {
        case operation
        case eventID = "event_id"
        case beforeSummary = "before_summary"
        case afterSummary = "after_summary"
    }
}

public struct LifeOSApprovalInput: Sendable {
    public let factID: UUID
    public let proposalID: UUID
    public let payloadHash: String
    public let issuedAt: Date
    public let expiresAt: Date
    public let idempotencyKey: String
    public let actor: String
    public let correlationID: UUID
    public let deviceID: UUID

    public init(
        factID: UUID, proposalID: UUID, payloadHash: String, issuedAt: Date, expiresAt: Date,
        idempotencyKey: String, actor: String, correlationID: UUID, deviceID: UUID
    ) {
        self.factID = factID
        self.proposalID = proposalID
        self.payloadHash = payloadHash
        self.issuedAt = issuedAt
        self.expiresAt = expiresAt
        self.idempotencyKey = idempotencyKey
        self.actor = actor
        self.correlationID = correlationID
        self.deviceID = deviceID
    }
}

public enum LifeOSApprovalProof: String, Codable, Sendable {
    case deviceSigned = "device_signed"
}

public struct LifeOSApproval: Codable, Equatable, Sendable {
    public let factID: UUID
    public let proposalID: UUID
    public let payloadHash: String
    public let issuedAt: String
    public let expiresAt: String
    public let idempotencyKey: String
    public let actor: String
    public let correlationID: UUID
    public let proof: LifeOSApprovalProof
    public let deviceID: UUID
    public let signature: String

    enum CodingKeys: String, CodingKey {
        case factID = "fact_id"
        case proposalID = "proposal_id"
        case payloadHash = "payload_hash"
        case issuedAt = "issued_at"
        case expiresAt = "expires_at"
        case idempotencyKey = "idempotency_key"
        case actor
        case correlationID = "correlation_id"
        case proof
        case deviceID = "device_id"
        case signature
    }
}

public protocol LifeOSApprovalSigning: Sendable {
    func approval(for input: LifeOSApprovalInput, actionClass: String) throws -> LifeOSApproval
}

public struct LifeOSApprovalSigner: LifeOSApprovalSigning, Sendable {
    private let signingKey: Curve25519.Signing.PrivateKey

    public init(signingKey: Curve25519.Signing.PrivateKey) {
        self.signingKey = signingKey
    }

    public func approval(for input: LifeOSApprovalInput, actionClass: String) throws -> LifeOSApproval {
        let unsigned = LifeOSApproval(
            factID: input.factID, proposalID: input.proposalID, payloadHash: input.payloadHash,
            issuedAt: Self.pythonISO8601(input.issuedAt), expiresAt: Self.pythonISO8601(input.expiresAt),
            idempotencyKey: input.idempotencyKey, actor: input.actor,
            correlationID: input.correlationID, proof: .deviceSigned, deviceID: input.deviceID,
            signature: ""
        )
        let signature = try signingKey.signature(for: signingData(for: unsigned, actionClass: actionClass))
        return LifeOSApproval(
            factID: unsigned.factID, proposalID: unsigned.proposalID, payloadHash: unsigned.payloadHash,
            issuedAt: unsigned.issuedAt, expiresAt: unsigned.expiresAt,
            idempotencyKey: unsigned.idempotencyKey, actor: unsigned.actor,
            correlationID: unsigned.correlationID, proof: unsigned.proof, deviceID: unsigned.deviceID,
            signature: signature.map { String(format: "%02x", $0) }.joined()
        )
    }

    public func signingData(for approval: LifeOSApproval, actionClass: String) throws -> Data {
        try Self.canonicalSigningData(actionClass: actionClass, fields: [
            "actor": approval.actor,
            "correlation_id": approval.correlationID.uuidString.lowercased(),
            "device_id": approval.deviceID.uuidString.lowercased(),
            "expires_at": approval.expiresAt,
            "fact_id": approval.factID.uuidString.lowercased(),
            "idempotency_key": approval.idempotencyKey,
            "issued_at": approval.issuedAt,
            "payload_hash": approval.payloadHash,
            "proof": approval.proof.rawValue,
            "proposal_id": approval.proposalID.uuidString.lowercased(),
        ])
    }

    public static func canonicalSigningData(
        actionClass: String, fields: [String: String]
    ) throws -> Data {
        let requiredKeys: Set<String> = [
            "actor", "correlation_id", "device_id", "expires_at", "fact_id",
            "idempotency_key", "issued_at", "payload_hash", "proof", "proposal_id",
        ]
        guard Set(fields.keys) == requiredKeys,
            actionClass.unicodeScalars.allSatisfy({ $0.isASCII }),
            fields.values.allSatisfy({ $0.unicodeScalars.allSatisfy(\.isASCII) }) else {
            throw ConversationRelayError.invalidBatch
        }
        let object = fields.sorted { $0.key < $1.key }
            .map { "\(jsonString($0.key)):\(jsonString($0.value))" }
            .joined(separator: ",")
        let json = "[\(jsonString("lifeos.approval.v1")),\(jsonString(actionClass)),{\(object)}]"
        guard let data = json.data(using: .ascii) else { throw ConversationRelayError.invalidBatch }
        return data
    }

    private static func pythonISO8601(_ date: Date) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.formatOptions = [
            .withInternetDateTime, .withDashSeparatorInDate,
            .withColonSeparatorInTime, .withColonSeparatorInTimeZone,
        ]
        return formatter.string(from: date).replacingOccurrences(of: "Z", with: "+00:00")
    }

    private static func jsonString(_ value: String) -> String {
        var encoded = "\""
        for scalar in value.unicodeScalars {
            switch scalar.value {
            case 0x22: encoded += "\\\""
            case 0x5C: encoded += "\\\\"
            case 0x08: encoded += "\\b"
            case 0x09: encoded += "\\t"
            case 0x0A: encoded += "\\n"
            case 0x0C: encoded += "\\f"
            case 0x0D: encoded += "\\r"
            case 0x00...0x1F:
                encoded += String(format: "\\u%04x", scalar.value)
            default: encoded.unicodeScalars.append(scalar)
            }
        }
        return encoded + "\""
    }
}

public enum WeekPlanExecutionState: String, Codable, Sendable {
    case applied
}

public struct WeekPlanExecutionResult: Codable, Sendable {
    public let proposalID: UUID
    public let state: WeekPlanExecutionState
    public let leaseID: UUID
    public let appliedOperations: Int
    public let message: String

    enum CodingKeys: String, CodingKey {
        case proposalID = "proposal_id"
        case state
        case leaseID = "lease_id"
        case appliedOperations = "applied_operations"
        case message
    }
}

struct WeekPlanApprovalRequest: Encodable {
    let approval: LifeOSApproval
}
