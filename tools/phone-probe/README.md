# Phone Probe 1a

Android WebView probe for `docs/integrations/breakout-phone-executor-DESIGN.md` § 7.2 / § 7.9.
**Read-only.** It places no order, taps nothing on the Breakout page, reads no input value and stores no credential.
Stock WebView user agent; no spoofing, no challenge handling, no reload loop.

## What it records (all in one JSON report)

| field | content |
|---|---|
| `device`, `webview`, `features` | phone model, Android version, WebView package and version, whether the WebView supports document-start scripts and web-message listeners |
| `probes` / `navs` | per page load: network (wifi / cellular / VPN), HTTP status, `cf-mitigated` header present, classified state, element counts |
| `captures` | redacted page shape of every frame (iframes included) when you tap **2 Capture** |
| `session` | when the terminal was first and last seen, when the login page came back, how many code / 2FA pages appeared |
| `fixtures` | results of the local typing / tap / iframe tests |
| `events` | start, pause, resume, load errors |

**Redaction.** A label is kept only if every word is in a fixed trading / login vocabulary (`probe.js`, `VOCAB`);
any other word becomes `*` and every digit run becomes `#`. Names, balances, emails and ids cannot appear. Copy and
Share refuse to export if any string still holds 6+ digits, an `@`, or a bearer token.
`FLAG_SECURE` blocks screenshots of the app.

## Checks

* `test/fixture_check.js` runs the page-side script in headless Chromium: the React-style typing quirk, iframe
  reach, redaction contract. It proves mechanics in Chromium, **not** behaviour in an Android WebView.
* The APK itself measures the WebView questions.

## Build

`.github/workflows/phone-probe-apk.yml` (push to `tools/phone-probe/**` or manual dispatch) uploads
`phone-probe-1a-debug-apk`. Debug-signed with the runner's throwaway key, so a newer build cannot be installed over an
older one without uninstalling (which clears the app's session).
