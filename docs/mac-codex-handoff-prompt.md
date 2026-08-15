# Mac Codex handoff prompt

Copy the following task into Codex on the Mac.

```text
Continue the macOS and iOS delivery work for Uplink.

Repository:
https://github.com/bambi2008/uplink

Working branch:
codex/commercial-mobile

Approved Windows delivery commit:
c6ba16a39d84115ded06235695d917ac70684d24

Before editing:
1. Clone or update the repository and check out codex/commercial-mobile.
2. Confirm that c6ba16a is in the branch history.
3. Read docs/macos-ios-handoff.md and this handoff prompt.
4. Inspect the current tree and tests before changing code.
5. Do not revert or broadly refactor the Windows, Android, backend, shared
   voice, turn-taking, bilingual conversation, or flight-review behavior that
   has already passed regression testing.

Complete the work that requires macOS:
1. Install dependencies with npm ci.
2. Set UPLINK_API_ORIGIN to the real public HTTPS backend origin, set
   UPLINK_RELEASE_BUILD=1, and run npm run mobile:sync.
3. Open ios/App/App.xcodeproj in Xcode.
4. Confirm the bundle identifier is com.bambi2008.uplink and matches App Store
   Connect.
5. Configure the correct Apple Developer team, signing certificate, and
   provisioning profile.
6. Verify the microphone permission description, AppIcon, launch screen, and
   Secure Storage dependency.
7. Install a Release build on real iPhones and test registration, login,
   logout, relaunch persistence, Chinese and English recognition, Jake TTS,
   interruption, thinking pauses, network-loss recovery, flight-review
   creation, and account isolation.
8. Confirm that no provider credentials or long-lived session tokens appear in
   source files, the app bundle, Xcode settings, device logs, or archives.
9. Run all existing automated tests and repeat the relevant iPhone acceptance
   tests after every fix.
10. Use Product > Archive, run Validate App, and prepare a TestFlight upload.
11. Commit only verified changes and push them back to
    codex/commercial-mobile. Report the commit hash, tests, device matrix, and
    any remaining App Store actions.

Security and product constraints:
- MiniMax, Xunfei, Doubao, and pronunciation-assessment credentials are
  server-side secrets. Never copy them into the Mac environment, repository,
  Xcode project, app bundle, logs, GitHub, or App Store Connect.
- Do not read, replace, clear, migrate, or rewrite the user's existing provider
  configuration.
- The mobile app must use only the production HTTPS backend. Do not make a
  release using localhost, a LAN address, a placeholder, or a test domain.
- Preserve Jake's existing bilingual conversation, fast response,
  interruption, thinking-pause, in-conversation correction, and flight-review
  behavior unless a reproducible iPhone defect requires a narrowly scoped fix.
- Do not declare the app release-ready until Xcode validation and real-device
  acceptance both pass.

If the production API origin, Apple Developer team, or App Store Connect access
is unavailable, finish every independent check first. Then report only the
specific missing inputs; do not insert placeholder production values.
```
