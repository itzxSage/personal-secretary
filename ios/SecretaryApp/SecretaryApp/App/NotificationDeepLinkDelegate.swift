#if os(iOS)
  import SecretaryClient
  import UIKit
  import UserNotifications

  final class NotificationDeepLinkDelegate: NSObject, UIApplicationDelegate,
    @preconcurrency UNUserNotificationCenterDelegate
  {
    func application(
      _ application: UIApplication,
      didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
      let center = UNUserNotificationCenter.current()
      center.delegate = self
      registerEnergyCategory(with: center)
      return true
    }

    func userNotificationCenter(
      _ center: UNUserNotificationCenter,
      didReceive response: UNNotificationResponse,
      withCompletionHandler completionHandler: @escaping () -> Void
    ) {
      let userInfo = response.notification.request.content.userInfo
      let responseText = (response as? UNTextInputNotificationResponse)?.userText
      if let checkInID = userInfo[EnergyNotificationContract.checkInIDKey] as? String,
        let submission = EnergyNotificationSubmission(
          checkInID: checkInID,
          actionIdentifier: response.actionIdentifier,
          responseText: responseText
        )
      {
        NotificationCenter.default.post(
          name: .lifeOSEnergyCheckInAction,
          object: submission
        )
        completionHandler()
        return
      }
      let value = userInfo["deep_link"]
      guard let rawURL = value as? String, let url = URL(string: rawURL) else {
        completionHandler()
        return
      }
      Task { @MainActor in
        await ConversationSession.shared.handleDeepLink(url)
        completionHandler()
      }
    }

    private func registerEnergyCategory(with center: UNUserNotificationCenter) {
      let rating = UNTextInputNotificationAction(
        identifier: EnergyNotificationContract.responseIdentifier,
        title: "Rate 1-5",
        options: [.foreground],
        textInputButtonTitle: "Save",
        textInputPlaceholder: "1-5"
      )
      let snooze = UNNotificationAction(
        identifier: EnergyNotificationContract.snoozeIdentifier,
        title: "Snooze",
        options: [.foreground]
      )
      let category = UNNotificationCategory(
        identifier: EnergyNotificationContract.categoryIdentifier,
        actions: [rating, snooze],
        intentIdentifiers: [],
        options: []
      )
      center.setNotificationCategories([category])
    }
  }
#endif
