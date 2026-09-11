import Foundation

public enum EnergyRating: Int, CaseIterable, Sendable {
  case one = 1
  case two = 2
  case three = 3
  case four = 4
  case five = 5
}

public enum EnergyNotificationAction: Equatable, Sendable {
  case response(EnergyRating)
  case snooze
}

public enum EnergyNotificationContract {
  public static let categoryIdentifier = "lifeos.energy.check_in"
  public static let checkInIDKey = "check_in_id"
  public static let responseIdentifier = "energy_response"
  public static let snoozeIdentifier = "energy_snooze"

  public static func action(
    for identifier: String,
    responseText: String? = nil
  ) -> EnergyNotificationAction? {
    if identifier == snoozeIdentifier {
      return .snooze
    }
    guard identifier == responseIdentifier,
      let responseText,
      let rawValue = Int(responseText.trimmingCharacters(in: .whitespacesAndNewlines)),
      let rating = EnergyRating(rawValue: rawValue)
    else {
      return nil
    }
    return .response(rating)
  }
}

public struct EnergyNotificationSubmission: Equatable, Sendable {
  public let checkInID: String
  public let action: EnergyNotificationAction

  public init?(checkInID: String, actionIdentifier: String, responseText: String? = nil) {
    guard !checkInID.isEmpty,
      let action = EnergyNotificationContract.action(
        for: actionIdentifier,
        responseText: responseText
      )
    else {
      return nil
    }
    self.checkInID = checkInID
    self.action = action
  }
}

extension Notification.Name {
  public static let lifeOSEnergyCheckInAction = Notification.Name("lifeos.energy.check_in.action")
}
