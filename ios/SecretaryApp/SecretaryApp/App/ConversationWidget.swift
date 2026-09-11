#if os(iOS)
import SwiftUI
import WidgetKit

/// Lock Screen widget source for an extension target. Device evidence remains
/// unverified until an installed extension registration and deep-link receipt
/// are captured.
struct ConversationWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "ConversationWidget", provider: ConversationTimelineProvider()) { _ in
            ConversationWidgetView()
                .widgetURL(URL(string: "lifeos://conversation/start?source=lock_screen"))
        }
        .configurationDisplayName("Voice Session")
        .description("Start a voice conversation from the Lock Screen.")
        .supportedFamilies([.accessoryCircular, .accessoryRectangular])
    }
}

private struct ConversationTimelineProvider: TimelineProvider {
    func placeholder(in context: Context) -> ConversationTimelineEntry {
        ConversationTimelineEntry(date: Date())
    }

    func getSnapshot(in context: Context, completion: @escaping (ConversationTimelineEntry) -> Void) {
        completion(ConversationTimelineEntry(date: Date()))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<ConversationTimelineEntry>) -> Void) {
        completion(Timeline(entries: [ConversationTimelineEntry(date: Date())], policy: .never))
    }
}

private struct ConversationTimelineEntry: TimelineEntry {
    let date: Date
}

private struct ConversationWidgetView: View {
    var body: some View {
        Image(systemName: "waveform")
            .font(.title2)
    }
}
#endif
