# Physical-iPhone Acceptance Install Checklist (T14)

Staging acceptance build for the LifeOS continuation campaign. The signed
`.app` / `.ipa` below were produced by `xcodebuild` from current `main` code.
**Installation and tap-through on the physical iPhone are user-gated (F3).**
Nothing in this checklist claims PHYSICAL IPHONE TESTED.

## Artifacts (local-only, gitignored)

- Archive: `artifacts/verification/t14-acceptance-build/SecretaryApp.xcarchive`
  (contains `Products/Applications/SecretaryApp.app`, arm64, signed)
- IPA: `artifacts/verification/t14-acceptance-build/ipa/SecretaryApp.ipa`
- Build logs: `artifacts/verification/t14-acceptance-build/xcodebuild-archive.log`
  (52 s wall), `xcodebuild-export.log`, `Packaging.log`
- Signature proof: `codesign -dv` shows `Identifier=com.lifeos.SecretaryApp`,
  `TeamIdentifier=946R3QHA5P`; appex `com.lifeos.SecretaryApp.Surfaces` signed
  under the same team. Rebuilding reproduces these via the T14 receipt.

## Prerequisites

- [ ] Mac with Xcode 26.3 (build lane used Xcode 26.3 / Build 17C529)
- [ ] Physical iPhone (iPhone 15 Plus seen via `xcrun devicectl list devices`;
      state was `unavailable` at build time — cable/Wi-Fi + unlocked, see step 1)
- [ ] Apple ID with development signing for team `946R3QHA5P` on this Mac
      (build lane used `Apple Development: jrsgagne@gmail.com`)
- [ ] No entitlements were added for this campaign: no microphone/background-audio
      entitlement changes; `NSMicrophoneUsageDescription` /
      `NSSpeechRecognitionUsageDescription` Info.plist strings already present.

## Install steps (user-gated F3)

1. [ ] Connect the iPhone via cable, unlock it, tap **Trust This Computer**.
   Verify visibility (read-only probe, run by agent at build time):
   `xcrun devicectl list devices` must list the phone.
2. [ ] Install via Xcode: open `ios/SecretaryApp/SecretaryApp.xcodeproj`,
   select the physical iPhone as the run destination, **Build & Run**
   (or `Window > Devices and Simulators > + Install` with the `.ipa`).
   Alternative CLI (user runs): `xcrun devicectl device install app --device
   <identifier> artifacts/verification/t14-acceptance-build/ipa/SecretaryApp.ipa`
3. [ ] On first launch, iOS prompts for **microphone** and **speech
   recognition** permission — tap **Allow** for both. Voice sessions do not
   start listening until you explicitly start one.
4. [ ] If iOS blocks first launch ("Untrusted Developer"): iPhone
   **Settings > General > VPN & Device Management**, trust the developer
   certificate, then relaunch.
5. [ ] Confirm the app reaches the paired Mac relay (staging relay was live on
   `192.168.12.133:8443` at T13; re-verify before tap-through).

## Voice notes (tonight's path)

- **No remote TTS is used.** Speech is native on-device (`NativeSpeechProvider`
  via AVSpeechSynthesizer / Speech framework; T8 provider selection committed
  in `061dd18`). `VoiceStudioProvider` exists but native is the default and the
  acceptance path — no network audio calls, no TTS credentials needed.
- Microphone grant (step 3) is the only permission gate for the interview turn.

## Guardrails

- Do NOT add microphone/background-audio entitlements to "make it work".
- Do NOT modify provisioning profiles or Xcode signing settings — if signing
  fails, stop and record the verbatim error as the blocker (T14 failure path).
- Report tap-through outcome honestly into F3; this checklist alone is
  build/install evidence, not a test pass.
