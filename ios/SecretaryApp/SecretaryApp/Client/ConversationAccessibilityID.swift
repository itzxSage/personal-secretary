public enum ConversationAccessibilityID: String, CaseIterable, Sendable {
    case shell = "conversation.shell"
    case transcript = "conversation.transcript"
    case textField = "conversation.text-field"
    case sendButton = "conversation.send-button"
    case pushToTalkButton = "conversation.push-to-talk"
    case status = "conversation.status"

    public static let requiredControls: [ConversationAccessibilityID] = [
        .shell,
        .transcript,
        .textField,
        .sendButton,
        .pushToTalkButton,
        .status,
    ]
}
