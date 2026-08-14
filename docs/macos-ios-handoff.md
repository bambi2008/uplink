# Uplink iOS handoff

Windows owns the shared web application, commercial backend, native bridge,
brand assets, and Android release flow. macOS is required only for the Apple
toolchain and store submission.

## Inputs required on the Mac

- The `codex/commercial-mobile` branch at the approved release commit.
- Xcode and an Apple Developer account with access to the seller's team.
- The real public Uplink HTTPS API origin.
- App Store Connect metadata, privacy policy URL, support URL, and subscription
  product identifiers when subscriptions are enabled.

Provider API keys are server-side secrets. Do not copy MiniMax, Xunfei, or Doubao
credentials to the Mac, Xcode project, app bundle, or App Store Connect.

## Prepare the native project

```bash
git checkout codex/commercial-mobile
npm ci
export UPLINK_API_ORIGIN='https://api.your-domain.com'
export UPLINK_RELEASE_BUILD=1
npm run mobile:sync
open ios/App/App.xcodeproj
```

Confirm in Xcode:

1. Bundle identifier is `com.bambi2008.uplink` and matches App Store Connect.
2. The correct Apple Developer team and automatic signing are selected.
3. The 1024 x 1024 AppIcon has no alpha channel.
4. `NSMicrophoneUsageDescription` is present and visible to users.
5. Secure Storage appears in Swift Package dependencies.
6. Release logging and WebView debugging remain disabled.

## Device acceptance

Test a release configuration on at least one current iPhone and one older
supported iPhone:

1. Fresh install, registration, login, logout, and relaunch persistence.
2. Microphone permission denied, granted, and later changed in Settings.
3. Chinese and English speech recognition.
4. Jake TTS playback, user interruption, and thinking pauses.
5. Network loss during ASR, chat, and TTS, followed by recovery.
6. Report creation and account isolation between two test users.
7. Confirm no provider credentials or long-lived session tokens appear in logs.

## Store archive

Use Product > Archive, run Xcode Validate App, then upload to App Store Connect.
Resolve every validation error before TestFlight. Keep provisioning profiles,
certificates, and exported archives outside Git. TestFlight approval is the final
gate before production submission.
