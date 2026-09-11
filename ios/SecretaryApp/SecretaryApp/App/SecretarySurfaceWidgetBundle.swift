#if os(iOS) && SECRETARY_WIDGET_EXTENSION
import SwiftUI
import WidgetKit

@main
struct SecretarySurfaceWidgetBundle: WidgetBundle {
    @WidgetBundleBuilder
    var body: some Widget {
        ConversationWidget()
        if #available(iOS 18.0, *) {
            ConversationControl()
        }
    }
}
#endif
