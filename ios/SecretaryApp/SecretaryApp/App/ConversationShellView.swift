import SecretaryClient
import SecretaryContract
import SwiftUI

struct ConversationShellView: View {
    let session: ConversationSession
    @State private var draft = ""
    @State private var showingDelete = false
    @State private var showingInterview = false
    @State private var showingWeekPlan = false

    var body: some View {
        VStack(spacing: 0) {
            header
            Button {
                showingWeekPlan = true
                if session.weekPlanState == .idle {
                    Task { await session.previewWeekPlan() }
                }
            } label: {
                HStack(spacing: 16) {
                    Image(systemName: "calendar.badge.clock").font(.title)
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Plan My Week").font(.headline)
                        Text("Make room for what matters").font(.subheadline)
                    }
                    Spacer()
                    Image(systemName: "chevron.right")
                }
                .padding(LifeDesign.spacing)
                .foregroundStyle(LifeDesign.navy)
                .background(LifeDesign.paleBlue, in: RoundedRectangle(cornerRadius: LifeDesign.radius))
            }
            .buttonStyle(.plain)
            .padding(.horizontal)
            .padding(.vertical, 12)
            .accessibilityIdentifier("week-plan.open")
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
        .sheet(isPresented: $showingWeekPlan) { WeekPlanView(session: session) }
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

private struct WeekPlanView: View {
    let session: ConversationSession
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: LifeDesign.spacing) {
                    stateCard
                    if let proposal = session.weekPlanProposal {
                        proposalDetails(proposal)
                    }
                }
                .padding(LifeDesign.spacing)
            }
            .background(LifeDesign.surface)
            .foregroundStyle(LifeDesign.navy)
            .navigationTitle("Your week")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
            .safeAreaInset(edge: .bottom) { approvalBar }
        }
        .interactiveDismissDisabled(session.isWeekPlanBusy)
    }

    private var stateCard: some View {
        LifeCard {
            VStack(alignment: .leading, spacing: 16) {
                switch session.weekPlanState {
                case .idle:
                    Text("A week with room for you.").font(.title2.bold())
                    Text("Build a plan around what LifeOS knows about your life. You'll review it before anything changes.")
                    Button("Create my plan") { Task { await session.previewWeekPlan() } }
                case .planning:
                    ProgressView("Planning your week…")
                    Text("Finding time for your priorities and protecting your commitments.")
                case .proposed:
                    Label("Your week, ready to review", systemImage: "calendar").font(.title2.bold())
                    if session.weekPlanProposal?.calendarDryRun.operations.isEmpty == true {
                        Text("There are no calendar changes to make yet. LifeOS needs confirmed routines with days, times, and durations to build your week.")
                    } else {
                        Text("Explore your schedule below. Nothing changes until you approve.")
                    }
                case .applying:
                    ProgressView("Updating your LifeOS calendar…")
                    Text("Applying the changes you approved. This may take a moment.")
                case .applied:
                    Label("Your week is on your calendar", systemImage: "checkmark.circle.fill")
                        .font(.title2.bold())
                    if let result = session.weekPlanResult {
                        Text(result.appliedOperations == 0
                             ? "Your LifeOS calendar is already up to date."
                             : "Completed \(result.appliedOperations) calendar changes.")
                    }
                    Link("Open Google Calendar", destination: URL(string: "https://calendar.google.com/calendar/u/0/r/week")!)
                case .recoverableError(let message):
                    Label("Let's get you back on track", systemImage: "exclamationmark.circle")
                        .font(.title2.bold())
                    Text(message)
                    Button(session.weekPlanProposal == nil ? "Try again" : "Create a fresh preview") {
                        Task { await session.previewWeekPlan() }
                    }
                    .buttonStyle(.borderedProminent).tint(LifeDesign.navy)
                    if session.weekPlanProposal != nil {
                        Link("Check Google Calendar", destination: URL(string: "https://calendar.google.com/calendar/u/0/r/week")!)
                    }
                }
            }
            .accessibilityIdentifier("week-plan.status")
        }
    }

    @ViewBuilder private var approvalBar: some View {
        if session.weekPlanState == .proposed {
            VStack(spacing: 8) {
                Text("Only the calendar changes shown above will be approved.")
                    .font(.footnote).foregroundStyle(LifeDesign.secondary)
                Button("Approve calendar changes") { Task { await session.approveWeekPlan() } }
                    .font(.headline)
                    .buttonStyle(.borderedProminent).tint(LifeDesign.navy)
                    .controlSize(.large)
                    .disabled(!session.canApproveWeekPlan)
                    .accessibilityIdentifier("week-plan.approve")
                if !session.canApproveWeekPlan {
                    Text(session.weekPlanProposal?.calendarDryRun.operations.isEmpty == true
                         ? "Add your real routines before creating another preview."
                         : "Some calendar changes need review. Resolve the conflicts in Calendar, then create a fresh preview.")
                        .font(.footnote)
                    Button("Create a fresh preview") { Task { await session.previewWeekPlan() } }
                }
            }
            .frame(maxWidth: .infinity)
            .padding()
            .background(LifeDesign.surface)
        }
    }

    @ViewBuilder private func proposalDetails(_ proposal: WeekPlanProposal) -> some View {
        Text("\(date(proposal.windowStart, proposal: proposal)) – \(date(proposal.windowEnd, proposal: proposal))")
            .font(.title2.bold())
        Text("Times in \(proposal.timezone.replacingOccurrences(of: "_", with: " "))")
            .font(.footnote).foregroundStyle(LifeDesign.secondary)
        if proposal.status != .feasible {
            Label("Some parts of your week still need attention", systemImage: "info.circle")
        }
        LifeCard {
            VStack(alignment: .leading, spacing: 12) {
                Text("What will change in Calendar").font(.headline)
                Text(proposal.calendarDryRun.calendarSummary)
                ForEach(Array(CalendarDryRunOperationKind.allDisplayKinds), id: \.rawValue) { kind in
                    let count = proposal.calendarDryRun.operations.filter { $0.operation == kind }.count
                    if count > 0 { Text(consequence(kind, count: count)) }
                }
                Text("Changes are limited to your LifeOS calendar. Events on your other calendars stay unchanged.")
                    .font(.subheadline).foregroundStyle(LifeDesign.secondary)
                DisclosureGroup("Inspect calendar changes") {
                    ForEach(Array(proposal.calendarDryRun.operations.enumerated()), id: \.offset) { _, operation in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(operationLabel(operation.operation)).font(.caption.bold())
                            Text(operation.afterSummary ?? operation.beforeSummary ?? "Calendar block")
                            if let before = operation.beforeSummary, operation.operation == .update {
                                Text("Previously: \(before)").font(.caption).foregroundStyle(LifeDesign.secondary)
                            }
                        }.padding(.vertical, 6)
                    }
                }
            }
        }
        if !proposal.gaps.isEmpty || !proposal.unscheduled.isEmpty {
            LifeCard {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Needs a little more attention").font(.headline)
                    ForEach(Array(proposal.gaps.enumerated()), id: \.offset) { _, gap in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(gap.title).bold()
                            Text(gap.explanation)
                        }
                    }
                    ForEach(Array(proposal.unscheduled.enumerated()), id: \.offset) { _, item in
                        Label(item.detail, systemImage: "clock.badge.exclamationmark")
                    }
                    Text("These items aren't fully scheduled. You can fill in missing details in Know Me.")
                        .font(.footnote).foregroundStyle(LifeDesign.secondary)
                }
            }
        }
        Text("Your proposed schedule").font(.title2.bold())
        ForEach(days(proposal), id: \.self) { day in
            LifeCard {
                VStack(alignment: .leading, spacing: 16) {
                    Text(date(day, proposal: proposal))
                        .font(.headline)
                    ForEach(Array(proposal.blocks.filter {
                        calendar(proposal).isDate($0.startsAt, inSameDayAs: day)
                    }.sorted { $0.startsAt < $1.startsAt }.enumerated()), id: \.offset) { _, block in
                        VStack(alignment: .leading, spacing: 6) {
                            Text("\(time(block.startsAt, proposal: proposal)) – \(time(block.endsAt, proposal: proposal))")
                                .font(.caption).foregroundStyle(LifeDesign.secondary)
                            Text(block.title).font(.body.weight(.medium))
                            if block.kind == .fixed || block.kind == .protected {
                                Label("Protected time", systemImage: "lock").font(.caption)
                            }
                            let explanations = proposal.explanations.filter {
                                $0.scheduledBlockIDs.contains(block.blockID) || $0.activityID == block.activityID
                            }
                            if !explanations.isEmpty {
                                DisclosureGroup("Why is this here?") {
                                    ForEach(Array(explanations.enumerated()), id: \.offset) { _, explanation in
                                        Text(explanation.summary).font(.subheadline)
                                    }
                                }.font(.caption)
                            }
                        }
                        Divider()
                    }
                }
            }
        }
        if !proposal.calendarBlocks.isEmpty {
            LifeCard {
                DisclosureGroup("How your time appears in Calendar") {
                    ForEach(Array(proposal.calendarBlocks.enumerated()), id: \.offset) { _, block in
                        VStack(alignment: .leading, spacing: 6) {
                            Text(block.title).bold()
                            Text("\(date(block.startsAt, proposal: proposal)), \(time(block.startsAt, proposal: proposal)) – \(time(block.endsAt, proposal: proposal))")
                                .font(.caption)
                            ForEach(Array(block.segmentTitles.enumerated()), id: \.offset) { _, title in Text(title) }
                            ForEach(Array(block.transitions.enumerated()), id: \.offset) { _, transition in Text(transition).font(.caption) }
                        }.padding(.vertical, 8)
                    }
                }
            }
        }
    }

    private func zone(_ proposal: WeekPlanProposal) -> TimeZone {
        TimeZone(identifier: proposal.timezone) ?? .current
    }
    private func calendar(_ proposal: WeekPlanProposal) -> Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = zone(proposal)
        return calendar
    }
    private func days(_ proposal: WeekPlanProposal) -> [Date] {
        Array(Set(proposal.blocks.map { calendar(proposal).startOfDay(for: $0.startsAt) })).sorted()
    }
    private func date(_ value: Date, proposal: WeekPlanProposal) -> String {
        value.formatted(Date.FormatStyle(date: .complete, time: .omitted, timeZone: zone(proposal)))
    }
    private func time(_ value: Date, proposal: WeekPlanProposal) -> String {
        value.formatted(Date.FormatStyle(date: .omitted, time: .shortened, timeZone: zone(proposal)))
    }
    private func operationLabel(_ kind: CalendarDryRunOperationKind) -> String {
        switch kind {
        case .insert: "Create"
        case .update: "Update LifeOS block"
        case .delete: "Remove LifeOS block"
        case .noop: "Already up to date"
        case .externalEdit: "Your edit — needs review"
        case .conflict: "Conflict — needs review"
        case .missing: "Missing — needs review"
        }
    }
    private func consequence(_ kind: CalendarDryRunOperationKind, count: Int) -> String {
        "\(operationLabel(kind)): \(count) \(count == 1 ? "block" : "blocks")"
    }
}

private extension CalendarDryRunOperationKind {
    static let allDisplayKinds: [Self] = [.insert, .update, .delete, .noop, .externalEdit, .conflict, .missing]
}
