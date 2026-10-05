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

## 1a.3: new host, saved login, auto-login test

* **"1 Load site"** opens `https://trade.breakoutprop.com/`; **"Load app."** opens `https://app.breakoutprop.com/`.
* **"Set login"** takes the operator's login in a NATIVE dialog and stores it only in Android-Keystore-backed
  `EncryptedSharedPreferences` (`Creds.kt`). It is never logged, never in the report (Copy / Share refuse if the report
  contains it), and `FLAG_SECURE` covers the dialog. "Delete saved" removes it.
* **"4 Auto-login"** clears the web session, loads the site, finds the login form, fills it (native value setter first,
  real key events as fallback), submits, and records into `autologin[]`: form found / filled / route / submitted / outcome /
  time to logged-in. The helpers in `probe.js` (`__probeLogin`) act **only on a `breakoutprop.com` page** and refuse any
  other host. A Cloudflare challenge, interactive CAPTCHA widget, emailed code, 2FA or number-match is **recorded and the
  test stops**: nothing is solved or bypassed. If the dashboard shows an "Open Terminal" control it is pressed (navigation
  only); no trading control is ever touched.
* **Capture** now takes over from a background capture instead of being dropped, reads the page directly (no dependence on
  the message listener), and reports same-origin iframes and cross-origin iframe hosts. **Copy / Share** refuse while
  Fixtures or Auto-login is still running.

## 1a.4: email number-match login

The new terminal logs in by EMAIL: you enter your email, Breakout emails numbers, and you tap the number the page shows.
Tapping in the email opens Chrome, so the WebView page never completes. Two ways to bring the sign-in back into this app:

* **"Paste link"** loads the URL in your clipboard into the WebView, but ONLY if it is `https` on `breakoutprop.com` or a
  subdomain; anything else is refused. The link is a login token: it is loaded, then the clipboard is cleared, and only the
  host and a path SHAPE (words kept, ids and tokens as `*`) are recorded. It is never echoed or stored.
* **Route email taps into the app (3 phone steps, Android 12+):** (1) Settings > Apps > **Phone Probe 1a** > **Open by default**.
  (2) Tap **Add link** and tick `trade.breakoutprop.com` (and `app.breakoutprop.com` if listed). (3) Tap the email's number
  button again; it should now open in this app. The app is not auto-verified for these hosts, so Android may not offer the
  option on every phone: if it does not, use "Paste link" (long-press the button in the email, copy the link address).

What the report records: `session.login_methods_seen` (`password_form`, `email_first`, `email_number_match`,
`code_or_2fa`), `session.completed_after_number_match` (did the waiting page finish by itself after the number was tapped in
ANOTHER browser, and `without_app_reload` says whether this app reloaded it), `session.first_logged_in_at`, and
`session.reauth_needed_after_min` (minutes until a sign-in step came back). The 1 h / 6 h / next-morning "3 Recheck" taps
measure whether you are still logged in WITHOUT re-authenticating, which is what decides autonomy. Auto-login on this
login stops with `email_login_human_step_required` / `number_match_human_step_required`: the human step is never automated.
