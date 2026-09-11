import Foundation
import SecretaryContract

/// Routes `lifeos://` deep links to the shared invocation adapter. Notification
/// taps and Lock Screen widget taps both land here; unknown links fail closed.
public struct DeepLinkRouter: Sendable {
    public let adapter: InvocationAdapter

    public init(adapter: InvocationAdapter) {
        self.adapter = adapter
    }

    public func handle(_ url: URL, values: InvocationValues) async -> InvocationOutcome {
        guard let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
            components.scheme == "lifeos",
            components.host == "conversation",
            components.path == "/start",
            components.user == nil,
            components.password == nil,
            components.port == nil,
            components.fragment == nil,
            let queryItems = components.queryItems,
            queryItems.count == 1,
            queryItems[0].name == "source",
            let rawSource = queryItems[0].value,
            let source = InvocationSource(rawValue: rawSource) else {
            return .invalidInvocation
        }
        return await adapter.invoke(source, values: values)
    }
}
