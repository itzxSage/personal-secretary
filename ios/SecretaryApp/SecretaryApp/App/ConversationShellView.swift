import SecretaryClient
import SecretaryContract
import SwiftUI

struct ConversationShellView: View {
    let session: ConversationSession
    @State private var draft = ""
    @State private var showingDelete = false
    @State private var showingInterview = false

    var body: some View {
        VStack(spacing: 0) {
            header
            transcriptList
            statusBar
            textComposer
            PushToTalkButton(session: session)
        }
        .task {
            await session.loadPendingEvents()
#if DEBUG && os(iOS)
            await session.runRelaySmokeTestIfRequested()
            await session.runRelaySmokeCleanupIfRequested()
#endif
        }
        .confirmationDialog(
            "Delete this conversation?",
            isPresented: $showingDelete,
            titleVisibility: .visible
        ) {
            Button("Delete from iPhone and Mac", role: .destructive) {
                Task { await session.deleteConversation() }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Message content is purged. Only minimized audit proof remains.")
        }
        .accessibilityIdentifier(ConversationAccessibilityID.shell.rawValue)
#if os(iOS)
        .sheet(isPresented: $showingInterview) { LifeInterviewView(session: session) }
#endif
    }

    private var header: some View {
        HStack {
            Text(AppBrand.displayName)
                .font(.headline)
            Spacer()
            if session.isRelayConfigured {
                Button("Sync") { Task { await session.synchronize() } }
                    .disabled(session.isBusy)
                Button("Delete", role: .destructive) { showingDelete = true }
                    .disabled(session.isBusy)
            }
#if os(iOS)
            Button("Know Me") { showingInterview = true }
#endif
        }
        .padding()
        .background(.bar)
    }

    private var transcriptList: some View {
        List(displayedTranscript, id: \.eventId) { event in
            TranscriptRow(event: event)
        }
        .listStyle(.plain)
        .accessibilityIdentifier(ConversationAccessibilityID.transcript.rawValue)
        .overlay {
            if session.transcript.isEmpty {
                ContentUnavailableView(
                    "No conversation yet",
                    systemImage: "waveform",
                    description: Text("Push to talk or use a system surface to start.")
                )
            }
        }
    }

    private var displayedTranscript: [ConversationEvent] {
        session.transcript.filter { event in
            switch event.payload {
            case .transcriptPartial(let payload):
                return payload.text == "voice_session_started"
            case .transcriptFinal, .attachment, .toolResult, .cancellation, .resume, .error:
                return true
            }
        }
    }

    private var statusBar: some View {
        Text(session.statusMessage ?? "Ready")
            .font(.footnote)
            .foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal)
            .padding(.vertical, 8)
            .background(.bar)
            .accessibilityIdentifier(ConversationAccessibilityID.status.rawValue)
            .accessibilityAddTraits(.updatesFrequently)
    }

    private var textComposer: some View {
        HStack(alignment: .bottom, spacing: 8) {
            TextField("Message \(AppBrand.displayName)", text: $draft, axis: .vertical)
                .lineLimit(1...4)
                .textFieldStyle(.roundedBorder)
                .submitLabel(.send)
                .onSubmit(sendDraft)
                .disabled(session.isSessionActive || session.isBusy || session.hasDeliveryConflict)
                .accessibilityLabel("Message")
                .accessibilityIdentifier(ConversationAccessibilityID.textField.rawValue)

            Button(action: sendDraft) {
                Image(systemName: "arrow.up")
                    .font(.body.weight(.semibold))
                    .frame(width: 44, height: 44)
            }
            .buttonStyle(.borderedProminent)
            .buttonBorderShape(.circle)
            .disabled(
                session.isSessionActive
                    || session.isBusy || session.hasDeliveryConflict
                    || draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            )
            .accessibilityLabel("Send message")
            .accessibilityIdentifier(ConversationAccessibilityID.sendButton.rawValue)
        }
        .padding(.horizontal)
        .padding(.top, 8)
        .background(.bar)
    }

    private func sendDraft() {
        let text = draft
        Task {
            if await session.sendText(text) {
                draft = ""
                await session.synchronize()
            }
        }
    }
}

private struct TranscriptRow: View {
    let event: ConversationEvent

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(event.kind.rawValue)
                .font(.caption)
                .foregroundStyle(.secondary)
            switch event.payload {
            case .transcriptPartial(let payload):
                Text(payload.text)
                    .font(.body)
            case .transcriptFinal(let payload):
                Text(payload.text)
                    .font(.body)
            case .attachment, .toolResult, .cancellation, .resume, .error:
                EmptyView()
            }
        }
        .padding(.vertical, 4)
    }
}
