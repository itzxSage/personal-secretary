import Foundation
import SecretaryClient
import Testing

@Test("energy notification exposes exactly five actionable ratings")
func energyNotificationRatingsAreBounded() throws {
  // Given: each value the notification's text-input action offers.
  let responses = EnergyRating.allCases.map { String($0.rawValue) }

  // When: every response crosses the typed notification boundary.
  let actions = responses.map {
    EnergyNotificationContract.action(
      for: EnergyNotificationContract.responseIdentifier,
      responseText: $0
    )
  }

  // Then: the values are exactly 1 through 5 with no untyped response.
  #expect(actions == EnergyRating.allCases.map { .response($0) })
}

@Test("energy notification snooze is typed and malformed actions fail closed")
func energyNotificationSnoozeAndUnknownActions() {
  // Given/When/Then: snooze is explicit and unknown or out-of-range actions are rejected.
  #expect(EnergyNotificationContract.action(for: "energy_snooze") == .snooze)
  #expect(
    EnergyNotificationContract.action(
      for: EnergyNotificationContract.responseIdentifier,
      responseText: "0"
    ) == nil
  )
  #expect(
    EnergyNotificationContract.action(
      for: EnergyNotificationContract.responseIdentifier,
      responseText: "6"
    ) == nil
  )
  #expect(EnergyNotificationContract.action(for: "unknown") == nil)
}

@Test("energy notification response binds the action to its check-in")
func energyNotificationSubmissionBindsCheckIn() throws {
  // Given: a notification request carrying only its opaque check-in identifier.
  let submission = try #require(
    EnergyNotificationSubmission(
      checkInID: "check-in-2026-09-05T14:00Z",
      actionIdentifier: EnergyNotificationContract.responseIdentifier,
      responseText: "4"
    )
  )

  // When/Then: the submission carries a typed action without health context or prose.
  #expect(submission.action == .response(.four))
  #expect(submission.checkInID == "check-in-2026-09-05T14:00Z")
  #expect(
    EnergyNotificationSubmission(
      checkInID: "",
      actionIdentifier: EnergyNotificationContract.responseIdentifier,
      responseText: "4"
    ) == nil
  )
}
