import SecretaryClient
import Testing

@Test("unsupported OS and model combinations make no public runtime claims")
func unsupportedMatrixCombinationsAreExcluded() {
    // Given: devices outside the initial captured matrix.
    let oldOS = iOSDeviceDescriptor(osMajor: 16, osMinor: 7, model: .iPhone13, lockScreenEligible: true)
    let unlistedModel = iOSDeviceDescriptor(osMajor: 17, osMinor: 6, model: .other, lockScreenEligible: false)

    // When/Then: neither device receives a claimed invocation surface.
    #expect(IOSInvocationMatrix.supportedSurfaces(for: oldOS).isEmpty)
    #expect(IOSInvocationMatrix.supportedSurfaces(for: unlistedModel).isEmpty)
}

@Test("settings gestures bind to the App Shortcut without changing conversation semantics")
func settingsGesturesShareShortcutHandler() {
    // Given: the public system-surface catalog.
    let backTap = IOSInvocationMatrix.binding(for: .backTap)
    let actionButton = IOSInvocationMatrix.binding(for: .actionButton)

    // When/Then: both configurable gestures use the same App Intent handler.
    #expect(backTap.handlerSource == .appShortcut)
    #expect(actionButton.handlerSource == .appShortcut)
    #expect(backTap.mechanism == .shortcutConfiguration)
    #expect(actionButton.mechanism == .shortcutConfiguration)
    #expect(backTap.evidence == .manualConfigurationRequired)
    #expect(actionButton.evidence == .manualConfigurationRequired)
}

@Test("direct surfaces bind to their matching shared handlers")
func directSurfacesUseMatchingHandlers() {
    // Given: surfaces with app-owned entry points.
    let pushToTalk = IOSInvocationMatrix.binding(for: .pushToTalk)
    let controlCenter = IOSInvocationMatrix.binding(for: .controlCenter)
    let lockScreen = IOSInvocationMatrix.binding(for: .lockScreen)

    // When/Then: each binding preserves its source before entering the adapter.
    #expect(pushToTalk.handlerSource == .pushToTalk)
    #expect(controlCenter.handlerSource == .controlCenter)
    #expect(lockScreen.handlerSource == .lockScreen)
    #expect(pushToTalk.mechanism == .inAppControl)
    #expect(controlCenter.mechanism == .controlWidget)
    #expect(lockScreen.mechanism == .widgetDeepLink)
    #expect(IOSInvocationMatrix.binding(for: .notification).mechanism == .notificationDelegate)
    #expect(IOSInvocationMatrix.siriAutomationScope == .appIntentRegistrationOnly)
}
