#if os(iOS)
import SwiftUI
import WidgetKit

@available(iOS 18.0, *)
struct ConversationControl: ControlWidget {
    var body: some ControlWidgetConfiguration {
        StaticControlConfiguration(kind: "ConversationControl") {
            ControlWidgetButton(action: StartControlCenterVoiceSessionIntent()) {
                Label("Start Voice Session", systemImage: "waveform")
            }
        }
        .displayName("Voice Session")
        .description("Start a voice conversation with Secretary")
    }
}
#endif
