import SecretaryClient
import SwiftUI

struct PushToTalkButton: View {
    let session: ConversationSession

    var body: some View {
        Button {
            Task {
                if session.isSessionActive {
                    await session.endVoiceSession()
                } else {
                    await session.startVoiceSession(source: .pushToTalk)
                }
            }
        } label: {
            Label(
                session.isSessionActive ? "End Voice Session" : "Push to Talk",
                systemImage: session.isSessionActive ? "stop.fill" : "waveform"
            )
                .font(.title3.weight(.semibold))
                .frame(maxWidth: .infinity)
                .padding(.vertical, 14)
        }
        .buttonStyle(.borderedProminent)
        .tint(session.isSessionActive ? .red : .accentColor)
        .disabled(!session.isSessionActive && (session.isBusy || session.isAgentProcessing || session.hasDeliveryConflict))
        .padding()
        .accessibilityHint(
            session.isSessionActive
                ? "Ends the active voice conversation."
                : "Starts a voice conversation. Requires microphone permission."
        )
        .accessibilityIdentifier(ConversationAccessibilityID.pushToTalkButton.rawValue)
    }
}
