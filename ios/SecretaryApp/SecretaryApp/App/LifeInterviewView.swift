#if os(iOS)
import AVFoundation
import SecretaryClient
import SwiftUI

struct LifeInterviewView: View {
    let session: ConversationSession
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.dismiss) private var dismiss
    @State private var voice = NativeInterviewVoice()
    @State private var draft = ""
    @State private var showingProgress = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: LifeDesign.spacing) {
                    Text("A little understanding.\nA more personal life.")
                        .font(.largeTitle.weight(.semibold)).tracking(-0.8)
                    Text("I'll ask one question at a time. Speak naturally, type, or skip anything.")
                        .foregroundStyle(LifeDesign.secondary)
                    LifeCard {
                        VStack(alignment: .leading, spacing: 20) {
                            Label(voiceStatus, systemImage: "sparkles")
                                .font(.subheadline.weight(.medium))
                                .foregroundStyle(LifeDesign.navy)
                            if let question = session.interviewReply?.question {
                                Text(question.prompt).font(.title2).fixedSize(horizontal: false, vertical: true)
                            } else if session.interviewReply?.phase == "paused" {
                                Text("Let's pick up where you left off.").font(.title2)
                            } else if session.interviewReply?.phase == "continuous" {
                                Text("We have a good starting point. I'll check in when something useful needs clarification.").font(.title2)
                            } else {
                                Text("Getting your conversation ready…").font(.title2)
                            }
                            if voice.state == .listening && !voice.partial.isEmpty {
                                Text(voice.partial).foregroundStyle(LifeDesign.secondary)
                                    .accessibilityLabel("Your answer: \(voice.partial)")
                            }
                            voiceControls
                        }
                    }
                    if let error = session.interviewError ?? voice.errorMessage {
                        Text(error).font(.callout).foregroundStyle(.secondary)
                        Button("Reconnect") { Task { await session.loadInterview() } }
                    }
                    TextField("Or type your answer", text: $draft, axis: .vertical)
                        .lineLimit(2...8).padding(18)
                        .background(.white, in: RoundedRectangle(cornerRadius: 18))
                        .onTapGesture { voice.pause() }
                    HStack {
                        Button("Skip for now") {
                            voice.pause()
                            Task { _ = await session.advanceInterview(.skip) }
                        }
                        .disabled(session.interviewReply?.question == nil || session.isBusy)
                        Spacer()
                        Button("Send answer", action: sendDraft)
                            .buttonStyle(.borderedProminent).tint(LifeDesign.navy)
                            .disabled(draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                                      || session.isBusy || session.interviewReply?.question == nil)
                    }
                    Button("Your life domains") { showingProgress.toggle() }
                    if showingProgress, let reply = session.interviewReply {
                        ForEach(reply.progress, id: \.domain) { progress in
                            HStack {
                                Text(progress.domain.replacingOccurrences(of: "_", with: " ").capitalized)
                                Spacer()
                                Text(progress.skipped ? "Skipped" : "\(progress.known) of \(progress.total) explored")
                                    .foregroundStyle(LifeDesign.secondary)
                            }.font(.footnote)
                        }
                    }
                }.padding(LifeDesign.spacing)
            }
            .background(LifeDesign.surface)
            .foregroundStyle(LifeDesign.navy)
            .navigationTitle("Know Me")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { pauseAndClose() }
                }
            }
            .task {
                voice.onAnswer = { text in draft = text; sendDraft() }
                await session.loadInterview()
            }
            .onChange(of: session.interviewReply?.revision) { _, _ in
                if let question = session.interviewReply?.question, voice.conversationEnabled {
                    voice.speak(question.prompt)
                } else if session.interviewReply?.question == nil { voice.pause() }
            }
            .onChange(of: scenePhase) { _, phase in
                if phase != .active { voice.pause() }
            }
            .onReceive(NotificationCenter.default.publisher(for: AVAudioSession.interruptionNotification)) { _ in
                voice.pause()
            }
            .onDisappear { voice.pause() }
        }
    }

    private var voiceStatus: String {
        switch voice.state {
        case .speaking: "Speaking · tap to answer"
        case .listening: "Listening to you"
        case .processing: "Saving your answer"
        case .idle: "\(AppBrand.displayName) · Your life, at your pace"
        case .error: "Voice paused"
        }
    }

    private var voiceControls: some View {
        HStack(spacing: 16) {
            Button {
                if voice.state == .listening { voice.finishAnswer() }
                else if voice.state == .speaking { voice.interruptAndListen() }
                else {
                    Task {
                        if session.interviewReply?.phase == "paused" {
                            guard await session.advanceInterview(.resume) else { return }
                        }
                        if let question = session.interviewReply?.question { await voice.start(question: question.prompt) }
                    }
                }
            } label: {
                Label(voice.state == .listening ? "I'm finished" : "Talk with me",
                      systemImage: voice.state == .listening ? "checkmark" : "mic.fill")
                    .padding(.vertical, 6)
            }
            .buttonStyle(.borderedProminent).tint(LifeDesign.navy)
            .disabled(session.isBusy || session.interviewReply == nil)
            if voice.conversationEnabled {
                Button("Pause") {
                    voice.pause()
                    Task { _ = await session.advanceInterview(.pause) }
                }
            }
        }
    }

    private func sendDraft() {
        let text = draft
        Task {
            if await session.advanceInterview(.answer, text: text) { draft = "" }
            else { voice.pause() }
        }
    }

    private func pauseAndClose() {
        voice.pause()
        Task {
            _ = await session.advanceInterview(.pause)
            dismiss()
        }
    }
}
#endif
