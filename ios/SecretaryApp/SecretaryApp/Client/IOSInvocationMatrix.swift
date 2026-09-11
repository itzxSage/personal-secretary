import Foundation

/// Device model families that change which invocation surfaces exist.
public enum IOSDeviceModel: String, Sendable, Equatable {
    case iPhone12
    case iPhone13
    case iPhone15Pro
    case iPhone15ProMax
    case other

    /// Only iPhone 15 Pro and Pro Max carry the Action Button.
    public var hasActionButton: Bool {
        self == .iPhone15Pro || self == .iPhone15ProMax
    }
}

/// A snapshot of the device the app is running on, used to compute the
/// supported invocation surface matrix.
public struct iOSDeviceDescriptor: Sendable, Equatable {
    public let osMajor: Int
    public let osMinor: Int
    public let model: IOSDeviceModel
    public let lockScreenEligible: Bool

    public init(osMajor: Int, osMinor: Int, model: IOSDeviceModel, lockScreenEligible: Bool) {
        self.osMajor = osMajor
        self.osMinor = osMinor
        self.model = model
        self.lockScreenEligible = lockScreenEligible
    }
}

/// How much evidence a surface has been verified with.
public enum InvocationEvidenceState: String, Sendable, Equatable {
    case simulatorVerified
    case deviceRegistrationRequired
    case manualConfigurationRequired
    case deviceDeepLinkReceiptRequired
}

public enum InvocationBindingMechanism: String, Sendable, Equatable {
    case inAppControl
    case appIntent
    case shortcutConfiguration
    case controlWidget
    case widgetDeepLink
    case notificationDelegate
}

public enum SiriAutomationScope: String, Sendable, Equatable {
    case appIntentRegistrationOnly
}

public struct InvocationSurfaceBinding: Sendable, Equatable {
    public let surface: InvocationSource
    public let handlerSource: InvocationSource
    public let mechanism: InvocationBindingMechanism
    public let evidence: InvocationEvidenceState
}

/// The public invocation fallback matrix: which surfaces exist on which
/// iPhone/iOS combinations, and what evidence each surface has.
public enum IOSInvocationMatrix {
    public static let siriAutomationScope = SiriAutomationScope.appIntentRegistrationOnly

    public static func supportedSurfaces(for device: iOSDeviceDescriptor) -> [InvocationSource] {
        guard device.osMajor >= 17 else {
            return []
        }
        var surfaces: [InvocationSource] = []
        let isCapturedIOS17 = device.osMajor == 17 && (0...6).contains(device.osMinor)
        let isCapturedIOS18Pro = device.osMajor >= 18 && device.model.hasActionButton
        if device.model != .other && (isCapturedIOS17 || isCapturedIOS18Pro) {
            surfaces = [.pushToTalk, .appShortcut, .backTap]
            if device.model.hasActionButton {
                surfaces.append(.actionButton)
            }
            if device.osMajor >= 18 {
                surfaces.append(.controlCenter)
            }
        }
        if device.lockScreenEligible {
            surfaces.append(.lockScreen)
        }
        return surfaces
    }

    public static func evidenceState(for source: InvocationSource) -> InvocationEvidenceState {
        switch source {
        case .pushToTalk:
            return .simulatorVerified
        case .appShortcut, .controlCenter, .lockScreen:
            return .deviceRegistrationRequired
        case .backTap, .actionButton:
            return .manualConfigurationRequired
        case .notification:
            return .deviceDeepLinkReceiptRequired
        }
    }

    public static func binding(for source: InvocationSource) -> InvocationSurfaceBinding {
        let handlerSource: InvocationSource
        let mechanism: InvocationBindingMechanism
        switch source {
        case .backTap, .actionButton:
            handlerSource = .appShortcut
            mechanism = .shortcutConfiguration
        case .pushToTalk:
            handlerSource = source
            mechanism = .inAppControl
        case .appShortcut:
            handlerSource = source
            mechanism = .appIntent
        case .controlCenter:
            handlerSource = source
            mechanism = .controlWidget
        case .lockScreen:
            handlerSource = source
            mechanism = .widgetDeepLink
        case .notification:
            handlerSource = source
            mechanism = .notificationDelegate
        }
        return InvocationSurfaceBinding(
            surface: source,
            handlerSource: handlerSource,
            mechanism: mechanism,
            evidence: evidenceState(for: source)
        )
    }
}
