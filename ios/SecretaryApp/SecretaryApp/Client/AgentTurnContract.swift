import Foundation

/// This DTO deliberately cannot decode or dispatch arbitrary model capability payloads.
public struct AgentTurnReply: Decodable, Sendable {
    public let replyText: String
    public let proposedActions: [AgentAdvisoryAction]
    enum CodingKeys: String, CodingKey {
        case replyText = "reply_text"
        case proposedActions = "proposed_actions"
    }
}

public struct AgentAdvisoryAction: Decodable, Sendable {
    public enum Kind: String, Decodable, Sendable {
        case weekPlanPreview = "week_plan_preview"
        case approvalRequired = "approval_required"
    }
    public let actionClass: Kind
    enum CodingKeys: String, CodingKey { case actionClass = "action_class" }
}
