# Uplink commercial runtime

The commercial runtime is separate from the desktop runtime. Customers open the
public HTTPS application, create an account, and use Uplink without a computer or
provider credentials.

## Runtime contract

- Set `UPLINK_COMMERCIAL_MODE=1`.
- Terminate TLS at the hosting load balancer and set `UPLINK_PUBLIC_ORIGIN` to the
  exact public HTTPS origin.
- Store every provider credential in the hosting platform's secret manager.
- Mount a persistent encrypted volume at `/data`.
- Run one application replica while the pilot uses SQLite. A PostgreSQL storage
  adapter is required before horizontal scaling.
- Back up `/data/uplink.sqlite3` daily and test restores before accepting payments.

## Local commercial smoke test

Use placeholder provider credentials only when testing account flows locally:

```powershell
$env:UPLINK_COMMERCIAL_MODE='1'
$env:UPLINK_INSECURE_COOKIE='1'
$env:UPLINK_DATA_DIR="$PWD/.commercial-test-data"
python server.py
```

Open `http://127.0.0.1:8800/`. The insecure-cookie switch must never be enabled on
the public deployment.

## Production gate

Before inviting paying customers:

1. Configure a real domain and HTTPS.
2. Move account storage from SQLite to managed PostgreSQL.
3. Connect App Store / Google Play subscriptions to server-side entitlements.
4. Add email verification, password reset, account deletion, privacy terms, and
   abuse controls.
5. Load test simultaneous ASR and TTS WebSocket sessions.

## Native iPhone and Android clients

The native projects package the Uplink interface locally. They do not use
Capacitor's production `server.url` option and do not contain provider secrets.
The app keeps its customer session in iOS Keychain / Android Keystore-backed
storage and exchanges it for a path-bound, one-time, 30-second WebSocket ticket.

Build the web payload for the real public HTTPS API before every native sync:

```powershell
$env:UPLINK_API_ORIGIN='https://app.example.com'
npm install
npm run mobile:sync
```

- Android: open `android/` in Android Studio, install the required SDK, then
  create a signed AAB for Google Play.
- iOS: open `ios/App/App.xcodeproj` on macOS with Xcode, select the Apple
  Developer team, verify the microphone privacy text, then archive for App Store
  Connect.
- The app id is currently `com.bambi2008.uplink`. Change it before the first
  store upload only if the seller's owned domain requires another reverse-domain
  identifier; changing it later creates a different app.
- Keep `UPLINK_NATIVE_ORIGINS` restricted to the shipped Capacitor origins.
- Replace SQLite with managed PostgreSQL before running more than one backend
  replica. In-memory WebSocket tickets require sticky single-replica operation
  until a shared Redis ticket store is added.

The checked-in native projects contain no signing certificates, provisioning
profiles, Android keystores, passwords, customer data, or provider credentials.
