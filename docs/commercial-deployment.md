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
