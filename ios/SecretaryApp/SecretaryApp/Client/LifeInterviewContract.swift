import Foundation

/// Life Engine owns interview state. Voice and keyboard share this response.
public struct LifeInterviewReply: Codable, Sendable {
    public let sessionID: UUID
    public let revision: Int
    public let phase: String
    public let question: LifeInterviewQuestion?
    public let progress: [LifeInterviewProgress]
    public let acknowledgment: String

    enum CodingKeys: String, CodingKey {
        case sessionID = "session_id"
        case revision, phase, question, progress, acknowledgment
    }
}

public struct LifeInterviewQuestion: Codable, Sendable {
    public let key: String
    public let domain: String
    public let prompt: String
    public let mode: String
}

public struct LifeInterviewProgress: Codable, Sendable {
    public let domain: String
    public let known: Int
    public let total: Int
    public let skipped: Bool
}

public enum LifeInterviewAction: String, Codable, Sendable {
    case answer, skip, pause, resume
}

struct LifeInterviewTurn: Encodable {
    let expected_revision: Int
    let action: LifeInterviewAction
    let text: String
    let question_key: String?
}
