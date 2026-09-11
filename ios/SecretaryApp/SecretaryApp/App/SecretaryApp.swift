import SecretaryClient
import SwiftUI

@main
struct SecretaryApp: App {
    #if os(iOS)
    @UIApplicationDelegateAdaptor(NotificationDeepLinkDelegate.self) private var appDelegate
    #endif
    @State private var session = ConversationSession.shared

    var body: some Scene {
        WindowGroup {
            ConversationShellView(session: session)
                .onOpenURL { url in
                    Task { await session.handleDeepLink(url) }
                }
        }
    }
}
