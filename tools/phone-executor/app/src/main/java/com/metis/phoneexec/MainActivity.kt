package com.metis.phoneexec

import android.annotation.SuppressLint
import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.text.InputType
import android.view.KeyCharacterMap
import android.view.MotionEvent
import android.view.ViewGroup
import android.view.WindowManager
import android.webkit.CookieManager
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.HorizontalScrollView
import android.widget.LinearLayout
import android.widget.TextView
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
import java.util.Date
import kotlin.math.abs
import kotlin.math.floor
import kotlin.math.max
import kotlin.math.round

/**
 * Phase-1b executor (design docs/integrations/breakout-phone-executor-DESIGN.md § 3, § 7.9).
 *
 * A screen-on kiosk WebView on trade.breakoutprop.com, with the stock WebView identity (no UA change, no
 * stealth, no challenge solving: a challenge page is never interacted with; the app reloads the terminal every
 * 30 min to re-check it). Every 30 s it:
 *   - logged out  -> auto re-login (PHONE-AUTOLOGIN-2, relogin()): type the account email, POLL the waiting page for
 *                    its number(s) (up to 90 s), POLL the dedicated inbox (up to 5 min), then either tap the ONE
 *                    lineup choice the mail names, or open the ONE mail link whose text is the page's single number
 *                    (LoginMatch; anything else fails closed). NO LATCH (operator 2026-10-09 "There is no halting"; 2026-10-10 "the login cannot depend
 *                    on manual input from me"): a failure is retried after 2, 5, 10, 20, then every 30 min, forever;
 *                    the 3rd consecutive failure sends ONE login_failed red flag ("still retrying"), the recovery one
 *                    login_ok. A tap on the screen defers re-login by at most 60 s (RELOGIN_DEFER_MS), never longer.
 *   - logged in   -> claim the next ticket from the VM (atomic, one attempt per ticket), fill it, read EVERY field
 *                    back (the submit label must carry the side), then:
 *                      DRY  (any test ticket, or the server says dry): do NOT submit,
 *                           report the read-back and clear the quantity.
 *                      LIVE (the server says live): submit, read Open orders / Positions back,
 *                           report the placement to the VM ledger; an opposite-side position is flattened.
 * Any unread, ambiguous or mismatched value is a refusal (fail closed), reported with the ticket dump.
 *
 * LIVE vs DRY IS DECIDED ONLY BY THE SERVER (operator 2026-10-06, ARMED-GATE): the claim's `submit` field, which
 * src/prop/phone_executor.py::submit_mode derives from accounts.yaml `mode` and the PROP_PHONE_MODE_<ACCOUNT> kill
 * switch (shadow / dry legs never emit a ticket at all). There is no device-local "armed" switch: a second, hidden
 * gate on the phone made the system harder to manage, not safer. Test tickets stay always dry.
 *
 * BACKGROUND (PI-20261006-APBY4NTV-0006, OBSERVED 2026-10-06: heartbeat stopped 10:03Z while the app was backgrounded,
 * claims resumed only when the operator reopened it): a backgrounded WebView may never answer evaluateJavascript, and
 * an un-timed js() then wedged the loop (busy=true forever: no claim, no heartbeat). Now every js() call times out,
 * and while the Activity is NOT resumed the loop never touches the WebView: it peeks GET /phone/pending (read-only,
 * claims nothing), and when a ticket waits it brings this Activity to the front. The claim + fill + every gate run
 * only once resumed; afterwards the task is moved back so the phone returns to what the operator was doing.
 *
 * BACKGROUND LOGIN CHECK (PI-20261010-GKFNZSH4-0001, MEASURED 2026-10-10: breakout_2 posted no account_status after
 * 10-09 15:01Z, background all night, and a logout_seen at 08:19Z was followed by no login_started): a backgrounded
 * app could never see a logout. The background tick now also brings the Activity forward (same BG_WAKE mechanism)
 * every LOGIN_CHECK_MS, when a re-login retry is due, or when the VM says account_status is stale; the resumed tick
 * reads the page, re-logs in if needed, posts account_status, and the task goes back unless the operator touched it.
 *
 * BACKGROUND ENGINE (PHONE-AUTOLOGIN-2, 2026-10-10: the bring-to-front was refused by Android's background-activity-start
 * limits, "bring-to-front already requested" at ~12:33Z with a retry due): those same login checks, the re-login and the
 * account panel read now run in BgWeb, a second WebView sharing this one's session, attached to a transparent
 * non-touchable overlay only while a job runs. Bring-to-front stays for login checks only after two engine jobs in a
 * row got no answer from the page. The heartbeat reports the engine (bg, bg_runs, bg_ok, bg_last).
 *
 * BACKGROUND TICKETS (PHONE-FGS-EXEC, operator 2026-10-15 "Phone fills in background"): a waiting ticket no longer
 * brings this Activity forward. The engine job claims it and runs the SAME execute()/executeAmend() on the engine's
 * page: every page call goes through [cur] (the kiosk page, or the engine's while a background ticket runs), so each
 * gate, read-back, server verify and go-token is unchanged. Bring-to-front is the fallback (BgTicket): no engine, no
 * claim reached, or a bad background result (6 h). The heartbeat reports bg_tickets / bg_ticket_last / bg_ticket_off_min.
 */
class MainActivity : Activity() {
    private lateinit var web: WebView
    private lateinit var status: TextView
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private lateinit var api: Api
    private lateinit var ledger: Ledger
    private var busy = false
    private var lastHbMs = 0L
    private var lastAcctMs = 0L
    private var lastSheetMissMs = 0L
    private var sheetOkPosted = false   // the first clean sheet read posts its shape once (opened_by, uPnL sign)
    private var lastPosMissMs = 0L
    private var lastStatus = ""
    // HOTFIX 2026-10-05 (manual login was being reset by the loop): any touch/key on the screen holds every
    // automatic navigation (reload, re-login) for USER_HOLD_MS; "Pause" holds everything until the page reads
    // logged in, or PAUSE_MAX_MS passes.
    private var lastUserInputMs = 0L
    private var pausedUntilMs = 0L
    private lateinit var pauseBtn: Button
    private var lastState = ""
    private var reloginFailures = 0     // CONSECUTIVE failed auto re-logins; reset by any logged-in read
    private var reloginNextMs = 0L      // backoff: no new attempt before this
    private var reloginFlagged = false  // the ONE red flag for this failure streak was sent
    private var lastPageReadMs = 0L     // last time a foreground tick read the page state
    private var statusStaleH = -1.0     // the VM's account_status age (h) from /phone/pending; < 0 = not told
    private var lastStaleWakeMs = 0L
    private var challengeRecheckMs = 0L
    private var execSrc = ""
    private var resumed = false
    private lateinit var fg: Page       // the kiosk WebView as a Page (bounded js calls)
    private var loginAttempted = false  // this tick started a re-login attempt (the wake window is handed back after it)
    // BACKGROUND ENGINE (PHONE-AUTOLOGIN-2): login checks, re-login and the account read run in BgWeb while backgrounded
    private var bgRuns = 0; private var bgOk = 0; private var bgLast = ""; private var bgLastMs = 0L
    private var bgNoAnswer = 0          // consecutive bg jobs whose page did not answer: >= 2 -> bring-to-front fallback
    private var bgLoginAtMs = 0L        // the engine logged in at this time; the kiosk reloads once to pick it up
    private var fgReloadMs = 0L
    private var lastPending = -1
    private var claimedThisTick = false
    private var claim5xx = 0
    private var claimBackoffUntilMs = 0L
    private var bgWakeUntilMs = 0L      // a bring-to-front was requested; retried only after this passes
    private var wokeAtMs = 0L           // > 0 while WE brought the Activity forward (it goes back afterwards)
    // BACKGROUND TICKETS (PHONE-FGS-EXEC): the page every ticket call drives; fg except while an engine ticket runs
    private lateinit var cur: Page
    private var lastClaimId = ""; private var lastClaimKind = ""   // what the last claimAndRun() claimed ("" = nothing)
    private var bgTickets = 0; private var bgTicketLast = ""
    private var bgTicketOffUntilMs = 0L // tickets go to the front until this (BgTicket fallback)
    private var bgTicketNextMs = 0L     // an engine ticket job that reached no claim is not repeated before this

    companion object {
        /** The live Activity in this process (null once destroyed): the watchdog restarts its loop or relaunches it. */
        @Volatile var instance: MainActivity? = null
        const val TRADE_URL = "https://trade.breakoutprop.com/"
        // The login entry that WORKED in the 1a probe (11:44-11:47Z): portal code step -> app.breakoutprop.com
        // (Cloudflare check, then served) -> trade host SSO -> logged-in landing. The trade host's own password
        // form is not the way in.
        const val APP_URL = "https://app.breakoutprop.com/"
        const val TICK_MS = 30_000L
        const val HEARTBEAT_MS = 120_000L
        const val ACCT_MS = 300_000L
        const val CHIPS_WAIT_MS = 12_000L               // Positions/Closed read: wait this long for the tab chips to render
        const val POS_MISS_MS = 1_800_000L   // a Positions read that failed is reported (masked dump) at most every 30 min
        // BREAKOUT-SL-AMEND-CLICK: the sheet's pencil click (and Confirm) stays OFF until a live amend_sheet_unmeasured
        // dump has shown the Stop row's icons (the ⓧ next to the pencil CANCELS the stop); the read path runs live
        const val SHEET_EDIT_MEASURED = false
        const val SHEET_MISS_MS = 600_000L              // a sheet read that failed is dumped at most every 10 min
        const val QTY_TOL_FRAC = 0.02                   // sheet "Total open volume" vs the ticket qty (venue rounding)
        const val PRICE_BUF_FRAC = 0.001                // the new stop clears the venue's current price by max(2 steps, 0.1%)
        const val USER_HOLD_MS = 5 * 60_000L
        const val PAUSE_MAX_MS = 10 * 60_000L
        const val JS_TIMEOUT_MS = Page.JS_TIMEOUT_MS
        // LINEUP LOGIN (PHONE-AUTOLOGIN-2, operator 2026-10-10: "it's way not waiting long enough to see the email. That
        // can take at least up to a minute"): the waiting page is polled for its number(s), the inbox for the mail
        const val PAGE_WAIT_MS = 90_000L
        const val DIAG_PARTS = 7                        // login_diag parts per attempt (phone_events keeps 40)
        const val DIAG_PART_CHARS = 148                 // + "dI/N " stays inside the server's 160
        const val PAGE_WAIT_MAX_MS = 150_000L           // ...extended while no mail has arrived yet (PHONE-AUTOLOGIN-3)
        const val PAGE_POLL_MS = 3_000L
        const val MAIL_WAIT_MS = 5 * 60_000L
        const val MAIL_POLL_MS = 10_000L
        const val BG_CHECK_MS = 15 * 60_000L            // backgrounded + logged in: engine login check + account read
        const val BG_WAKE_MAX_MS = 3 * 60_000L
        // a tap defers auto re-login by at most this; the old 5-min USER_HOLD_MS let an operator merely OPENING the
        // app (one tap, then background) block the login indefinitely (MEASURED 2026-10-10 08:19Z)
        const val RELOGIN_DEFER_MS = 60_000L
        val RELOGIN_BACKOFF_MIN = listOf(2L, 5L, 10L, 20L, 30L)   // then 30 min forever
        const val RELOGIN_FLAG_AFTER = 3
        const val LOGIN_CHECK_MS = 30 * 60_000L         // backgrounded: bring forward to read the page at least this often
        const val STALE_WAKE_MIN_MS = 10 * 60_000L      // ...and on a stale-account_status hint at most this often
        const val STATUS_STALE_H = 1.0
        const val CHALLENGE_RECHECK_MS = 30 * 60_000L
        // Server read-back (design 3.4): "key=value" lines in THIS order, hashed with SHA-256. Must equal
        // src/prop/phone_executor.py READBACK_FIELDS (tests/test_phone_executor.py compares the two lists).
        val READBACK_FIELDS = listOf("ticket_id", "symbol", "side", "order_type", "price", "qty", "qty_unit", "tp", "sl", "submit_label", "submit_disabled", "tpsl")
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(b: Bundle?) {
        super.onCreate(b)
        instance = this
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        setSecure(true)   // fail closed: secure until the state machine has SEEN the logged-in terminal
        api = Api(this); ledger = Ledger(this)
        Store.setFlag(this, Store.RELOGIN_LATCHED, false)   // an older build's latch must not survive the update
        execSrc = assets.open("exec.js").bufferedReader().readText()

        web = WebView(this)
        fg = Page(web, execSrc)
        cur = fg
        // TOUCH-HOLD counts only a real finger-down on the page (fix 2026-10-06 09:06Z: the operator saw "paused because
        // you are using it" with the app open and untouched; Activity.onUserInteraction also fires for keys, resumes and
        // system-dispatched events). Our own dispatchKeyEvent typing never reaches a touch listener.
        web.setOnTouchListener { _, ev -> if (ev.actionMasked == MotionEvent.ACTION_DOWN) lastUserInputMs = System.currentTimeMillis(); false }
        web.settings.javaScriptEnabled = true
        web.settings.domStorageEnabled = true
        CookieManager.getInstance().setAcceptCookie(true)
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, true)
        web.settings.allowFileAccess = false; web.settings.allowContentAccess = false
        web.settings.mixedContentMode = android.webkit.WebSettings.MIXED_CONTENT_NEVER_ALLOW
        // Same WebView setup as the 1a probe, whose login worked (stock UA, default navigation handling):
        // no shouldOverrideUrlLoading filter, every navigation stays in THIS WebView and cookie store.
        // Fail closed on navigation: any page off the trade host (the portal / login / code step) is secure at once;
        // the terminal itself is cleared only by tick() after it read state == logged_in.
        web.webViewClient = object : WebViewClient() {
            override fun onPageStarted(v: WebView?, url: String?, f: android.graphics.Bitmap?) {
                if (url == null || !url.startsWith("https://trade.breakoutprop.com/")) setSecure(true)
            }
        }
        status = TextView(this).apply { setPadding(16, 8, 16, 8); setBackgroundColor(Color.parseColor("#202833")); setTextColor(Color.WHITE); textSize = 12f }
        val bar = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            fun btn(label: String, f: () -> Unit) = addView(Button(this@MainActivity).apply { text = label; isAllCaps = false; setOnClickListener { f() } })
            btn("Setup") { setupDialog() }
            btn("Dry test") { dryTest() }
            btn("Share ID") { shareFingerprint() }
            addView(Button(this@MainActivity).apply { pauseBtn = this; isAllCaps = false; text = "Pause"; setOnClickListener { togglePause() } })
            btn("Login") { lastUserInputMs = System.currentTimeMillis(); setStatus("login: opening app.breakoutprop.com (log in there, then tap Reload)"); web.loadUrl(APP_URL) }
            btn("Reload") { web.loadUrl(home()) }
            // harmless: there is no latch any more; this only skips the current backoff wait and the touch defer
            btn("Retry login") { reloginNextMs = 0L; lastUserInputMs = 0L; setStatus("auto re-login: retrying on the next tick") }
        }
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(HorizontalScrollView(this@MainActivity).apply { addView(bar) })
            addView(status)
            addView(web, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        }
        setContentView(root)
        KeepAliveService.start(this, launch = false)
        web.loadUrl(TRADE_URL)

        Liveness.markTick(this)
        scope.launch {
            val stuck = ledger.unresolved()
            if (stuck.isNotEmpty()) api.event("error", "restart found unresolved tickets; not retried; check the terminal", stuck.first())
            // the last watchdog/boot/sticky restart reason rides the event, so a restart is explained, not just seen
            api.event("app_started", Liveness.lastRestartReason(this@MainActivity))
        }
        startLoop()
        // relaunched by the watchdog (not by the operator): hand the screen back unless it was touched
        if (intent?.getBooleanExtra("watchdog", false) == true) scope.launch {
            delay(8_000L); if (lastUserInputMs == 0L && resumed) moveTaskToBack(true)
        }
    }

    /** The tick loop, as a restartable job (PHONE-KEEPALIVE): the watchdog can cancel a wedged one and start a fresh one. */
    private var loopJob: Job? = null
    private fun startLoop() {
        loopJob?.cancel()
        loopJob = scope.launch { while (true) { delay(TICK_MS); Liveness.markTick(this@MainActivity); tick() } }
    }

    /** Called by KeepAliveService when no tick or heartbeat has happened for Liveness.LOOP_STALE_MS. */
    fun restartLoop(why: String) {
        loopJob?.cancel(); busy = false
        Liveness.markTick(this)
        scope.launch { try { api.event("loop_restarted", why) } catch (e: Exception) { } }
        startLoop()
    }

    override fun onDestroy() { if (instance === this) instance = null; scope.cancel(); super.onDestroy() }

    override fun onResume() {
        super.onResume(); resumed = true
        // the background engine logged in while we were away: reload the kiosk once so it picks up the shared session
        if (bgLoginAtMs > fgReloadMs) { fgReloadMs = System.currentTimeMillis(); web.loadUrl(home()) }
        if (bgWakeUntilMs > System.currentTimeMillis()) { wokeAtMs = System.currentTimeMillis(); scope.launch { delay(3000); tick() } }
    }

    override fun onPause() { resumed = false; super.onPause() }

    /** FLAG_SECURE (blank screenshots / recents) is ON for every credential-bearing screen -- login, code step,
     *  challenge, unread state, the Setup dialog (which sets its own flag) -- and OFF only on the logged-in trading
     *  terminal, which shows balances and positions but never a credential (operator 2026-10-06: screenshots of the app). */
    private var secureOn = false
    private fun setSecure(on: Boolean) {
        if (on == secureOn) return
        secureOn = on
        if (on) window.addFlags(WindowManager.LayoutParams.FLAG_SECURE) else window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
    }

    private fun setStatus(s: String) { status.text = s; lastStatus = s }
    /** The touch-hold protects a HUMAN LOGIN only (operator 2026-10-06 09:06Z): once logged in it never blocks claim,
     *  navigation or execution. The Pause button is the only manual stop. */
    private fun userActive() = lastState != "logged_in" && System.currentTimeMillis() - lastUserInputMs < USER_HOLD_MS
    /** Auto re-login is deferred only while the operator is touching the screen RIGHT NOW (60 s), never longer. */
    private fun loginDeferred() = resumed && System.currentTimeMillis() - lastUserInputMs < RELOGIN_DEFER_MS
    private fun paused() = System.currentTimeMillis() < pausedUntilMs
    private fun togglePause() {
        pausedUntilMs = if (paused()) 0L else System.currentTimeMillis() + PAUSE_MAX_MS
        pauseBtn.text = if (paused()) "PAUSED (tap to resume)" else "Pause"
        setStatus(if (paused()) "paused: no reload, no re-login, no ticket until logged in or 10 min" else "resumed")
    }

    private fun shareFingerprint() {
        val fp = Store.fingerprint(this)
        startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).setType("text/plain")
            .putExtra(Intent.EXTRA_TEXT, "Metis executor device fingerprint (not a secret): $fp"), "Share device ID"))
    }

    private fun setupDialog() {
        fun field(hint: String, key: String, secret: Boolean = false) = EditText(this).apply {
            this.hint = hint
            inputType = if (secret) InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD else InputType.TYPE_CLASS_TEXT
            if (!secret) setText(Store.get(this@MainActivity, key) ?: if (key == Store.API) Store.DEFAULT_API else "")
        }
        val api = field("API base", Store.API)
        val email = field("Breakout login email", Store.LOGIN_EMAIL)
        val user = field("Dedicated inbox (Gmail address)", Store.INBOX_USER)
        val pass = field("Inbox app password (blank = keep)", Store.INBOX_PASS, secret = true)
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL; setPadding(32, 16, 32, 0)
            listOf(api, email, user, pass).forEach { addView(it) }
            // § 7.19: one IMAP connect NOW with what is typed (blank password = the stored one); the answer in seconds
            addView(Button(this@MainActivity).apply { text = "Test inbox"; isAllCaps = false
                setOnClickListener { testInbox(user.text.toString(), pass.text.toString()) } })
            addView(Button(this@MainActivity).apply { text = "Allow restart after reboot (display over apps)"; isAllCaps = false
                setOnClickListener { startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName"))) } })
            addView(Button(this@MainActivity).apply { text = "Ignore battery optimisation"; isAllCaps = false
                setOnClickListener { startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName"))) } })
            addView(Button(this@MainActivity).apply { text = "Allow notifications"; isAllCaps = false
                setOnClickListener { if (android.os.Build.VERSION.SDK_INT >= 33) requestPermissions(arrayOf("android.permission.POST_NOTIFICATIONS"), 1) } })
        }
        val dlg = AlertDialog.Builder(this).setTitle("Setup").setView(box)
            .setPositiveButton("Save") { _, _ ->
                Store.put(this, Store.API, api.text.toString().trim())
                Store.put(this, Store.LOGIN_EMAIL, email.text.toString().trim())
                Store.put(this, Store.INBOX_USER, Creds.user(user.text.toString()))
                // § 7.19: EVERY whitespace / invisible char removed (was: only the ASCII space -- a pasted NBSP stayed in)
                if (Creds.pass(pass.text.toString()).isNotEmpty()) Store.put(this, Store.INBOX_PASS, Creds.pass(pass.text.toString()))
                // takes effect now: the running loop re-reads the store on every inbox poll; skip the backoff wait too
                reloginNextMs = 0L
                val pm = getSystemService(PowerManager::class.java)
                setStatus("saved. overlay=${Settings.canDrawOverlays(this)} battery_exempt=${pm.isIgnoringBatteryOptimizations(packageName)}")
            }.setNegativeButton("Cancel", null).create()
        dlg.window?.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        dlg.show()
    }

    /** § 7.19 "Test inbox": one immediate IMAP connect; the scrubbed result (Gmail's own words + credential SHAPES,
     *  never a value) is shown on screen and posted as quiet login_diag events ("inbox_test ..."). */
    private fun testInbox(typedUser: String, typedPass: String) {
        val u = Creds.user(typedUser).ifEmpty { Store.get(this, Store.INBOX_USER) ?: "" }
        val pw = if (Creds.pass(typedPass).isNotEmpty()) typedPass else Store.get(this, Store.INBOX_PASS) ?: ""
        val src = if (Creds.pass(typedPass).isNotEmpty()) "typed" else "stored"
        if (u.isEmpty() || pw.isEmpty()) { setStatus("inbox test: no inbox address or app password (type them, or Save first)"); return }
        setStatus("inbox test: connecting to the dedicated inbox...")
        scope.launch {
            val r = try { Mail.test(u, pw) } catch (e: Exception) { "FAIL ${e.javaClass.simpleName}" }
            setStatus("inbox test ($src): $r")
            AlertDialog.Builder(this@MainActivity).setTitle("Inbox test").setMessage("($src password)\n$r").setPositiveButton("OK", null).show()
            postChunks("inbox_test src=$src $r")
        }
    }

    /** A long scrubbed line as numbered quiet login_diag events inside the server's 160 chars. */
    private suspend fun postChunks(line: String) {
        val parts = line.chunked(DIAG_PART_CHARS - 6).take(3)
        parts.forEachIndexed { i, t -> api.event("login_diag", "i${i + 1}/${parts.size} $t") }
    }

    private fun dryTest() {
        scope.launch {
            val r = api.post("test-ticket", JSONObject().put("symbol", "ETHUSDT"))
            setStatus("test ticket: " + (r?.optString("ticket_id") ?: "VM unreachable") + (r?.optInt("http", 0)?.takeIf { it > 0 }?.let { " http $it" } ?: ""))
            tick()
        }
    }

    // ---------------- page bridge ----------------
    /** Every page call is bounded (Page): a frozen (backgrounded) WebView never answers, and an unbounded wait wedged the loop. */
    /** [cur]: the kiosk page, or the background engine's while a background ticket runs (PHONE-FGS-EXEC). */
    private suspend fun js(code: String): String = cur.js(code)
    private suspend fun ensure() = cur.ensure()
    private suspend fun jsObj(code: String): JSONObject? = cur.obj(code)
    private fun q(s: String) = JSONObject.quote(s)
    private fun typeKeys(s: String) {
        val evs = KeyCharacterMap.load(KeyCharacterMap.VIRTUAL_KEYBOARD).getEvents(s.toCharArray()) ?: return
        for (e in evs) cur.web.dispatchKeyEvent(e)
    }

    private suspend fun tick() {
        if (busy) return
        if (!resumed) { backgroundTick(); return }
        busy = true
        claimedThisTick = false
        loginAttempted = false
        var st = "unread"
        var s: JSONObject? = null
        try {
            ensure()
            s = jsObj("__ex.state()") ?: run { setStatus("page not readable"); return }
            st = when {
                s.optBoolean("challenged") -> "challenged"
                s.optBoolean("loggedIn") -> "logged_in"
                s.optBoolean("pw") || s.optBoolean("email") || s.optBoolean("codeWait") -> "login"
                else -> "other"
            }
            setSecure(st != "logged_in")
            lastPageReadMs = System.currentTimeMillis()
            if (paused()) {
                if (st == "logged_in") { pausedUntilMs = 0L; pauseBtn.text = "Pause"; setStatus("logged in: pause lifted") }
                else { setStatus("paused (${(pausedUntilMs - System.currentTimeMillis()) / 60_000 + 1} min left): no automatic action"); return }
            } else if (pauseBtn.text.toString() != "Pause") pauseBtn.text = "Pause"
            if (st != lastState) {
                // Any arrival at the login page pings at once (also right after a restart). Auto re-login starts at
                // once; only an unconfigured inbox needs a human. No claim/fill/submit happens while logged out.
                if (st == "login") api.event("logout_seen", if (Store.get(this, Store.INBOX_PASS) == null) "LOGGED OUT and auto re-login not configured: open the app, Setup (no orders until then)" else "logged out; trying auto re-login (no orders until logged in)")
                if (st == "challenged") { api.event("error", "challenge page shown; not solved; re-checked by a terminal reload every 30 min"); challengeRecheckMs = System.currentTimeMillis() + CHALLENGE_RECHECK_MS }
                lastState = st
            }
            when (st) {
                // never interact with a challenge; only re-check it by reloading the terminal on a slow cadence
                "challenged" -> if (System.currentTimeMillis() >= challengeRecheckMs && !userActive()) {
                    challengeRecheckMs = System.currentTimeMillis() + CHALLENGE_RECHECK_MS
                    setStatus("challenge page: reloading the terminal to re-check (no solving)"); web.loadUrl(home())
                } else setStatus("challenge page (not solved): re-checking every 30 min. Operator: open the app and check.")
                "logged_in" -> { loginRecovered("logged in"); onLoggedIn(s) }
                "login" -> relogin(fg, s, bg = false)
                else -> if (userActive()) setStatus("not on the terminal; you are using the screen, so no reload")
                        else { setStatus("not on the terminal (${s.optString("host")}); reloading"); web.loadUrl(home()) }
            }
        } catch (e: JsTimeout) {
            setStatus("page did not answer within ${JS_TIMEOUT_MS / 1000} s (${if (resumed) "foreground" else "went to background"}); no action this tick")
        } finally {
            // a woken tick always heartbeats (and reads the account panel when logged in) before going back
            try { heartbeat(st, s, force = wokeAtMs > 0) } catch (e: Exception) { }
            // We brought the Activity forward (a waiting ticket, or a login check): once the claim ran (ticket or none),
            // or the wake window passed, hand the screen back -- unless the operator touched it meanwhile. A successful
            // re-login extends the window so the next tick reads the terminal and posts account_status first.
            val now = System.currentTimeMillis()
            // (a finished re-login attempt, ok or failed, also hands it back: 12:30Z the window outlived a failed attempt and
            // the due retry at ~12:32:41Z was skipped as "bring-to-front already requested")
            if (wokeAtMs > 0 && (claimedThisTick || now > bgWakeUntilMs || (loginAttempted && lastState != "logged_in"))) {
                val touched = lastUserInputMs > wokeAtMs
                wokeAtMs = 0L; bgWakeUntilMs = 0L
                if (!touched && resumed) moveTaskToBack(true)
            }
            busy = false
        }
    }

    /** Activity not resumed: never touch the WebView. Peek (read-only) for a waiting ticket; if one waits, bring the
     *  Activity to the front so the normal resumed tick claims and runs it. A failed bring-to-front burns nothing. */
    private suspend fun backgroundTick() {
        busy = true
        try {
            val p = api.pendingInfo()
            val n = p?.first
            statusStaleH = p?.second ?: -1.0
            lastPending = n ?: -1
            // PHONE-FGS-EXEC step 2: an AMEND needs an on-screen view (the position sheet is opened by a native tap, and on
            // the overlay elementFromPoint returned null, MEASURED 1b.233 10:40Z), so amends go to the front. The app cannot
            // tell an amend from an entry before claiming, so it asks twice: with and without accepts=amend.
            val nEntries = if ((n ?: 0) > 0) api.pendingInfo(amends = false)?.first else n
            val amendsToFront = BgTicket.frontForAmends(n, nEntries)
            val now = System.currentTimeMillis()
            // LOGIN CHECK (PI-20261010-GKFNZSH4-0001): the page is never read in the background, so a logout is only
            // seen by bringing the Activity forward. Why now, or null:
            val loginWhy = when {
                lastState == "login" && now >= reloginNextMs && Store.get(this, Store.INBOX_PASS) != null -> "logged out; re-login retry due"
                now - lastPageReadMs >= LOGIN_CHECK_MS -> "periodic login check"
                statusStaleH >= STATUS_STALE_H && now - lastStaleWakeMs >= STALE_WAKE_MIN_MS -> { lastStaleWakeMs = now; "VM says account_status is ${"%.1f".format(statusStaleH)} h old" }
                else -> null
            }
            // the background engine checks the login / reads the account without the Activity, on the same triggers and
            // also every BG_CHECK_MS while logged in (fresh account_status with the app backgrounded)
            val bgWhy = loginWhy ?: if (now - maxOf(lastPageReadMs, bgLastMs) >= BG_CHECK_MS) "background check" else null
            when {
                paused() -> setStatus("background: paused" + if ((n ?: 0) > 0) " ($n ticket(s) waiting)" else "")
                now < bgWakeUntilMs -> setStatus("background: bring-to-front already requested")
                // tickets run in the background engine (PHONE-FGS-EXEC); the front is the fallback (BgTicket)
                amendsToFront -> ticketToFront(if (nEntries == null) "$n ticket(s) waiting, kinds unread (entry-only peek failed)" else "${n!! - nEntries} amend(s) waiting (the position sheet needs the screen)")
                (n ?: 0) > 0 && now < bgTicketNextMs -> setStatus("background: $n ticket(s) waiting; engine retry in ${(bgTicketNextMs - now) / 1000 + 1} s")
                (n ?: 0) > 0 && BgTicket.route(now, bgNoAnswer, bgTicketOffUntilMs) == BgTicket.Route.ENGINE -> bgJob("$n ticket(s) waiting", ticket = true)
                (n ?: 0) > 0 -> ticketToFront("$n ticket(s) waiting; " + if (bgNoAnswer >= 2) "engine unavailable" else "engine fallback for ${(bgTicketOffUntilMs - now) / 60_000 + 1} min")
                bgWhy != null && bgNoAnswer < 2 -> bgJob(bgWhy)
                loginWhy != null -> { bgWakeUntilMs = now + BG_WAKE_MAX_MS; setStatus("background: $loginWhy; engine unavailable, bringing the executor to the front"); bgNoAnswer = 0; bringToFront() }
                n == null -> setStatus("background: pending check failed (VM unreachable or refused); no claim")
                else -> setStatus("background: no ticket waiting")
            }
        } finally {
            try { heartbeat("background", null) } catch (e: Exception) { }
            busy = false
        }
    }

    /** One BACKGROUND ENGINE job (BgWeb): read the page; logged out -> the same re-login as the kiosk; logged in -> open
     *  the terminal and post account_status. Never a ticket. A job whose page never answers counts toward the
     *  bring-to-front fallback (2 in a row), which resets the count so the engine is tried again afterwards. */
    private suspend fun bgJob(why: String, ticket: Boolean = false) {
        val p = BgWeb.attach(this, execSrc)
        bgRuns += 1; bgLastMs = System.currentTimeMillis()
        if (p == null) { bgLast = "no_engine"; bgNoAnswer = 2; setStatus("background: $why; engine could not be created"); if (ticket) ticketToFront("$why; engine could not be created"); return }
        setStatus("background: $why; checking in the background engine (${BgWeb.engine})")
        try {
            p.load(home())
            var s = p.settle(30_000L)
            if (s == null) { bgNoAnswer += 1; bgLast = "no_answer"; setStatus("background: engine page did not answer ($bgNoAnswer in a row)"); return }
            bgNoAnswer = 0; bgOk += 1
            lastPageReadMs = System.currentTimeMillis()
            val st = when {
                s.optBoolean("challenged") -> "challenged"
                s.optBoolean("loggedIn") -> "logged_in"
                s.optBoolean("pw") || s.optBoolean("email") || s.optBoolean("codeWait") -> "login"
                else -> "other"
            }
            if (st != lastState) {
                if (st == "login") api.event("logout_seen", "logged out (background engine); trying auto re-login (no orders until logged in)")
                lastState = st
            }
            bgLast = st
            when (st) {
                "login" -> {
                    loginAttempted = false
                    relogin(p, s, bg = true)
                    bgLast = if (lastState == "logged_in") "login_ok" else if (loginAttempted) "login_fail" else "login_wait"
                }
                "logged_in" -> {
                    loginRecovered("logged in")
                    if (!s.optBoolean("onAccount") && s.optString("singleAccountLink").startsWith("https://trade.breakoutprop.com/")) {
                        p.load(s.optString("singleAccountLink")); s = p.settle(20_000L) ?: s
                    }
                    if (ticket) { bgTicket(p, s); bgLast = "ticket+$bgTicketLast"; return }
                    // PHONE-FGS-EXEC: no ticket runs in this job, so an open order form is one a finished ticket left
                    // (Breakout keeps the panel open across reloads; after the 11:42Z background dry fill every check
                    // read "busy" and account_status stopped). Close it with its panel toggle, then read.
                    var closed = ""
                    if (p.obj("__ex.terminal()")?.optBoolean("ticketOpen") == true) {
                        // a stray modal first, by the shared cancel rule (Cancel only; Escape when a Confirm stays open)
                        if ((p.obj("__ex.dismissModals()")?.optInt("found") ?: 0) > 0) { delay(800); p.ensure() }
                        closed = p.js("__ex.closeTicket()"); delay(1200); p.ensure()
                    }
                    var acct = "busy"
                    for (i in 0 until 10) { acct = readAccount(p); if (acct != "busy") break; delay(2000) }
                    if (acct == "posted") lastAcctMs = System.currentTimeMillis()
                    bgLast = "logged_in+$acct" + (if (closed.isNotEmpty()) " form:$closed" else "")
                }
                "challenged" -> setStatus("background: challenge page (not solved); re-checked on the next background check")
                else -> setStatus("background: engine not on the terminal (${s.optString("host")})")
            }
            // a ticket job that never reached a logged-in page: not repeated every tick (the login path has its own backoff);
            // a re-login that just succeeded lets the next tick claim at once
            if (ticket && lastState != "logged_in") bgTicketNextMs = System.currentTimeMillis() + 2 * 60_000L
        } catch (e: JsTimeout) { bgNoAnswer += 1; bgLast = "js_timeout"; if (ticket) bgTicketNextMs = System.currentTimeMillis() + 60_000L }
        finally { cur = fg; BgWeb.detach(this) }
    }

    /** BACKGROUND TICKET (PHONE-FGS-EXEC): the engine page is logged in. Open the account terminal on it, then the SAME
     *  claimAndRun() the kiosk runs, with [cur] = the engine page. No claim reached -> this ticket goes to the front.
     *  A bad result (BgTicket.countsAgainst) -> tickets go to the front for BAD_RESULT_RETRY_MS, one event says so. */
    private suspend fun bgTicket(p: Page, s0: JSONObject) {
        var s = s0
        if (!s.optBoolean("onAccount")) {
            val saved = Store.get(this, Store.TERMINAL_URL)
            if (saved != null && saved.startsWith("https://trade.breakoutprop.com/")) { p.load(saved); s = p.settle(20_000L) ?: s }
        }
        cur = p
        lastClaimId = ""; lastClaimKind = ""
        try {
            if (!s.optBoolean("onAccount") || !ensureTerminal()) {
                bgTicketLast = "no_terminal"
                bgTicketNextMs = System.currentTimeMillis() + BgTicket.NO_CLAIM_RETRY_MS
                ticketToFront("engine logged in but not on the account terminal")
                return
            }
            claimedThisTick = false
            claimAndRun()
            if (!claimedThisTick) {   // backoff / VM unreachable / refused: no ticket held; the next tick asks again
                bgTicketLast = "no_claim"; bgTicketNextMs = System.currentTimeMillis() + 60_000L; return
            }
            bgTickets += 1
            val st = if (lastClaimId.isEmpty()) null else ledger.last(lastClaimId)
            bgTicketLast = (if (lastClaimKind == "amend") "amend:" else "") + (st ?: "none")
            if (BgTicket.countsAgainst(lastClaimKind, st)) {
                bgTicketOffUntilMs = System.currentTimeMillis() + BgTicket.BAD_RESULT_RETRY_MS
                api.event("bg_ticket_fallback", "background ticket ended $st (${BgWeb.engine}); next tickets bring the app to the front for ${BgTicket.BAD_RESULT_RETRY_MS / 3_600_000} h, then the engine is retried", lastClaimId)
            }
        } finally { cur = fg }
    }

    /** The pre-PHONE-FGS-EXEC path, kept as the fallback: bring the Activity forward so the resumed tick claims. */
    private fun ticketToFront(why: String) {
        bgWakeUntilMs = System.currentTimeMillis() + BG_WAKE_MAX_MS
        bgNoAnswer = 0   // as on the login path: the engine is tried again after this front run
        setStatus("background: $why; bringing the executor to the front")
        bringToFront()
    }

    /** Allowed from the background because the operator granted "Display over other apps" (as for the boot restart). */
    private fun bringToFront() {
        try { startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)) }
        catch (e: Exception) { setStatus("background: bring-to-front failed (${e.javaClass.simpleName}); overlay=${Settings.canDrawOverlays(this)}") }
    }

    /** HEARTBEAT (2026-10-06 08:15Z: an hour with no claim and no event, because every no-claim branch but one only
     *  set the on-screen status). On EVERY tick path, at most every 2 min: the status line, the page state, the
     *  pause / touch-hold flags and the terminal gate. Our own state and UI labels only; never links or values. */
    private suspend fun heartbeat(st: String, s: JSONObject?, force: Boolean = false) {
        val now = System.currentTimeMillis()
        Liveness.markTick(this)   // long jobs (re-login, mail wait) heartbeat too: they count as alive
        if (!force && now - lastHbMs < HEARTBEAT_MS) return
        lastHbMs = now
        val state = JSONObject().put("st", st).put("paused", paused()).put("hold", userActive())
            .put("host", s?.optString("host") ?: "").put("onAccount", s?.optBoolean("onAccount") ?: false)
            .put("path_depth", s?.optInt("path_depth") ?: 0)
            .put("fg", resumed).put("jsTimeouts", fg.timeouts).put("pending", lastPending)
            // the page state the LAST foreground tick read (logged_in / login / challenged / other; "" = never read):
            // a backgrounded heartbeat says st=background and nothing else, so a logout overnight stayed invisible
            // until the app was opened (2026-10-10, breakout_2 last balance 10-09 15:01Z, background all night).
            .put("last", lastState)
            // auto re-login: consecutive failures, minutes to the next retry, whether the red flag was sent
            .put("relogin_failures", reloginFailures).put("relogin_flagged", reloginFlagged)
            .put("relogin_next_min", if (reloginNextMs > now) (reloginNextMs - now) / 60_000 + 1 else 0L)
            // background engine: which one, jobs run / jobs whose page answered, the last job's result and its age
            .put("bg", BgWeb.engine).put("bg_runs", bgRuns).put("bg_ok", bgOk).put("bg_last", bgLast)
            .put("bg_last_min", if (bgLastMs > 0) (now - bgLastMs) / 60_000 else -1L)
            // PHONE-FGS-EXEC: tickets the engine claimed, the last one's ledger state, minutes left on the front fallback
            .put("bg_tickets", bgTickets).put("bg_ticket_last", bgTicketLast)
            .put("bg_ticket_off_min", if (bgTicketOffUntilMs > now) (bgTicketOffUntilMs - now) / 60_000 + 1 else 0L)
            // PHONE-KEEPALIVE: what keeps this app alive, and what had to bring it back
            .put("battery_exempt", getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(packageName))
            .put("overlay", Settings.canDrawOverlays(this)).put("fgs_running", Liveness.fgsRunning)
            .put("alarm_exact", KeepAliveService.exactAlarmOk(this))
            .put("last_restart_reason", Liveness.lastRestartReason(this)).put("restarts_24h", Liveness.restarts24h(this))
            .put("app_version", appVersion())
        // never touch the WebView while backgrounded (jsObj is bounded anyway and yields null on a timeout)
        if (resumed) jsObj("__ex.terminal()")?.let { tm ->
            for (k in listOf("ready", "probe", "panels", "orderControl", "ticketOpen", "buySell")) state.put(k, tm.optBoolean(k))
            state.put("tabs", tm.optInt("tabs")).put("inputs", tm.optInt("inputs"))
        }
        if (resumed && st == "logged_in" && now - lastAcctMs >= ACCT_MS) { lastAcctMs = now; state.put("acct", readAccount(fg)) }
        api.heartbeat(lastStatus, state)
    }

    private fun appVersion(): String = try {
        val pi = packageManager.getPackageInfo(packageName, 0)
        "${pi.versionName}/${if (Build.VERSION.SDK_INT >= 28) pi.longVersionCode else pi.versionCode.toLong()}"
    } catch (e: Exception) { "?" }

    /** Read the account panel (read-only) and post it as account_status; only on the terminal with no ticket open.
     *  Returns a short state for the heartbeat: posted / unread / busy. Nothing is posted unless a value was read. */
    private suspend fun readAccount(p: Page): String {
        val tm = p.obj("__ex.terminal()") ?: return "busy"
        if (!tm.optBoolean("ready") || tm.optBoolean("ticketOpen")) return "busy"
        var a = p.obj("__ex.accountPanel()") ?: return "unread"
        fun d(o: JSONObject, k: String): Double? = if (o.isNull(k)) null else o.optDouble(k).takeIf { !it.isNaN() }
        // PROP-TRACKING (operator 2026-10-12 "we need to be able to track the prop trades"): the headline shows only
        // "Portfolio", so balance + the terminal's own uPnL are read on the Portfolio TAB of the bottom panel when the
        // headline did not show them. Tab clicks only (read-only views); never inside a ticket (checked above).
        if (d(a, "balance") == null || d(a, "unrealized") == null) {
            if (p.js("__ex.clickText(${q("^portfolio$")}, '[role=tab],button,[role=button]')") == "clicked") {
                delay(1200); p.ensure()
                p.obj("__ex.accountPanel()")?.let { b2 -> a = JSONObject().put("balance", d(a, "balance") ?: d(b2, "balance") ?: JSONObject.NULL)
                    .put("equity", d(a, "equity") ?: d(b2, "equity") ?: JSONObject.NULL)
                    .put("portfolio", d(a, "portfolio") ?: d(b2, "portfolio") ?: JSONObject.NULL)
                    .put("unrealized", d(a, "unrealized") ?: d(b2, "unrealized") ?: JSONObject.NULL) }
            }
        }
        val bal = d(a, "balance")
        val eqRead = d(a, "equity")
        val pf = d(a, "portfolio")
        val eq = eqRead ?: pf   // "Portfolio" is the terminal's headline account value; the label is posted so the server records which one
        if (bal == null && eq == null) return "unread"
        // Positions every read (the server reconciles the journal against it); Closed orders only once Positions read
        // cleanly (they carry the exit of a position that disappeared). The Positions view is left up afterwards.
        var pos = readView(p, "^positions\\b", "__ex.positionsRead()")
        // BREAKOUT-SL-AMEND-CLICK: a side the grid draws without text is read from the row's sheet (MEASURED)
        // only on the FOREGROUND kiosk: the background engine's overlay is not hit-testable (1b.233/1b.241: elementFromPoint
        // null at every target), so there the side stays unread and the server's journal fallback (ESTIMATED) applies
        if (pos != null && pos.optString("state") == "side_unread" && p === fg) pos = sheetSides(p, pos)
        val closed = if (pos?.optString("state") == "read") readView(p, "^(closed orders|trades)\\b", "__ex.closedRead()") else null
        if (closed != null) { p.js("__ex.clickText(${q("^positions\\b")})"); delay(600) }
        val ps = pos?.optString("state") ?: "unanswered"
        // A Closed orders read that was not clean is dumped like a Positions miss (state prefixed "closed_"), so its
        // layout can be fixed from evidence; the server keeps the latest miss per account.
        val cs = closed?.optString("state")
        if (closed != null && cs != "read" && System.currentTimeMillis() - lastPosMissMs >= POS_MISS_MS) {
            lastPosMissMs = System.currentTimeMillis()
            api.positionsMiss("closed_$cs", closed.optJSONArray("headers") ?: JSONArray(), closed.optJSONArray("controls") ?: JSONArray(),
                closed.optJSONArray("cells") ?: JSONArray())
        }
        if (ps != "read" && System.currentTimeMillis() - lastPosMissMs >= POS_MISS_MS) {
            lastPosMissMs = System.currentTimeMillis()
            api.positionsMiss((ps + " " + (pos?.optString("detail") ?: "")).trim(), pos?.optJSONArray("headers") ?: JSONArray(), pos?.optJSONArray("controls") ?: JSONArray(),
                pos?.optJSONArray("cells") ?: JSONArray())
        }
        api.accountStatus(bal, eq, if (eqRead != null) "equity" else "portfolio", d(a, "unrealized"), pos, closed)
        return if (ps == "read") "posted" else "posted_pos_$ps"
    }

    /** Wait (poll) until the bottom-panel tab chips exist: the SPA renders them after the document is "complete". */
    private suspend fun waitChips(p: Page, maxMs: Long): JSONObject? {
        val end = System.currentTimeMillis() + maxMs
        var tc: JSONObject? = null
        while (true) {
            tc = try { p.ensure(); p.obj("__ex.tabChips()") } catch (e: JsTimeout) { null }
            if ((tc?.optInt("chips") ?: 0) > 0 || System.currentTimeMillis() >= end) return tc
            delay(1000)
        }
    }

    /** Show one bottom-panel view by its tab label and read it. Runs against the loaded /account/<id>/trade terminal:
     *  waits for the chips; when the page is an account page but not /trade it opens the terminal first (not while the
     *  operator is using the screen). A genuine miss returns state no_tab plus a masked dump of what IS on the page. */
    private suspend fun readView(p: Page, tabRe: String, reader: String): JSONObject? {
        var tc = waitChips(p, CHIPS_WAIT_MS)
        if ((tc?.optInt("chips") ?: 0) == 0 && tc?.optBoolean("trade") == false && tc?.optInt("acct_href") == 1 && !(p === fg && userActive())) {
            val href = p.js("__ex.terminalHref()")
            if (href.startsWith("https://trade.breakoutprop.com/")) { p.load(href); p.settle(20_000L); tc = waitChips(p, CHIPS_WAIT_MS) }
        }
        if (p.js("__ex.clickTab(${q(tabRe)})") != "clicked") {
            val d = p.obj("__ex.missDump()") ?: JSONObject()
            return JSONObject().put("state", "no_tab").put("headers", d.optJSONArray("headers") ?: JSONArray())
                .put("controls", d.optJSONArray("controls") ?: JSONArray())
                .put("detail", "d${d.optInt("path_depth")} c${d.optInt("chips")} t${if (d.optBoolean("trade")) 1 else 0}")
        }
        delay(1200); p.ensure()
        var r = p.obj(reader)
        // the panel fills in after the click: no table yet / header but no rows yet are re-read a few times before
        // they count as a miss (a flat Positions view is only believed on positive evidence, never on "not rendered")
        var tries = 0
        while (tries < 4 && r?.optString("state").let { it == "no_table" || it == "empty_unconfirmed" }) {
            delay(1500); tries += 1; p.ensure(); r = p.obj(reader) ?: r
        }
        return r
    }

    /** Where "back to the terminal" goes: the last account terminal seen while logged in, else the host root. */
    private fun home(): String = Store.get(this, Store.TERMINAL_URL)?.takeIf { it.startsWith("https://trade.breakoutprop.com/") } ?: TRADE_URL

    /** Logged in. Inside an account: remember it and work tickets. On the account landing: open the ONE account
     *  (single link, or the remembered terminal); never reload the landing; ambiguous = wait for one human tap. */
    private suspend fun onLoggedIn(s: JSONObject) {
        if (s.optBoolean("onAccount")) {
            // Claim ONLY on the trading terminal (05:21Z dry test claimed on an account page without one and burned
            // the ticket). Not ready -> open this account's /trade page and wait; still not ready -> no claim.
            if (!ensureTerminal()) return
            val href = js("location.href")
            if (href.startsWith("https://trade.breakoutprop.com/") && href != Store.get(this, Store.TERMINAL_URL)) Store.put(this, Store.TERMINAL_URL, href)
            claimAndRun(); return
        }
        if (userActive()) { setStatus("logged in (account list); you are using the screen, so no navigation"); return }
        val one = s.optString("singleAccountLink")
        val saved = Store.get(this, Store.TERMINAL_URL)
        when {
            one.startsWith("https://trade.breakoutprop.com/") -> { setStatus("logged in: opening the account"); web.loadUrl(one) }
            saved != null && s.optInt("accountLinkCount") == 0 -> { setStatus("logged in: opening the remembered account"); web.loadUrl(home()) }
            else -> setStatus("logged in on the account list: tap into the Breakout account ONCE (no tickets until then)")
        }
    }

    private suspend fun terminalReady(): Boolean = jsObj("__ex.terminal()")?.optBoolean("ready") == true

    private suspend fun ensureTerminal(): Boolean {
        if (terminalReady()) return true
        if (cur === fg && userActive()) { setStatus("logged in, not on the terminal; you are using the screen, so no navigation"); return false }
        val href = js("__ex.terminalHref()")
        if (!href.startsWith("https://trade.breakoutprop.com/")) { setStatus("logged in, not on the terminal and no account path: tap into the account once"); return false }
        setStatus("logged in: opening the account's terminal")
        cur.load(href)
        for (i in 0 until 12) { delay(2500); ensure(); if (terminalReady()) return true }
        setStatus("terminal did not load (no Order/Order form control, Buy-Sell tabs, panels or buy+sell markers): no claim")
        // Self-diagnosing miss (fix 06:17Z): post the page's control texts once per load attempt, never a report.
        val tm = jsObj("__ex.terminal()")
        val ctl = try { JSONArray(js("JSON.stringify(__ex.controls())")) } catch (e: Exception) { JSONArray() }
        api.terminalMiss("tabs=${tm?.optInt("tabs")} inputs=${tm?.optInt("inputs")} probe=${tm?.optBoolean("probe")}", ctl)
        return false
    }

    // ---------------- auto re-login ----------------
    /** PHONE-AUTOLOGIN-2. Runs on the kiosk page ([bg] false) or the background engine's page ([bg] true).
     *  1. email submitted (or a code page found);
     *  2. the waiting page is POLLED (every 3 s, up to 90 s) until its number(s) render -- the old build read it ONCE
     *     6 s after the submit and failed "no single number on the waiting page" (MEASURED 12:30:35Z, 12:40:45Z);
     *  3. the dedicated inbox is POLLED (every 10 s, up to 5 min: "that can take at least up to a minute");
     *  4. LoginMatch decides: LINEUP (>= 2 numbers on the page) -> tap the ONE the mail names; ONE number on the page ->
     *     open the ONE mail link whose text is that number. Zero or several candidates: nothing is acted on;
     *  5. the page must then read logged in.
     *  PHONE-AUTOLOGIN-3 (live 13:52Z/13:56Z: page_n=0 for 90 s after "email=clicked"): the submit is VERIFIED to move
     *  the page (else Enter), the inbox is polled IN PARALLEL from the submit on, the page wait stretches to 150 s while
     *  no mail has arrived, and each attempt posts its login_diag in parts with the scrubbed page structure (postDiag).
     *  Counts, steps and timings only (never a number, link or address). */
    private suspend fun relogin(p: Page, s: JSONObject, bg: Boolean) {
        val now = System.currentTimeMillis()
        // the engine already logged in: the kiosk only needs a reload to pick up the shared session
        if (!bg && bgLoginAtMs > fgReloadMs) { fgReloadMs = now; setStatus("logged in by the background engine: reloading the terminal"); p.load(home()); return }
        if (!bg && bgLoginAtMs > 0 && now - fgReloadMs < 45_000L) { setStatus("reloading after the background engine's login; not starting another"); return }
        if (!bg && loginDeferred()) { setStatus("logged out; you are touching the screen, so auto re-login waits until 60 s after your last tap"); return }
        if (now < reloginNextMs) { setStatus("logged out; auto re-login attempt ${reloginFailures + 1} in ${(reloginNextMs - now) / 60_000 + 1} min (failed $reloginFailures in a row)"); return }
        val email = Store.get(this, Store.LOGIN_EMAIL)
        val user = Store.get(this, Store.INBOX_USER)
        val pass = Store.get(this, Store.INBOX_PASS)
        if (email == null || user == null || pass == null) { setStatus("logged out; auto re-login not configured (Setup)"); return }
        loginAttempted = true
        setStatus("logged out: starting email login (attempt ${reloginFailures + 1}${if (bg) ", background" else ""})")
        // the first attempt of a streak pings; retries are logged quietly (the red flag comes from loginFail)
        if (reloginFailures == 0) api.event("login_started", if (bg) "background engine" else "") else api.event("login_retry", "attempt ${reloginFailures + 1} starting")
        val d = JSONObject().put("eng", if (bg) BgWeb.engine else "fg")   // scrubbed diag: counts, steps, timings
        val t0 = now
        var step = "start"
        fun secs() = (System.currentTimeMillis() - t0) / 1000
        try {
            // MAIL WINDOW: a code page this attempt reached by submitting the email is answered by mail sent after that
            // submit (-60 s clock slack); a code page we FOUND (an earlier attempt, ours or the operator's) by mail up to
            // 30 min old.
            var since = Date(now - 30 * 60_000L)
            var st0 = s
            if (!st0.optBoolean("codeWait") && st0.optString("host").startsWith("trade.")) {
                step = "app"; p.load(APP_URL)
                st0 = p.settle(20_000L) ?: return loginFail("app.breakoutprop.com not readable", p, d.put("step", step))
                if (st0.optBoolean("challenged")) return loginFail("app.breakoutprop.com shows a challenge (not solved)", p, d.put("step", step))
            }
            var dec = LoginMatch.Decision("none", null, 0)
            // PHONE-AUTOLOGIN-3: a structure snapshot BEFORE the submit, so the diag can say whether the page changed
            val sig0 = p.obj("__ex.loginProbe()")?.optString("sig") ?: ""
            d.put("_sig0", sig0)
            if (!st0.optBoolean("codeWait")) {
                step = "email"
                since = Date(System.currentTimeMillis() - 60_000)
                p.ensure()
                val r = p.js("__ex.loginEmail(${q(email)})")
                d.put("email", r)
                p.obj("__ex.loginSubmitInfo()")?.let { d.put("sub_where", it.optString("where")).put("sub_btns", it.optInt("buttons")).put("sub_label", "(" + it.optString("label") + ")") }
                if (r != "clicked" && r != "submitted" && r != "no_button") return loginFail("email step: $r", p, d.put("step", step))
                // VERIFY THE SUBMIT ADVANCED the page (live 13:52Z/13:56Z: zero numbers for 90 s after "clicked"):
                // up to 12 s for the page to change, else press Enter in the field and wait again
                var adv = if (r == "no_button") false else advanced(p, sig0, 12_000L)
                if (!adv) {
                    val e = try { p.ensure(); p.js("__ex.loginEnter()") } catch (x: JsTimeout) { "js_timeout" }
                    d.put("enter", e)
                    adv = advanced(p, sig0, 12_000L)
                }
                d.put("adv", adv).put("adv_s", secs())
                if (r == "no_button" && !adv) return loginFail("email step: no continue control and Enter did not advance the page", p, d.put("step", step))
            } else d.put("found_code_page", true)
            // MAIL, IN PARALLEL with the page poll (PHONE-AUTOLOGIN-3): the inbox is read from the submit on, so the diag
            // says what arrived even when the page never shows a number, and a mail that came early is not waited for twice
            val mailEnd = System.currentTimeMillis() + MAIL_WAIT_MS
            var latest: List<MailNums> = emptyList(); var polls = 0; var mails = 0; var mailErr = ""; var firstMailS = -1L
            var mailAuthFailed = false
            // its own Job (cancelled in the finally below): an inbox failure can never cancel the tick loop
            val mailJob = CoroutineScope(currentCoroutineContext() + Job()).launch {
                try {
                    while (isActive && System.currentTimeMillis() < mailEnd) {
                        // re-read every poll: a Setup save during an attempt takes effect on the next poll (§ 7.19)
                        val u = Store.get(this@MainActivity, Store.INBOX_USER) ?: user
                        val pw = Store.get(this@MainActivity, Store.INBOX_PASS) ?: pass
                        val sc = Mail.scan(u, pw, since); polls += 1
                        latest = sc.mails; mails = maxOf(mails, sc.mails.size); sc.error?.let { mailErr = it }
                        if (sc.error != null && polls == 1 || sc.authFailed) postChunks("inbox err=${sc.error} mech=${sc.mech.ifEmpty { "-" }} " +
                            "srv=(${sc.detail}) ${Creds.userShape(Creds.user(u))} ${Creds.passShape(Creds.pass(pw))}")
                        // Gmail refused the credential: retrying it every 10 s cannot help and invites Gmail's login
                        // throttle (24-28 refused logins per attempt, MEASURED 2026-10-10). One check per attempt.
                        if (sc.authFailed) { mailAuthFailed = true; break }
                        if (sc.mails.isNotEmpty() && firstMailS < 0) firstMailS = secs()
                        delay(MAIL_POLL_MS)
                    }
                } catch (e: CancellationException) { throw e } catch (e: Exception) { mailErr = e.javaClass.simpleName }
            }
            fun mailDiag() {
                d.put("mails", mails).put("polls", polls).put("mail_first_s", firstMailS)
                if (mailErr.isNotEmpty()) d.put("mail_err", mailErr)
                val n = latest.firstOrNull()
                d.put("mshape", LoginMatch.mailShape(n))
                if (n != null) d.put("m_numlinks", n.links.size).put("m_shown", n.shown.size).put("m_phrased", n.phrased.size)
                    .put("m_links", n.allLinks).put("m_act", n.actLinks)
            }
            try {
                // PAGE: poll until the number(s) render; past PAGE_WAIT_MS keep going while no mail has arrived yet, up to
                // PAGE_WAIT_MAX_MS (the page may only move once the mail is sent)
                step = "page"
                var choices = emptyList<String>(); var single: String? = null
                val pageEnd = System.currentTimeMillis() + PAGE_WAIT_MS
                val pageMax = System.currentTimeMillis() + PAGE_WAIT_MAX_MS
                while (true) {
                    delay(PAGE_POLL_MS)
                    try { p.ensure() } catch (e: JsTimeout) { }
                    val c = p.obj("__ex.loginChoices()")
                    if (c != null) {
                        val top = strs(c.optJSONArray("top")); val btn = strs(c.optJSONArray("btn"))
                        choices = LoginMatch.lineup(top, btn); single = LoginMatch.single(top, btn)
                        d.put("page_n", c.optInt("n")).put("page_top", top.size).put("page_btn", btn.size)
                    }
                    if (choices.isNotEmpty() || single != null) break
                    val tNow = System.currentTimeMillis()
                    if (mailAuthFailed) { mailDiag(); return loginFail("dedicated inbox refused the login (AuthenticationFailedException; see the inbox login_diag)", p, d.put("step", step)) }
                    if (tNow > pageMax || (tNow > pageEnd && mails > 0)) {
                        mailDiag()
                        return loginFail("no number or lineup on the waiting page within ${(tNow - t0) / 1000} s (mail: ${d.optString("mshape")})", p, d.put("step", step))
                    }
                    heartbeat(if (bg) "background" else "login", null)
                }
                d.put("shape", if (choices.isNotEmpty()) "lineup" else "single").put("choices", choices.size).put("page_s", secs())
                // MAIL: decide on what the parallel poller has read
                step = "mail"
                setStatus("waiting for the Breakout email in the dedicated inbox (up to ${MAIL_WAIT_MS / 60_000} min)")
                while (true) {
                    dec = LoginMatch.decide(choices, single, latest)
                    if (dec.kind != "none" || System.currentTimeMillis() > mailEnd || mailAuthFailed) break
                    heartbeat(if (bg) "background" else "login", null)
                    delay(2_000L)
                }
                mailDiag()
                d.put("match", dec.n).put("tier", dec.tier).put("mail_s", secs())
            } finally { mailJob.cancel() }
            when (dec.kind) {
                "ambiguous" -> return loginFail("the mail matches ${dec.n} candidates; nothing tapped (fail closed)", p, d.put("step", "match"))
                "none" -> return loginFail(if (mails == 0 && mailErr.isNotEmpty()) "dedicated inbox not readable ($mailErr)"
                    else "no Breakout mail naming exactly one of the page's numbers within ${MAIL_WAIT_MS / 60_000} min ($mails mail(s) seen)", p, d.put("step", step))
                "tap" -> {
                    step = "tap"; p.ensure()
                    val r = p.js("__ex.loginPick(${q(dec.value!!)})")
                    d.put("pick", r)
                    if (r != "clicked") return loginFail("lineup tap: $r", p, d.put("step", step))
                }
                // The link opens in a throwaway WebView (same profile, same cookie store) and the WAITING PAGE STAYS:
                // with the number check, it is the waiting page that completes once the link is confirmed. Loading the
                // link into the waiting page itself discarded it, and 2026-10-10 18:37Z / 18:54Z ended on "Sign in".
                else -> { step = "link"; d.put("link_view", openLinkAside(dec.value!!)) }
            }
            // VERIFY: let the waiting page finish on its own (up to 60 s), then the terminal must read logged in
            step = "verify"
            var ok = false
            for (i in 0 until 20) {
                delay(PAGE_POLL_MS)
                val a = try { p.ensure(); p.obj("__ex.state()") } catch (e: JsTimeout) { null }
                if (a?.optBoolean("loggedIn") == true) { ok = true; break }
            }
            // Not yet: reload the terminal and keep reading. 2026-10-10 18:37Z the first reload landed on the portal's
            // "Loading..." page and settle() returned at readyState=complete, before the session reached the
            // terminal, so a good link read as a failed login (operator: "can also try reloading in that situation").
            // Up to 3 reloads, each read for up to 30 s, stopping the moment the terminal reads logged in.
            for (r in 0 until 3) {
                if (ok) break
                p.load(home()); d.put("reloads", r + 1)
                val end = System.currentTimeMillis() + 30_000L
                while (System.currentTimeMillis() < end) {
                    if (p.settle(6_000L)?.optBoolean("loggedIn") == true) { ok = true; break }
                    heartbeat(if (bg) "background" else "login", null)
                    delay(PAGE_POLL_MS)
                }
            }
            d.put("verify_s", secs())
            if (ok) {
                d.put("step", "ok"); postDiag(p, d)
                lastState = "logged_in"; setStatus("re-login OK${if (bg) " (background)" else ""}")
                if (bg) bgLoginAtMs = System.currentTimeMillis()
                loginRecovered("auto re-login via dedicated inbox (${d.optString("shape")}${if (bg) ", background" else ""})")
                if (wokeAtMs > 0) bgWakeUntilMs = System.currentTimeMillis() + BG_WAKE_MAX_MS   // read the terminal before going back
            } else loginFail("not logged in after the ${dec.kind}", p, d.put("step", step))
        } catch (e: JsTimeout) {
            loginFail("page stopped answering at the $step step", p, d.put("step", step).put("js_timeout", true))
        }
    }

    private fun strs(a: JSONArray?): List<String> = if (a == null) emptyList() else (0 until a.length()).map { a.optString(it) }

    /** PHONE-AUTOLOGIN-3: did the page move after the email submit? (the email field is gone, a code page or a login
     *  reads, or the text signature changed) -- polled every 1.5 s for up to [maxMs]. */
    /** Open the mail's link in a separate, never-shown WebView that shares the app's cookie store, wait until its
     *  document completes (up to 25 s), then destroy it. Returns a short diag token: "done", "timeout" or "err".
     *  Never logs the URL (it carries the one-time code). */
    @SuppressLint("SetJavaScriptEnabled")
    private suspend fun openLinkAside(url: String): String {
        val w = try { WebView(applicationContext) } catch (e: Exception) { return "err" }
        try {
            w.settings.javaScriptEnabled = true; w.settings.domStorageEnabled = true
            w.settings.allowFileAccess = false; w.settings.allowContentAccess = false
            CookieManager.getInstance().setAcceptThirdPartyCookies(w, true)
            w.webViewClient = WebViewClient()
            val dm = resources.displayMetrics
            w.measure(android.view.View.MeasureSpec.makeMeasureSpec(dm.widthPixels, android.view.View.MeasureSpec.EXACTLY),
                android.view.View.MeasureSpec.makeMeasureSpec(dm.heightPixels, android.view.View.MeasureSpec.EXACTLY))
            w.layout(0, 0, dm.widthPixels, dm.heightPixels)
            w.onResume(); w.resumeTimers()
            val aside = Page(w, "")
            w.loadUrl(url)
            val end = System.currentTimeMillis() + 25_000L
            delay(2_000L)
            while (System.currentTimeMillis() < end) {
                val rs = try { aside.js("document.readyState") } catch (e: JsTimeout) { "" }
                if (rs == "complete") { delay(3_000L); CookieManager.getInstance().flush(); return "done" }
                delay(1_000L)
            }
            return "timeout"
        } finally { try { w.stopLoading(); w.destroy() } catch (e: Exception) { } }
    }

    private suspend fun advanced(p: Page, sig0: String, maxMs: Long): Boolean {
        val end = System.currentTimeMillis() + maxMs
        while (System.currentTimeMillis() < end) {
            delay(1_500L)
            try {
                p.ensure()
                val s = p.obj("__ex.state()") ?: continue
                if (!s.optBoolean("email") || s.optBoolean("codeWait") || s.optBoolean("loggedIn")) return true
                val sig = p.obj("__ex.loginProbe()")?.optString("sig")
                if (sig != null && sig0.isNotEmpty() && sig != sig0) return true
            } catch (e: JsTimeout) { }
        }
        return false
    }

    /** The login_diag events: "k=v" COUNTS, step names and timings, then the scrubbed page STRUCTURE (exec.js
     *  loginProbe: tag histogram, shadow roots, iframes, input types, phrases present, numeric tokens COUNTED, and
     *  button / heading / label texts with every digit turned into "N" and emails / links dropped). The server keeps
     *  160 chars of [A-Za-z0-9 _.:/()=+-] per event, so the tokens are packed into numbered parts ("dI/N ..."), at
     *  most DIAG_PARTS; the core line comes first. Keys starting with "_" are internal. Never a value. */
    private suspend fun postDiag(p: Page, d: JSONObject) {
        val toks = d.keys().asSequence().filter { !it.startsWith("_") }.map { "$it=${d.opt(it)}" }.toMutableList()
        val pr = try { p.ensure(); p.obj("__ex.loginProbe()") } catch (e: Exception) { null }
        if (pr != null) {
            val sig0 = d.optString("_sig0")
            if (sig0.isNotEmpty()) toks += "changed=${pr.optString("sig") != sig0}"
            for (k in listOf("cands", "numtok", "digits", "chars", "shadow", "frames", "frames_xo")) toks += "$k=${pr.optInt(k)}"
            toks += "ph=" + strs(pr.optJSONArray("phrases")).joinToString("+").ifEmpty { "-" }
            val inp = pr.optJSONObject("inputs")
            toks += "in=" + (inp?.keys()?.asSequence()?.joinToString("+") { "$it.${inp.optInt(it)}" } ?: "").ifEmpty { "-" }
            toks += "tags=" + strs(pr.optJSONArray("tags")).joinToString("+")
            toks += strs(pr.optJSONArray("texts")).map { "(" + it.replace("(", " ").replace(")", " ").take(40) + ")" }
        } else toks += "probe=unread"
        val parts = mutableListOf<String>(); var cur = ""
        for (raw in toks) {
            val tk = raw.take(DIAG_PART_CHARS)
            if (cur.isNotEmpty() && cur.length + 1 + tk.length > DIAG_PART_CHARS) { parts += cur; cur = "" }
            cur = if (cur.isEmpty()) tk else "$cur $tk"
        }
        if (cur.isNotEmpty()) parts += cur
        val n = minOf(parts.size, DIAG_PARTS)
        for (i in 0 until n) api.event("login_diag", "d${i + 1}/$n ${parts[i]}")
    }

    /** NO LATCH: every failure schedules the next attempt (2, 5, 10, 20, then 30 min forever). Failures before and
     *  after the red flag are logged quietly (login_retry); the RELOGIN_FLAG_AFTER-th sends ONE login_failed.
     *  Each failure also posts ONE quiet login_diag (the scrubbed structure snapshot) so it is diagnosable from the VM. */
    private suspend fun loginFail(why: String, p: Page, d: JSONObject) {
        reloginFailures += 1
        val waitMin = RELOGIN_BACKOFF_MIN[minOf(reloginFailures - 1, RELOGIN_BACKOFF_MIN.size - 1)]
        reloginNextMs = System.currentTimeMillis() + waitMin * 60_000L
        try { d.put("host", p.obj("__ex.state()")?.optString("host") ?: "unread") } catch (e: Exception) { d.put("host", "unread") }
        try { postDiag(p, d) } catch (e: Exception) { }
        if (reloginFailures >= RELOGIN_FLAG_AFTER && !reloginFlagged) {
            reloginFlagged = true
            api.event("login_failed", "auto re-login failed $reloginFailures times in a row; STILL RETRYING every <= 30 min: $why")
        } else api.event("login_retry", "failed ($reloginFailures): $why; next try in $waitMin min")
        setStatus("login failed ($reloginFailures in a row): $why; retrying in $waitMin min")
        if (p !== fg || !userActive()) p.load(home())
    }

    /** Logged in (by us or by hand): end the failure streak; ONE recovery event only if the red flag was sent,
     *  else the usual login_ok after an auto re-login. */
    private suspend fun loginRecovered(how: String) {
        val auto = how.startsWith("auto")
        if (reloginFlagged) api.event("login_ok", "RECOVERED ($how) after $reloginFailures failed auto re-logins")
        else if (auto) api.event("login_ok", how)
        reloginFailures = 0; reloginNextMs = 0L; reloginFlagged = false
    }

    // ---------------- tickets ----------------
    private suspend fun claimAndRun() {
        // NEVER claim while backgrounded: the page cannot be driven, and a claimed ticket is one attempt only.
        // (PHONE-FGS-EXEC: the background engine's page CAN be driven; cur !== fg only inside bgTicket())
        if (!resumed && cur === fg) { setStatus("backgrounded: no claim"); return }
        val now = System.currentTimeMillis()
        if (now < claimBackoffUntilMs) { setStatus("logged in · VM busy (5xx); backing off ${(claimBackoffUntilMs - now) / 1000 + 1} s (no claim)"); return }
        // accepts amend: this build executes trail amends (PROP-TRAIL-PHONE); the VM serves them to no older build.
        // PHONE-FGS-EXEC step 2: the background engine claims ENTRIES only (accepts=[]); amends are claimed on the kiosk.
        val r = api.post("claim", JSONObject().put("accepts", JSONArray().apply { if (cur === fg) put("amend") })) ?: run { setStatus("logged in · VM unreachable (no claim, no click)"); return }
        if (!r.optBoolean("ok")) {
            // 5xx (e.g. the API restarting for a deploy): ONE attempt, no in-tick retry, and no execution -- we hold no
            // ticket, so nothing can be filled or submitted. Back off 1 -> 2 -> 4 -> 5 min. If the server committed the
            // claim before the 502, that ticket is never served again (status 'claimed'; it expires server-side).
            val http = r.optInt("http")
            if (http >= 500) {
                claim5xx += 1
                claimBackoffUntilMs = System.currentTimeMillis() + minOf(60_000L shl minOf(claim5xx - 1, 3), 300_000L)
                setStatus("logged in · claim failed (http $http); backing off, will not retry this tick")
            } else setStatus("logged in · claim refused (http $http)")
            return
        }
        claim5xx = 0; claimBackoffUntilMs = 0L
        claimedThisTick = true
        val t = r.optJSONObject("ticket")
        if (t == null) { setStatus("logged in · waiting for a ticket (none queued) · ${java.text.DateFormat.getTimeInstance().format(Date())}"); return }
        lastClaimKind = if (t.optString("kind") == "amend") "amend" else "entry"
        lastClaimId = if (lastClaimKind == "amend") t.optString("amend_id") else t.optString("ticket_id")
        if (t.optString("kind") == "amend") {
            val aid = t.optString("amend_id")
            try { executeAmend(t, r.optJSONObject("config") ?: JSONObject()) }
            catch (e: JsTimeout) {
                // after the save click the stop is UNKNOWN (mismatch, the VM locks the trail and pings); before it, nothing changed
                if (ledger.last(aid) == "submitted") { ledger.append(aid, "unconfirmed"); amendResult(t, "mismatch", "page stopped answering after the save click", null, null, null) }
                else if (ledger.last(aid) == "intended") { ledger.append(aid, "refused"); amendResult(t, "refused", "page stopped answering before any save (nothing saved)", null, null, null) }
            }
            return
        }
        val id = t.optString("ticket_id")
        try { execute(t, r.optJSONObject("config") ?: JSONObject()) }
        catch (e: JsTimeout) {
            // The page stopped answering mid-ticket (e.g. the app was sent to the background). Fail closed: after a
            // submit click it is UNCONFIRMED for a human; before one, a refusal (nothing was submitted).
            if (ledger.last(id) == "submitted") {
                ledger.append(id, "unconfirmed")
                api.event("mismatch", "page stopped answering after submit; CHECK the terminal", id)
                setStatus("ticket $id UNCONFIRMED — operator check")
            } else if (ledger.last(id) == "intended") refuse(id, "page stopped answering mid-fill (not submitted)", null)
        }
    }

    private fun num(s: String?): Double? = s?.replace(",", "")?.replace(" ", "")?.toDoubleOrNull()
    private fun roundTo(v: Double, step: Double) = round(v / step) * step
    private fun fmt(v: Double, step: Double): String {
        val dec = maxOf(0, -floor(kotlin.math.log10(step)).toInt())
        return String.format(java.util.Locale.US, "%.${dec}f", v)
    }

    private suspend fun execute(t: JSONObject, cfg: JSONObject) {
        val id = t.getString("ticket_id")
        if (ledger.seen(id)) { report(id, "skipped", "ledger already holds this ticket", null); return }
        ledger.append(id, "intended")
        val meta = t.optJSONObject("meta") ?: JSONObject()
        val test = meta.optBoolean("test")
        val live = t.optString("submit") == "live" && !test
        val sym = t.optString("symbol").uppercase()
        val inst = cfg.optJSONObject("instruments")?.optJSONObject(sym)
        val venue = t.optString("venue_symbol").ifEmpty { inst?.optString("venue") ?: "" }
        if (venue.isEmpty() || inst == null) return refuse(id, "symbol not mapped in prop_platforms phone_accounts", null)
        val qStep = inst.optDouble("qty_step", Double.NaN); val pStep = inst.optDouble("price_step", Double.NaN)
        if (qStep.isNaN() || pStep.isNaN()) return refuse(id, "qty/price step not declared", null)
        val base = venue.uppercase().removeSuffix("USDT").removeSuffix("USD").removeSuffix("/").removeSuffix("-")
        val long = t.optString("direction").lowercase() == "long"
        val sideTab = if (long) "Buy" else "Sell"
        val sideRe = if (long) Regex("long|buy", RegexOption.IGNORE_CASE) else Regex("short|sell", RegexOption.IGNORE_CASE)
        val oppRe = if (long) Regex("short|sell", RegexOption.IGNORE_CASE) else Regex("long|buy", RegexOption.IGNORE_CASE)
        val qty = floor(t.optDouble("qty", 0.0) / qStep + 1e-9) * qStep
        val entry = roundTo(t.optDouble("entry", 0.0), pStep)
        val sl = roundTo(t.optDouble("sl", 0.0), pStep); val tp = roundTo(t.optDouble("tp", 0.0), pStep)
        if (qty <= 0.0) return refuse(id, "qty below the venue step", null)
        if (!(if (long) sl < entry && entry < tp else tp < entry && entry < sl)) return refuse(id, "bracket geometry wrong for the side", null)
        setStatus("ticket $id: ${if (live) "LIVE" else "DRY"} $sideTab ${fmt(qty, qStep)} $venue @ ${fmt(entry, pStep)}")

        // 1. SYMBOL FIRST, then the ticket, then VERIFY before anything is typed (fix 2026-10-05 ~22:40Z: the first
        //    dry ticket found the terminal on BTC, nothing selected ETH, and the ticket was refused "symbol ETH not
        //    verified" with the page's own BTC values in the dump; our code had typed nothing). The venue symbol is
        //    selected on the page before the ticket is opened or touched; the ticket's submit label ("Long (buy) ETH")
        //    must then name the asset. Every move is a selection; a route that cannot be verified refuses with its name.
        //    SOL-PICKER (2026-10-07): the first route is the terminal's "Select market" chip (open, pick ONE row,
        //    read back), then the watchlist; the picker state is reset per ticket so a failed pick never carries over.
        js("__ex.symbolReset()")
        // PHONE-FGS-EXEC step 2: the engine page was reloaded at the start of its job; also Cancel any open dialog that
        // is not the order form (exactly one "Cancel" button, never Confirm) and press Escape, before the symbol step
        var reset = ""
        if (cur !== fg) { reset = "cancelled " + js("__ex.cancelModals()") + "; "; js("__ex.escape()"); delay(400); ensure() }
        var route = reset + selectSymbol(venue, base)
        if (js("__ex.openTicket()") == "no_order_control") return refuse(id, "order control not found (symbol route: $route)", jsObj("__ex.ticket()"))
        delay(1500); ensure()
        var shown = js("__ex.symbolOnTicket()")
        if (!shown.uppercase().contains(base)) {
            // second round with the ticket open: its own header may be the symbol picker
            route += " | ticket open: " + selectSymbol(venue, base)
            if (js("__ex.openTicket()") == "clicked") delay(1500)
            shown = js("__ex.symbolOnTicket()")
        }
        var tk = jsObj("__ex.ticket()") ?: return refuse(id, "ticket unreadable", null)
        if (!tk.optBoolean("open")) return refuse(id, "ticket not open (symbol route: $route)", tk)
        if (!shown.uppercase().contains(base)) return refuse(id, "symbol $base not on the submit label (ticket shows '${shown.ifEmpty { "?" }}'; symbol route: $route)", tk)
        // the market chip, when the page has exactly one, must agree with the submit label (both read, before any typing)
        val chip = js("__ex.marketShown()")
        if (chip.isNotEmpty() && chip != base) return refuse(id, "market chip shows '$chip', submit label shows '$shown' (expected $base; symbol route: $route)", tk)
        // 2. type + side
        js("__ex.tab('Limit')"); delay(500); js("__ex.tab(${q(sideTab)})"); delay(700)
        if (js("__ex.tabSelected('Limit')") != "true" || js("__ex.tabSelected(${q(sideTab)})") != "true") return refuse(id, "Limit/$sideTab tab not selected", jsObj("__ex.ticket()"))
        if (!js("__ex.symbolOnTicket()").uppercase().contains(base)) return refuse(id, "submit label lost $base after the tabs", jsObj("__ex.ticket()"))
        // 3. price + quantity, BY LABEL (never by index alone): each field is located by its label at the moment it
        //    is typed and again when it is read back; n != 1 (missing or ambiguous label) refuses with the labels seen.
        val fPrice = Field("Limit price", "limit price"); val fQty = Field("Quantity", "quantity")
        for (f in listOf(fPrice, fQty)) { val n = readField(f)?.optInt("n", 0) ?: 0; if (n != 1) return refuse(id, "${f.name} field not unique (n=$n)", jsObj("__ex.ticket()")) }
        if (!typeInto(fPrice, fmt(entry, pStep), entry, pStep)) return refuse(id, "limit price did not read back", jsObj("__ex.ticket()"))
        // quantity UNIT before the quantity is typed (MEASURED am-3 09:16Z: a "Toggle quantity unit" button reading
        // "USD", so 0.01 would have been $0.01). Toggle ONCE to the base asset and verify; never convert to a notional.
        val unit0 = js("__ex.qtyUnit()")
        if (unit0.isNotEmpty() && !unit0.uppercase().contains(base)) {
            if (js("__ex.toggleQtyUnit()") != "clicked") return refuse(id, "quantity unit toggle not clickable (shows '$unit0')", jsObj("__ex.ticket()"))
            delay(800); ensure()
            val unit1 = js("__ex.qtyUnit()")
            if (!unit1.uppercase().contains(base)) return refuse(id, "quantity unit toggle did not reach $base (was '$unit0', now '$unit1')", jsObj("__ex.ticket()"))
        }
        if (!typeInto(fQty, fmt(qty, qStep), qty, qStep)) return refuse(id, "quantity did not read back", jsObj("__ex.ticket()"))
        // quantity unit verified AFTER typing: the unit toggle names the base asset, else (no toggle on this layout) the
        // "Quantity ... available" alert or the field's own adornment must; the refusal carries all three texts.
        tk = jsObj("__ex.ticket()") ?: return refuse(id, "ticket unreadable", null)
        val alerts = (tk.optJSONArray("alerts")?.toString() ?: "").uppercase()
        val qNear = (readField(fQty)?.optString("near") ?: "").uppercase()
        val unitNow = js("__ex.qtyUnit()").uppercase()
        val unitOk = if (unitNow.isNotEmpty()) unitNow.contains(base) else (alerts.contains(base) || qNear.contains(base))
        if (!unitOk) return refuse(id, "quantity unit not verified as $base (toggle '$unitNow'; alerts $alerts; near '$qNear')", tk)
        // 4. TP/SL: open the section (checkbox or the "TP/SL" control, MEASURED am-3), then the fields BY LABEL
        // one move per step with read-back (am-4 09:48Z: box checked but the section stayed collapsed)
        // deterministic candidates by attempt (exec.js openTpsl), each followed by a ~3 s poll for "Take profit price"
        var tpslSeq = ""
        var phase = 0   // each candidate is clicked at most ONCE (a second click on an accordion would collapse it)
        for (i in 0 until 4) {
            val r = js("__ex.openTpsl($phase)")
            tpslSeq += (if (tpslSeq.isEmpty()) "" else ">") + r
            if (r == "ok" || r == "none" || r == "ambiguous" || (r == "wait" && phase >= 2)) break
            if (r == "expanded" || r == "switched" || r == "wait") phase++
            var shown = false
            for (w in 0 until 6) { delay(500); ensure(); if (js("__ex.readByLabel('take ?profit|\\\\btp\\\\b','price').n") != "0") { shown = true; break } }
            if (shown) { tpslSeq += ">ok"; break }
        }
        if (!tpslSeq.endsWith("ok")) return refuse(id, "TP/SL section not opened ($tpslSeq)", jsObj("__ex.ticket()"))
        val fTp = Field("TP price", "take ?profit|\\btp\\b", "price"); val fSl = Field("SL price", "stop ?loss|\\bsl\\b", "price")
        for (f in listOf(fTp, fSl)) { val r = readField(f); if (r?.optInt("n", 0) != 1) return refuse(id, "${f.name} field not unique (n=${r?.optInt("n", 0) ?: 0}, labels ${r?.optJSONArray("labels")})", jsObj("__ex.ticket()")) }
        if (!typeInto(fTp, fmt(tp, pStep), tp, pStep)) return refuse(id, "TP did not read back", jsObj("__ex.ticket()"))
        if (!typeInto(fSl, fmt(sl, pStep), sl, pStep)) return refuse(id, "SL did not read back", jsObj("__ex.ticket()"))
        // 5. full read-back, by label, all four at once; the four labels must resolve to four DIFFERENT inputs
        delay(500)
        tk = jsObj("__ex.ticket()") ?: return refuse(id, "ticket unreadable", null)
        val fields = listOf(fPrice, fQty, fTp, fSl); val wants = listOf(entry, qty, tp, sl); val steps = listOf(pStep, qStep, pStep, pStep)
        val reads = fields.map { readField(it) }
        val ks = reads.map { it?.optInt("k", -1) ?: -1 }
        if (ks.any { it < 0 } || ks.toSet().size != 4) return refuse(id, "fields not unique or collide at read-back (k=$ks)", tk)
        val ok = reads.indices.all { close(num(reads[it]?.optString("value")), wants[it], steps[it]) }
        val sub = tk.optJSONObject("submit")
        val subText = sub?.optString("text") ?: ""
        if (!ok) return refuse(id, "read-back mismatch", tk)
        if (!subText.uppercase().contains(base)) return refuse(id, "submit label lost $base at read-back", tk)
        if (tk.optBoolean("tpsl") != true) return refuse(id, "TP/SL box not on at read-back", tk)
        if (!sideRe.containsMatchIn(subText) || oppRe.containsMatchIn(subText)) return refuse(id, "submit label does not match the side", tk)
        if (sub?.optBoolean("disabled") != false) return refuse(id, "submit disabled after fill", tk)
        // 5b. SERVER read-back (design 3.4, PHONE-GO-TOKEN): a SECOND, independent check; every phone-side check above
        //     stays. The phone posts what the page SHOWS (raw strings); the server re-verifies it against the ticket and
        //     the venue steps and issues ONE token bound to the ticket id + the read-back hash (30 s). No token
        //     (unreachable, refused) = no submit. Test tickets and dry accounts get a "dry" token that never says go.
        val fieldsRb = listOf(fPrice, fQty, fTp, fSl)
        val unitSrc = unitNow.ifEmpty { qNear.ifEmpty { alerts } }
        val rb = readBack(id, tk, fieldsRb, unitSrc)
        val rbHash = sha256(readBackLines(rb))
        val v = api.post("verify", JSONObject().put("ticket_id", id).put("readback", rb).put("readback_sha256", rbHash))
            ?: return refuse(id, "server verify unreachable (no go-token; not submitted)", tk)
        if (!v.optBoolean("ok")) return refuse(id, "server read-back refused: ${v.optJSONArray("reasons")?.toString()?.take(200) ?: "http " + v.optInt("http")}", tk)
        val token = v.optString("token")
        val serverLive = v.optString("mode") == "live"
        if (token.isEmpty() || v.optString("readback_sha256") != rbHash) return refuse(id, "server verify answered without a token bound to this read-back", tk)

        if (!live || !serverLive) {
            // exercise the redeem on the dry path too: a DRY token must answer go=false (never a click either way). A LIVE
            // token on a dry path (the claim said dry, the server now says live) is left to expire unredeemed.
            val g = if (serverLive) null else api.post("go", JSONObject().put("ticket_id", id).put("token", token).put("readback_sha256", rbHash))
            val goTxt = if (serverLive) "live token not redeemed (app dry)" else if (g == null) "go unreachable" else if (g.optBoolean("go")) "go=TRUE on a dry token" else "go=false (${g.optString("reason").take(40)})"
            if (g?.optBoolean("go") == true) api.event("mismatch", "server said go on a DRY token; not submitted", id)
            ledger.append(id, "dry_filled")
            report(id, "dry_filled", (if (test) "test ticket (always dry)" else "server mode dry") + "; server verify ok, ${v.optString("mode")} token, $goTxt", tk)
            api.event("dry_fill_ok", engTag() + "filled + read back + SERVER verified (${v.optString("mode")} token, $goTxt), NOT submitted: $sideTab ${fmt(qty, qStep)} $venue lim ${fmt(entry, pStep)} tp ${fmt(tp, pStep)} sl ${fmt(sl, pStep)}", id)
            js("__ex.setByLabel('quantity', '', '')")
            setStatus("DRY ticket $id filled and read back; not submitted")
            return
        }
        // 6. LIVE submit: re-read the form NOW, re-hash, redeem the token (one-shot, server-side); click only on go=true
        val tkNow = jsObj("__ex.ticket()") ?: return refuse(id, "ticket unreadable before submit", null)
        val hashNow = sha256(readBackLines(readBack(id, tkNow, fieldsRb, unitSrc)))
        if (hashNow != rbHash) return refuse(id, "form changed since the server verified it (not submitted)", tkNow)
        val go = api.post("go", JSONObject().put("ticket_id", id).put("token", token).put("readback_sha256", hashNow))
            ?: return refuse(id, "go-token redeem unreachable (not submitted)", tkNow)
        if (!go.optBoolean("go")) return refuse(id, "go-token refused: ${go.optString("reason").ifEmpty { "http " + go.optInt("http") }}", tkNow)
        ledger.append(id, "submitted")
        if (js("__ex.submit()") != "clicked") { ledger.append(id, "refused"); return refuse(id, "submit not clickable", tk) }
        delay(1500)
        jsObj("__ex.dialog()")?.let { d ->
            if (sideRe.containsMatchIn(d.optString("text")) && !oppRe.containsMatchIn(d.optString("text"))) js("__ex.dialogClick('confirm|place|submit|yes|ok')")
            else { js("__ex.dialogClick('cancel|close|no')"); api.event("mismatch", "unexpected confirmation dialog; cancelled", id); return refuse(id, "unexpected confirmation dialog", tk) }
            delay(1500)
        }
        // 7. confirm by re-read
        for (attempt in 0 until 3) {
            for (view in listOf("^open orders$", "^positions$")) {
                js("__ex.clickText(${q(view)})"); delay(1500)
                val rows = js("JSON.stringify(__ex.rows())").uppercase()
                val hit = rows.split("],[").any { r -> (r.contains(venue.uppercase()) || r.contains(base)) && sideRe.containsMatchIn(r) }
                // BREAKOUT-UNTRACKED-1014: Positions is a DIV grid (no <tr>), invisible to rows(); a clean grid read
                // showing this symbol on this side also confirms the submit. Read-only; the opp/flatten path stays rows()-only.
                val grid = if (view == "^positions$") jsObj("__ex.positionsRead()")?.takeIf { it.optString("state") == "read" }?.optJSONArray("rows") else null
                val gridHit = grid != null && (0 until grid.length()).any { i ->
                    val g = grid.optJSONObject(i); val gs = g?.optString("symbol")?.uppercase() ?: ""
                    gs.isNotEmpty() && ((venue.isNotEmpty() && gs.contains(venue.uppercase())) || (base.isNotEmpty() && gs.contains(base))) && sideRe.containsMatchIn(g?.optString("side") ?: "")
                }
                val opp = view == "^positions$" && rows.split("],[").any { r -> (r.contains(venue.uppercase()) || r.contains(base)) && oppRe.containsMatchIn(r) && !sideRe.containsMatchIn(r) }
                if (opp) {
                    js("__ex.rowClose(${q(venue)}, ${q(oppRe.pattern)})"); delay(1200); js("__ex.dialogClick('confirm|close|yes|ok')")
                    ledger.append(id, "contained")
                    api.event("flattened", "opposite-side position found after submit; close clicked; CHECK the terminal", id)
                    report(id, "mismatch_flattened", "opposite side seen", tk); return
                }
                if (hit || gridHit) {
                    ledger.append(id, "placed")
                    api.post("report", JSONObject().put("kind", "fill").put("status", "placed").put("ticket_id", id)
                        .put("symbol", sym).put("direction", if (long) "long" else "short")
                        .put("entry", entry).put("sl", sl).put("tp", tp).put("qty", qty).put("source", "phone_executor"))
                    api.event("submitted", engTag() + "placed $sideTab ${fmt(qty, qStep)} $venue lim ${fmt(entry, pStep)} (seen in ${view.trim('^', '$')})", id)
                    setStatus("LIVE ticket $id placed and seen")
                    return
                }
            }
        }
        ledger.append(id, "unconfirmed")
        api.event("mismatch", "submitted but not found in Open orders/Positions after 3 reads; CHECK the terminal", id)
        setStatus("ticket $id UNCONFIRMED — operator check")
    }

    // ---------------- trail amends (PROP-TRAIL-PHONE) ----------------
    /** Edit the stop (and a TP revision) of ONE open position, the way the entry is filled: locate by label, demand
     *  exactly one match, read EVERY value back, never save without the read-back, then re-read the terminal.
     *  Order: Positions row (unique for symbol+side) -> the terminal must show the stop the VM believes is resting
     *  (else human_moved: a human's stop is never overridden) -> the row's ONE edit control -> the ONE dialog -> SL
     *  (and TP) by label, typed and read back, the kept TP read back unchanged -> DRY: cancel and report; LIVE: the ONE
     *  save button, then the position re-read (row column, else the edit dialog re-opened read-only and cancelled).
     *  Anything unread or ambiguous before the save is "refused" (nothing changed); after it, "mismatch" (the VM
     *  locks this ticket's trail and pings). One attempt per amend id (the ledger), never retried here. */
    private suspend fun executeAmend(t: JSONObject, cfg: JSONObject) {
        val aid = t.getString("amend_id")
        if (ledger.seen(aid)) return amendResult(t, "refused", "ledger already holds this amend", null, null, null)
        ledger.append(aid, "intended")
        val live = t.optString("submit") == "live"   // the server decides (ARMED-GATE: no device-local switch)
        val sym = t.optString("symbol").uppercase()
        val inst = cfg.optJSONObject("instruments")?.optJSONObject(sym)
        val venue = t.optString("venue_symbol").ifEmpty { inst?.optString("venue") ?: "" }
        if (venue.isEmpty() || inst == null) return amendRefuse(t, "symbol not mapped in prop_platforms phone_accounts", null)
        val pStep = inst.optDouble("price_step", Double.NaN)
        if (pStep.isNaN()) return amendRefuse(t, "price step not declared", null)
        val long = t.optString("direction").lowercase() == "long"
        val sideRe = if (long) "long|buy" else "short|sell"
        val fromSl = t.optDouble("from_sl", Double.NaN); val sl = t.optDouble("sl", Double.NaN)
        val fromTp = t.optDouble("from_tp", Double.NaN); val tp = t.optDouble("tp", Double.NaN)
        if (fromSl.isNaN() || sl.isNaN()) return amendRefuse(t, "amend has no from_sl/sl", null)
        val tpChange = !tp.isNaN() && !(!fromTp.isNaN() && close(tp, fromTp, pStep))
        // never loosen: the trail only tightens; a TP revision carries the SL unchanged
        if (if (long) sl < fromSl - pStep / 2 else sl > fromSl + pStep / 2) return amendRefuse(t, "amend would loosen the stop", null)
        setStatus("amend $aid: ${if (live) "LIVE" else "DRY"} $venue SL ${fmt(fromSl, pStep)} -> ${fmt(sl, pStep)}${if (tpChange) " TP -> ${fmt(tp, pStep)}" else ""}")
        // 1. the position row
        js("__ex.clickText('^positions$')"); delay(1500); ensure()
        var pos = jsObj("__ex.posInfo(${q(venue)}, ${q(sideRe)})") ?: return amendRefuse(t, "positions unreadable", null)
        // BREAKOUT-SL-AMEND-CLICK: the live Positions view is a div grid with no side text and no edit control, so a
        // row it cannot match (n=0) or a grid row goes to the position SHEET, which reads side + volume MEASURED
        if (pos.optInt("n") == 0 || pos.optString("layout") == "div_grid") return executeAmendSheet(t, live, venue, long, pStep, fromSl, sl, tpChange)
        if (pos.optInt("n") != 1) return amendRefuse(t, "position row not unique (n=${pos.optInt("n")})", pos)
        // 2. the terminal must show the stop/target the VM believes is resting (row columns, when the layout has them)
        val rowSl = firstNum(pos.optString("sl", "")); val rowTp = firstNum(pos.optString("tp", ""))
        if (rowSl != null && !close(rowSl, fromSl, pStep)) return amendHuman(t, "row SL $rowSl != expected ${fmt(fromSl, pStep)}", pos)
        if (rowTp != null && !fromTp.isNaN() && !close(rowTp, fromTp, pStep)) return amendHuman(t, "row TP $rowTp != expected ${fmt(fromTp, pStep)}", pos)
        // 3. the row's ONE edit control, then the ONE dialog
        val e = js("__ex.posEdit(${q(venue)}, ${q(sideRe)})")
        if (e != "clicked") {
            // MEASURE FIRST (BREAKOUT-POS-UNPARSED): the row's control dump also goes to the account's visible positions
            // diag (state amend_<why>), since the amend_result form only lands in the ticket's meta
            try { api.positionsMiss("amend_$e", pos.optJSONArray("headers") ?: JSONArray(), pos.optJSONArray("ctl_lines") ?: JSONArray()) } catch (x: Exception) { }
            return amendRefuse(t, "position edit control: $e", pos)
        }
        delay(1500); ensure()
        val d0 = jsObj("__ex.editDialog()")
        if (d0?.optInt("n") != 1) { js("__ex.dlgCancel()"); return amendRefuse(t, "edit dialog not unique (n=${d0?.optInt("n")})", pos) }
        val fSl = Field("SL price", "stop ?loss|\\bsl\\b", "price"); val fTp = Field("TP price", "take ?profit|\\btp\\b", "price")
        val slF = dlgField(fSl) ?: return amendCancelRefuse(t, "dialog SL field unreadable", d0)
        val tpF = dlgField(fTp)
        if (slF.optInt("n") != 1) return amendCancelRefuse(t, "dialog SL field not unique (n=${slF.optInt("n")}, labels ${slF.optJSONArray("labels")})", d0)
        if ((tpChange || !fromTp.isNaN()) && tpF?.optInt("n") != 1) return amendCancelRefuse(t, "dialog TP field not unique (n=${tpF?.optInt("n")})", d0)
        // 4. the dialog's own values must be the resting ones before anything is typed
        val dSl = num(slF.optString("value"))
        if (dSl == null || !close(dSl, fromSl, pStep)) { js("__ex.dlgCancel()"); return amendHuman(t, "dialog SL ${slF.optString("value")} != expected ${fmt(fromSl, pStep)}", d0) }
        if (!fromTp.isNaN()) { val dTp = num(tpF?.optString("value")); if (dTp == null || !close(dTp, fromTp, pStep)) { js("__ex.dlgCancel()"); return amendHuman(t, "dialog TP ${tpF?.optString("value")} != expected ${fmt(fromTp, pStep)}", d0) } }
        // 5. type + read back, all of it, by label
        if (!dlgType(fSl, fmt(sl, pStep), sl, pStep)) return amendCancelRefuse(t, "SL did not read back in the dialog", jsObj("__ex.editDialog()"))
        if (tpChange && !dlgType(fTp, fmt(tp, pStep), tp, pStep)) return amendCancelRefuse(t, "TP did not read back in the dialog", jsObj("__ex.editDialog()"))
        delay(400)
        val d1 = jsObj("__ex.editDialog()")
        val slR = num(dlgField(fSl)?.optString("value")); val tpR = if (fromTp.isNaN() && !tpChange) null else num(dlgField(fTp)?.optString("value"))
        val wantTp = if (tpChange) tp else fromTp
        if (slR == null || !close(slR, sl, pStep)) return amendCancelRefuse(t, "SL read-back mismatch ($slR)", d1)
        if (!wantTp.isNaN() && (tpR == null || !close(tpR, wantTp, pStep))) return amendCancelRefuse(t, "TP read-back mismatch ($tpR)", d1)
        if (!live) {
            val c = js("__ex.dlgCancel()"); delay(800)
            ledger.append(aid, "dry_amended")
            amendResult(t, "dry_amended", "typed + read back in the dialog, NOT saved (server mode dry; cancel: $c)", slR, tpR, d1)
            setStatus("DRY amend $aid read back; not saved"); return
        }
        // 6. LIVE save: the ONE save button
        ledger.append(aid, "submitted")
        val sv = js("__ex.dlgSave()")
        if (sv != "clicked") { ledger.append(aid, "refused"); return amendCancelRefuse(t, "dialog save control: $sv", d1) }
        delay(1500); ensure()
        // a follow-up confirmation is accepted only if it carries no input and its ONE save/confirm button is not a close
        jsObj("__ex.editDialog()")?.let { d2 ->
            if (d2.optInt("n") == 1 && (d2.optJSONArray("inputs")?.length() ?: 0) == 0) { js("__ex.dlgSave()"); delay(1500) }
        }
        if ((jsObj("__ex.editDialog()")?.optInt("n") ?: 0) > 0) {
            js("__ex.dlgCancel()"); ledger.append(aid, "unconfirmed")
            return amendResult(t, "mismatch", "edit dialog still open after the save click", null, null, jsObj("__ex.editDialog()"))
        }
        // 7. read back on the terminal: the row's SL/TP columns, else the edit dialog re-opened read-only and cancelled
        js("__ex.clickText('^positions$')"); delay(1500); ensure()
        val p2 = jsObj("__ex.posInfo(${q(venue)}, ${q(sideRe)})")
        if (p2 == null || p2.optInt("n") != 1) { ledger.append(aid, "unconfirmed"); return amendResult(t, "mismatch", "position row not unique after the save (n=${p2?.optInt("n")})", null, null, p2) }
        var vSl = firstNum(p2.optString("sl", "")); var vTp = firstNum(p2.optString("tp", ""))
        if (vSl == null || (vTp == null && !wantTp.isNaN())) {
            if (js("__ex.posEdit(${q(venue)}, ${q(sideRe)})") == "clicked") {
                delay(1500); ensure()
                vSl = num(dlgField(fSl)?.optString("value"))
                if (!wantTp.isNaN()) vTp = num(dlgField(fTp)?.optString("value"))
                js("__ex.dlgCancel()"); delay(800)
            }
        }
        val okSl = vSl != null && close(vSl, sl, pStep)
        val okTp = wantTp.isNaN() || (vTp != null && close(vTp, wantTp, pStep))
        if (okSl && okTp) {
            ledger.append(aid, "amended")
            amendResult(t, "amended", "saved and read back on the terminal", vSl, vTp, p2)
            setStatus("LIVE amend $aid: SL ${fmt(sl, pStep)} read back")
        } else {
            ledger.append(aid, "unconfirmed")
            amendResult(t, "mismatch", "after the save the terminal shows SL $vSl TP $vTp (asked ${fmt(sl, pStep)} / ${if (wantTp.isNaN()) "-" else fmt(wantTp, pStep)})", vSl, vTp, p2)
            setStatus("amend $aid NOT VERIFIED — operator check")
        }
    }

    // ---------------- position sheet (BREAKOUT-SL-AMEND-CLICK) ----------------
    /** Open the ONE Positions row's bottom sheet for [sym] and read its [Position details] (MEASURED: what the venue
     *  shows). The child row ("ETH 5x") is tapped first, then the group row ("ETH 1"); which one opened the sheet is
     *  reported as opened_by (unmeasured until the first live run). Returns the details (ok, side, qty, entry, mark,
     *  upnl, upnl_signed, opened_by, tries) or {ok:false, why, tries}; the sheet is left OPEN only when ok. */
    private suspend fun sheetRead(p: Page, sym: String): JSONObject {
        val tries = JSONArray()
        // BREAKOUT-SL-AMEND-CLICK (manager 11:02Z: an "Edit Take profit" modal was seen open on the engine page, origin
        // unknown): an open Edit Stop loss / Edit Take profit modal is dismissed (its Cancel, else Escape -- never
        // Confirm) before any sheet attempt, and the attempt does not run while one is still open
        val m0 = dismissModal(p)
        if (m0.isNotEmpty()) tries.put("modal_pre:$m0".take(40))
        if (m0.endsWith("still_open")) return JSONObject().put("ok", false).put("why", "modal_open").put("tries", tries)
        var opened = ""
        // REUSE (manager 12:47Z; operator screenshot 12:43Z: a sheet a human opened was left open on the kiosk): a sheet
        // that is ALREADY open for THIS symbol (title == the symbol, one sheet) is read as-is -- no touch needed. Any
        // other open sheet (wrong title, or not exactly one) is closed first, as before: fail closed.
        val pre0 = p.obj("__ex.sheetInfo(${q(sym)})")
        if (pre0?.optBoolean("open") == true) {
            if (pre0.optBoolean("title_ok")) { opened = "pre_open"; tries.put("sheet:pre_open") }
            else { tries.put("sheet:pre_open_other close=${sheetClose(p, sym)}".take(40)) }
        }
        // BREAKOUT-NATIVE-TAP (1b.210, 08:37Z): click() and a synthetic pointer tap reached the cells on both rows and
        // opened no sheet (the group tap only expanded the row). Each row is now touched NATIVELY (a MotionEvent the
        // WebView receives as a trusted touch) at the centre exec.js re-checked; every miss still records what the page
        // showed after it (viewport, new visible texts, the touched cell's ancestor chain)
        // TARGETS (2026-10-17, 1b.253 08:03Z + 12:03Z MEASURED): the Market cell got a fully trusted finger touch
        // (pdown:touch w9 tr1 .. click:touch tr1) and opened nothing, while click() on the group cell reaches the row's
        // handler; so the cell was wrong, not the touch. 1b.261 20:03Z (hd: map) MEASURED why: the Market cell is an
        // <a> with its own onClick and the ROW holds the opener. The child row is tried at its Opening price cell, then
        // its "n TP / n SL" text, then (last) its Market cell. The GROUP row only toggles expand: it is touched ONLY when no child
        // row is in the DOM (to expand it, then the child targets run), never while one is (08:03Z: that collapsed it).
        var probe: JSONObject? = null
        val queue = ArrayDeque(if (opened.isEmpty()) listOf("child_entry", "child_tpsl", "child") else emptyList())
        var grouped = false
        while (queue.isNotEmpty()) {
            val which = queue.removeFirst()
            p.js("__ex.domMark()")
            val r = nativeTap(p, sym, which)
            tries.put("$which/native:$r".take(40))
            lastTapGeom.forEach { tries.put(it.take(40)) }
            if (!r.startsWith("touched")) {
                if (r == "no_grid" || (which == "group" && r == "none")) break
                if ((which == "child_entry" || which == "child") && r == "none" && !grouped) { grouped = true; queue.clear(); queue.addAll(listOf("group", "child_entry", "child_tpsl", "child")) }
                continue
            }
            delay(1200); p.ensure()
            // which touch preceded a modal (manager 11:58Z: attribute any Edit SL/TP modal to the tap that opened it); a
            // modal is dismissed (Cancel/Escape, never Confirm) and NOTHING further is attempted on this read
            if ((p.obj("__ex.modalsOpen()")?.optInt("n") ?: 0) > 0) {
                tries.put("modal_after:$which/native".take(40)); tries.put("modal_dismiss:${dismissModal(p)}".take(40))
                return JSONObject().put("ok", false).put("why", "modal_after_tap").put("tries", tries)
            }
            if (p.obj("__ex.sheetInfo(${q(sym)})")?.optBoolean("open") == true) { opened = "$which/native"; break }
            delay(1500); p.ensure()
            if (p.obj("__ex.sheetInfo(${q(sym)})")?.optBoolean("open") == true) { opened = "$which/native"; break }
            probe = p.obj("__ex.domDiff()")?.put("at", "$which/native")
            // per touch: how many elements appeared and whether a [Position details] tab did (a sheet the reader missed)
            tries.put("$which/native:no_sheet n${probe?.optInt("n_new") ?: -1} d${probe?.optInt("details_tabs") ?: -1}".take(40))
        }
        if (opened.isEmpty()) {
            val m1 = dismissModal(p)
            if (m1.isNotEmpty()) tries.put("modal_post:$m1".take(40))
        }
        if (opened.isEmpty()) return JSONObject().put("ok", false).put("why", "sheet_not_open").put("tries", tries).apply { if (probe != null) put("probe", probe) }
        val si = p.obj("__ex.sheetInfo(${q(sym)})") ?: JSONObject()
        if (!si.optBoolean("title_ok")) {
            val c = sheetClose(p, sym)
            return JSONObject().put("ok", false).put("why", "sheet_title").put("info", si).put("tries", tries).put("close", c)
        }
        p.js("__ex.sheetTab('details')"); delay(800); p.ensure()
        val d = p.obj("__ex.sheetDetails()") ?: JSONObject().put("ok", false).put("why", "details_unanswered")
        d.put("opened_by", opened).put("tries", tries)
        if (!d.optBoolean("ok")) { if (!d.has("why")) d.put("why", "details_unread"); d.put("close", sheetClose(p, sym)) }
        return d
    }

    /** One NATIVE touch on the Positions row cell for [which] (BREAKOUT-NATIVE-TAP): exec.js centres the cell, then
     *  re-checks it right before the touch (sheetTarget: the ONE cell, not denied, and elementFromPoint at its centre
     *  is that cell or inside it with nothing denied up to it); only then is the point touched. The denylist is the same
     *  one every click goes through. "touched k=<css scale>" or why not (none | no_grid | rows_<n> | covered@<tag> |
     *  point_deny_* | deny_* | offscreen | sheet_open | unanswered). */
    private var lastTapGeom: List<String> = emptyList()
    private suspend fun nativeTap(p: Page, sym: String, which: String): String {
        lastTapGeom = emptyList()
        val s = p.obj("__ex.sheetTarget(${q(sym)}, ${q(which)}, true)") ?: return "unanswered"
        if (!s.optBoolean("ok")) return s.optString("why", "refused")
        delay(350)
        val r = p.obj("__ex.sheetTarget(${q(sym)}, ${q(which)}, false)") ?: return "unanswered"
        if (!r.optBoolean("ok")) {
            // geometry of a covered point (measure-only; 1b.233 read "covered" with nothing at the centre)
            lastTapGeom = listOf(r.optString("g1"), r.optString("g2"), p.viewInfo() + (if (p === fg) " fg" else " bg")).filter { it.isNotEmpty() }
            return r.optString("why", "refused") + (if (r.optString("top").isNotEmpty()) "@" + r.optString("top") else "")
        }
        // which WebView took the touch (PHONE-FGS-EXEC: the background engine's non-touchable overlay or the kiosk);
        // MotionEvents go to the view directly, never through the window, but the two are measured apart
        return p.tap(r.getDouble("x"), r.getDouble("y"), r.getDouble("vw")) + (if (p === fg) " fg" else " bg")
    }

    /** Stray-modal dismiss before / after a sheet attempt: exec.js dismissModals (the ONE rule cancelModals also uses --
     *  each open non-form dialog / Edit SL/TP modal gets its own exactly-one "Cancel" clicked, NEVER Confirm; Escape if
     *  one holding a Confirm stays), then a re-check. "" when nothing was open; else "f<found> c<cancelled>[ esc]" plus
     *  " closed" or " still_open" -- and while still_open the caller does nothing else on the page. */
    private suspend fun dismissModal(p: Page): String {
        val d = p.obj("__ex.dismissModals()") ?: return "unanswered still_open"
        if (d.optInt("found") == 0 && !d.optBoolean("esc")) return ""
        delay(700); p.ensure()
        val left = p.obj("__ex.modalsOpen()")?.optInt("n") ?: -1
        return "f${d.optInt("found")} c${d.optInt("cancelled")}${if (d.optBoolean("esc")) " esc" else ""} ${if (left == 0) "closed" else "still_open"}"
    }

    /** Close the sheet: Escape first, then its header close icon. closed | escape | x | still_open(..) */
    private suspend fun sheetClose(p: Page, sym: String): String {
        if (p.obj("__ex.sheetInfo(${q(sym)})")?.optBoolean("open") != true) return "closed"
        p.js("__ex.sheetClose()"); delay(700); p.ensure()
        if (p.obj("__ex.sheetInfo(${q(sym)})")?.optBoolean("open") != true) return "escape"
        val x = p.js("__ex.sheetCloseX()"); delay(700); p.ensure()
        return if (p.obj("__ex.sheetInfo(${q(sym)})")?.optBoolean("open") != true) "x" else "still_open($x)"
    }

    /** The sheet's evidence as short masked lines for the visible positions diag (state sheet_* / amend_sheet_*). No
     *  values: the server masks digits in cells, and only shapes (tries, misses, uPnL text shape, icon dump) go in. */
    private suspend fun sheetMiss(state: String, d: JSONObject, orders: JSONObject? = null, force: Boolean = false) {
        // its own clock (manager 06:09Z): sharing lastPosMissMs with the Positions miss hid every sheet dump for 30 min
        if (!force && System.currentTimeMillis() - lastSheetMissMs < SHEET_MISS_MS) return
        lastSheetMissMs = System.currentTimeMillis()
        val h = JSONArray()
        d.optJSONArray("tries")?.let { a -> for (i in 0 until a.length()) h.put(a.optString(i).take(40)) }
        if (d.has("opened_by")) h.put("opened_by=${d.optString("opened_by")}")
        d.optJSONArray("miss")?.let { a -> for (i in 0 until a.length()) h.put("miss=${a.optString(i)}".take(40)) }
        if (d.has("upnl_shape")) h.put("upnl=${d.optString("upnl_shape")} signed=${d.optBoolean("upnl_signed")}".take(40))
        if (d.has("close")) h.put("close=${d.optString("close")}".take(40))
        val ctl = JSONArray()
        d.optJSONObject("probe")?.let { pr ->
            ctl.put("${pr.optString("at")} w=${pr.optInt("w")} h=${pr.optInt("h")} new=${pr.optInt("n_new")} dlg=${pr.optInt("dialogs")}".take(40))
            ctl.put("tabs details=${pr.optInt("details_tabs")} tpsl=${pr.optInt("tpsl_tabs")}")
            pr.optJSONArray("ev")?.let { a -> for (i in 0 until a.length()) ctl.put("ev: " + a.optString(i).take(35)) }
            pr.optJSONArray("hd")?.let { a -> for (i in 0 until a.length()) ctl.put("hd: " + a.optString(i).take(35)) }
            pr.optJSONArray("leaves")?.let { a -> for (i in 0 until minOf(a.length(), 10)) ctl.put("new: " + a.optString(i).take(34)) }
            pr.optJSONArray("chain")?.let { a -> for (i in 0 until a.length()) ctl.put("up: " + a.optString(i).take(35)) }
        }
        orders?.optJSONArray("rows")?.let { rs ->
            for (i in 0 until rs.length()) {
                val r = rs.optJSONObject(i) ?: continue
                ctl.put("${r.optString("type")} px=${r.optInt("n_px")} pen=${r.optInt("pencils")}")
                r.optJSONArray("icon_lines")?.let { l -> for (j in 0 until l.length()) ctl.put("  " + l.optString(j).take(38)) }
            }
        }
        try { api.positionsMiss(state, h, ctl) } catch (x: Exception) { }
    }

    /** side_unread (the Positions grid draws the side without text): each such row's side, size and avg entry are read
     *  from its sheet's [Position details] (MEASURED, side_source sheet) and the read becomes "read". A row symbol that
     *  repeats cannot be told apart from its sheet, and any unread sheet leaves side_unread untouched for the server's
     *  journal fallback (ESTIMATED). The sheet is closed after each read. A uPnL without a sign is sent as null: a
     *  sign-less loss is never a MEASURED number (open flag; upnl_signed says which). */
    private suspend fun sheetSides(p: Page, pos: JSONObject): JSONObject {
        val rows = pos.optJSONArray("rows") ?: return pos
        val syms = (0 until rows.length()).map { rows.optJSONObject(it)?.optString("symbol") ?: "" }
        if (syms.isEmpty() || syms.any { it.isEmpty() } || syms.toSet().size != syms.size) return pos
        // BREAKOUT-NATIVE-TAP finding (a): on 1b.210 a tap on the GROUP row expanded it and the child row showed the
        // side as TEXT ("Short"). A row read as the collapsed group (row_kind group) is expanded with ONE native touch
        // and Positions is re-read: a side the row now shows in text is read like any grid cell (side_source row,
        // MEASURED), with no sheet. Expanded stays expanded, so later reads find the child row without touching.
        val grp = (0 until rows.length()).mapNotNull { rows.optJSONObject(it) }.filter { it.optString("row_kind") == "group" && (it.isNull("side") || it.optString("side").isEmpty()) }
        val pre = JSONArray()
        // which kind of row the read saw (1b.233 10:40Z: the expand step left no line, so whether it ran is unmeasured)
        pre.put("kinds=" + (0 until rows.length()).joinToString(",") { rows.optJSONObject(it)?.optString("row_kind", "-")?.ifEmpty { "-" } ?: "?" }.take(30))
        val mg = dismissModal(p)
        if (mg.isNotEmpty()) pre.put("modal_pre:$mg".take(40))
        if (grp.size == 1 && rows.length() == 1 && !mg.endsWith("still_open")) {
            val x = nativeTap(p, syms[0], "group")
            delay(1200); p.ensure()
            if (x.startsWith("touched") && (p.obj("__ex.modalsOpen()")?.optInt("n") ?: 0) > 0) {
                // the expand touch opened an edit modal: record it, dismiss (never Confirm), and stop this read here
                pre.put("modal_after:group/expand").put("modal_dismiss:${dismissModal(p)}".take(40))
                sheetMiss("sheet_modal_after_tap", JSONObject().put("tries", pre), force = true)
                return pos
            }
            if (p.obj("__ex.sheetInfo(${q(syms[0])})")?.optBoolean("open") != true) {
                val again = p.obj("__ex.positionsRead()")
                val ar = again?.optJSONArray("rows")
                if (again?.optString("state") == "read" && ar != null && ar.length() == 1 && ar.optJSONObject(0)?.optString("symbol") == syms[0]) {
                    val r0 = JSONObject(ar.getJSONObject(0).toString()).put("side_source", "row")
                    return JSONObject(again.toString()).put("rows", JSONArray().put(r0)).put("provenance", "MEASURED")
                }
                // still no side: the expanded row's shape (cell count, nesting, whether childRows picked it) rides on the
                // sheet diag below, so the two measurements never overwrite each other
                pre.put("group/expand:$x".take(40)).put("child_read=${again?.optString("state") ?: "unanswered"}".take(40))
                p.obj("__ex.childShape()")?.let { cs ->
                    pre.put("hdr=${cs.optInt("hdr")} grp=${cs.optInt("groups")} child=${cs.optInt("child_rows")}")
                    cs.optJSONArray("sides")?.let { a -> for (j in 0 until a.length()) a.optJSONObject(j)?.let { o ->
                        pre.put("${o.optString("side")} c=${o.optInt("cells")} d=${o.optInt("depth")}/${o.optInt("row_depth")} ${o.optString("role")} pk=${o.optBoolean("picked")} ts=${o.optInt("tpsl")}".take(40)) } }
                }
            } else pre.put("group/expand:$x opened_sheet".take(40))
            lastTapGeom.forEach { pre.put(it.take(40)) }
        } else p.obj("__ex.childShape()")?.let { cs ->
            // no expand (the row was not read as the collapsed group): the panel's shape, so the next build knows why
            pre.put("hdr=${cs.optInt("hdr")} grp=${cs.optInt("groups")} child=${cs.optInt("child_rows")} sides=${cs.optJSONArray("sides")?.length() ?: 0}")
        }
        val out = JSONArray()
        for (i in 0 until rows.length()) {
            val r = JSONObject(rows.getJSONObject(i).toString())
            if (!r.isNull("side") && r.optString("side").isNotEmpty()) { out.put(r); continue }
            val d = sheetRead(p, syms[i])
            if (!d.optBoolean("ok")) {
                if (pre.length() > 0) { val tr = JSONArray(); for (j in 0 until pre.length()) tr.put(pre.get(j))
                    d.optJSONArray("tries")?.let { a -> for (j in 0 until a.length()) tr.put(a.get(j)) }; d.put("tries", tr) }
                sheetMiss("sheet_${d.optString("why")}", d, force = d.optString("why") == "modal_after_tap"); return pos
            }
            d.put("close", sheetClose(p, syms[i]))
            r.put("side", d.getString("side")).put("side_source", "sheet").put("qty", d.getDouble("qty")).put("qty_src", "sheet")
                .put("entry", d.getDouble("entry")).put("upnl_signed", d.optBoolean("upnl_signed"))
            if (!d.isNull("mark")) r.put("mark", d.optDouble("mark"))
            if (d.optBoolean("upnl_signed") && !d.isNull("upnl")) r.put("upnl", d.optDouble("upnl")) else r.put("upnl", JSONObject.NULL)
            out.put(r)
            if (!sheetOkPosted) { sheetOkPosted = true; sheetMiss("sheet_ok", d, force = true) }
        }
        return JSONObject(pos.toString()).put("state", "read").put("rows", out).put("provenance", "MEASURED")
    }

    /** The trail amend on Breakout's position SHEET (operator-measured flow, 2026-10-14): the Positions grid has no
     *  edit control and no side text, so the stop is edited where the venue puts it. Every step is asserted before the
     *  next: the sheet's side + total volume == the ticket's; the [1 TP / 1 SL] tab holds ONE Stop row whose trigger is
     *  the stop the VM believes is resting (else human_moved); the new stop is tighter AND clear of the venue's current
     *  price; then -- only when SHEET_EDIT_MEASURED -- the Stop row's ONE pencil, the "Edit Stop loss" modal (header side
     *  + qty asserted), ONLY the Trigger typed and read back (quantity unchanged), Confirm, and the Stop row re-read. */
    private suspend fun executeAmendSheet(t: JSONObject, live: Boolean, venue: String, long: Boolean, pStep: Double,
                                          fromSl: Double, sl: Double, tpChange: Boolean) {
        val aid = t.getString("amend_id")
        val want = if (long) "long" else "short"
        if (tpChange) return amendRefuse(t, "the sheet path edits the stop only; a TP revision is not built", null)
        // the sheet needs an on-screen view (PHONE-FGS-EXEC routes amends to the front); never attempted on the engine page
        if (cur !== fg) return amendRefuse(t, "sheet path runs on the foreground view only (engine page: not hit-testable)", null)
        if (if (long) sl <= fromSl + pStep / 2 else sl >= fromSl - pStep / 2) return amendRefuse(t, "sheet path: the new stop is not tighter", null)
        // 1-2. the sheet and its [Position details], MEASURED
        val d = sheetRead(cur, venue)
        if (!d.optBoolean("ok")) {
            val tries = d.optJSONArray("tries")?.let { a -> (0 until a.length()).map { a.optString(it) } } ?: emptyList()
            if (d.optString("why") == "sheet_not_open" && tries.all { it.endsWith(":none") || it.endsWith(":no_grid") }) {
                ledger.append(aid, "no_position")
                return amendResult(t, "no_position", "no $venue row in Positions (${tries.joinToString(" ")})", null, null, d)
            }
            sheetMiss("amend_sheet_${d.optString("why")}", d, force = true)
            return amendRefuse(t, "position sheet unread: ${d.optString("why")} ${d.optJSONArray("miss") ?: ""} ${tries.joinToString(" ")}", d)
        }
        suspend fun bail(why: String, dump: JSONObject?) { val c = sheetClose(cur, venue); amendRefuse(t, "$why (sheet close: $c)", dump) }
        val tq = t.optDouble("qty", Double.NaN); val sq = d.optDouble("qty", Double.NaN)
        if (d.optString("side") != want) return bail("sheet side ${d.optString("side")} != ticket $want", d)
        if (tq.isNaN() || sq.isNaN() || abs(sq - tq) > max(1e-9, QTY_TOL_FRAC * tq)) return bail("sheet volume $sq != ticket qty $tq", d)
        val mark = d.optDouble("mark", Double.NaN)
        if (mark.isNaN()) return bail("sheet current price unread", d)
        val buf = max(2 * pStep, mark * PRICE_BUF_FRAC)
        if (if (long) sl > mark - buf else sl < mark + buf) return bail("new stop ${fmt(sl, pStep)} within ${fmt(buf, pStep)} of the current price ${fmt(mark, pStep)}", d)
        // 3. [1 TP / 1 SL]: the ONE Stop row, its trigger == the resting stop the VM expects
        cur.js("__ex.sheetTab('tpsl')"); delay(800); ensure()
        val so = jsObj("__ex.sheetOrders()") ?: return bail("sheet orders unanswered", d)
        if (so.optInt("n_stop") != 1) return bail("sheet stop rows n=${so.optInt("n_stop")}", so)
        val rs = so.optJSONArray("rows") ?: JSONArray()
        val stop = (0 until rs.length()).map { rs.optJSONObject(it) }.first { it?.optString("type") == "stop" }!!
        val trig = if (stop.isNull("trigger")) null else stop.optDouble("trigger")
        if (!close(trig, fromSl, pStep)) {
            val c = sheetClose(cur, venue)
            return amendHuman(t, "terminal stop $trig != expected ${fmt(fromSl, pStep)} (sheet close: $c)", so)
        }
        // FIRST-RUN GATE (BREAKOUT-SL-AMEND-CLICK): the read path above runs live; the pencil sits next to the ⓧ that
        // CANCELS the stop, and no capture has shown the live icons, so nothing is clicked until their dump is reviewed
        if (!SHEET_EDIT_MEASURED) {
            sheetMiss("amend_sheet_unmeasured", d, so, force = true)
            return bail("sheet read verified (side, volume, stop ${fmt(fromSl, pStep)} MEASURED); the pencil click is gated until its icon dump is reviewed", so)
        }
        // 4. the Stop row's ONE pencil -> the ONE "Edit Stop loss" modal
        if (stop.optInt("pencils") != 1) return bail("stop row pencils n=${stop.optInt("pencils")}", so)
        val e = js("__ex.sheetEditStop()")
        if (e != "clicked") return bail("stop edit control: $e", so)
        delay(1200); ensure()
        suspend fun mbail(why: String, dump: JSONObject?) { val c = js("__ex.modalCancel()"); delay(600); bail("$why (modal cancel: $c)", dump) }
        val m0 = jsObj("__ex.modalInfo()") ?: return mbail("edit modal unanswered", so)
        if (m0.optInt("n") != 1 || m0.optString("kind") != "stop") return mbail("edit modal n=${m0.optInt("n")} kind=${m0.optString("kind")}", m0)
        val mq = m0.optDouble("qty", Double.NaN)
        if (m0.optString("side") != want || mq.isNaN() || abs(mq - sq) > max(1e-9, QTY_TOL_FRAC * sq)) return mbail("modal header ${m0.optString("side")} $mq != $want $sq", m0)
        if (m0.optInt("triggers") != 1) return mbail("modal Trigger inputs n=${m0.optInt("triggers")}", m0)
        val mt = num(jsObj("__ex.modalTrigger()")?.optString("value"))
        if (!close(mt, fromSl, pStep)) { js("__ex.modalCancel()"); delay(600); val c = sheetClose(cur, venue); return amendHuman(t, "modal trigger $mt != expected ${fmt(fromSl, pStep)} (sheet close: $c)", m0) }
        fun qtyField(m: JSONObject?): String {
            val ins = m?.optJSONArray("inputs") ?: return "?"
            return (0 until ins.length()).map { ins.optJSONObject(it) }.filter { Regex("(?i)quantity|\\bqty\\b|size").containsMatchIn(it?.optString("label") ?: "") }
                .map { it!!.optString("value") }.joinToString("|")
        }
        val q0 = qtyField(m0)
        // 5. ONLY the Trigger, typed and read back; the quantity must not move
        val st = jsObj("__ex.modalSetTrigger(${q(fmt(sl, pStep))})")
        delay(400)
        val tr = num(jsObj("__ex.modalTrigger()")?.optString("value"))
        val m1 = jsObj("__ex.modalInfo()")
        if (st?.optInt("n") != 1 || !close(tr, sl, pStep)) return mbail("trigger read-back $tr", m1)
        if (qtyField(m1) != q0) return mbail("modal quantity changed ($q0 -> ${qtyField(m1)})", m1)
        if (!live) {
            val c = js("__ex.modalCancel()"); delay(600); val sc = sheetClose(cur, venue)
            ledger.append(aid, "dry_amended")
            amendResult(t, "dry_amended", "sheet: trigger typed + read back, NOT confirmed (server mode dry; cancel: $c, sheet close: $sc)", tr, null, m1)
            setStatus("DRY amend $aid read back on the sheet; not saved"); return
        }
        ledger.append(aid, "submitted")
        val cf = js("__ex.modalConfirm()")
        if (cf != "clicked") { ledger.append(aid, "refused"); return mbail("modal confirm control: $cf", m1) }
        delay(1500); ensure()
        if ((jsObj("__ex.modalInfo()")?.optInt("n") ?: 0) > 0) {
            js("__ex.modalCancel()"); delay(600); val c = sheetClose(cur, venue); ledger.append(aid, "unconfirmed")
            return amendResult(t, "mismatch", "edit modal still open after Confirm (sheet close: $c)", null, null, jsObj("__ex.modalInfo()"))
        }
        // 6. re-read the Stop row on the sheet
        cur.js("__ex.sheetTab('tpsl')"); delay(1000); ensure()
        val so2 = jsObj("__ex.sheetOrders()")
        val rs2 = so2?.optJSONArray("rows") ?: JSONArray()
        val stops2 = (0 until rs2.length()).map { rs2.optJSONObject(it) }.filter { it?.optString("type") == "stop" }
        val vSl = if (stops2.size == 1 && !stops2[0]!!.isNull("trigger")) stops2[0]!!.optDouble("trigger") else null
        val c = sheetClose(cur, venue)
        if (close(vSl, sl, pStep)) {
            ledger.append(aid, "amended")
            amendResult(t, "amended", "confirmed and read back on the position sheet (sheet close: $c)", vSl, null, so2)
            setStatus("LIVE amend $aid: SL ${fmt(sl, pStep)} read back")
        } else {
            ledger.append(aid, "unconfirmed")
            amendResult(t, "mismatch", "after Confirm the sheet shows stop $vSl (asked ${fmt(sl, pStep)}; stops n=${stops2.size}; sheet close: $c)", vSl, null, so2)
            setStatus("amend $aid NOT VERIFIED — operator check")
        }
    }

    private suspend fun dlgField(f: Field): JSONObject? = jsObj("__ex.dlgRead(${q(f.re)}, ${q(f.prefer)})")

    private suspend fun dlgType(f: Field, text: String, want: Double, step: Double): Boolean {
        val res = jsObj("__ex.dlgSet(${q(f.re)}, ${q(f.prefer)}, ${q(text)})") ?: return false
        if (res.optInt("n", 0) != 1) return false
        delay(300)
        if (close(num(dlgField(f)?.optString("value")), want, step)) return true
        if (js("__ex.dlgFocus(${q(f.re)}, ${q(f.prefer)})") != "focused") return false
        typeKeys(text); delay(400)
        return close(num(dlgField(f)?.optString("value")), want, step)
    }

    private fun firstNum(s: String?): Double? = s?.let { Regex("-?[0-9][0-9,]*(\\.[0-9]+)?").find(it)?.value }?.let { num(it) }

    private suspend fun amendRefuse(t: JSONObject, why: String, dump: JSONObject?) {
        ledger.append(t.optString("amend_id"), "refused")
        amendResult(t, "refused", why, null, null, dump)
        setStatus("amend ${t.optString("amend_id")} REFUSED: $why")
    }

    private suspend fun amendCancelRefuse(t: JSONObject, why: String, dump: JSONObject?) {
        val c = try { js("__ex.dlgCancel()") } catch (e: JsTimeout) { "timeout" }
        amendRefuse(t, "$why (dialog cancel: $c)", dump)
    }

    private suspend fun amendHuman(t: JSONObject, why: String, dump: JSONObject?) {
        ledger.append(t.optString("amend_id"), "human_moved")
        amendResult(t, "human_moved", why, null, null, dump)
        setStatus("amend ${t.optString("amend_id")}: terminal differs from the VM ($why); not touched")
    }

    /** The amend's one report. The VM re-checks an "amended" claim against what it asked for and pings any lock. */
    private suspend fun amendResult(t: JSONObject, result: String, why: String, slRead: Double?, tpRead: Double?, dump: JSONObject?) {
        val b = JSONObject().put("kind", "amend_result").put("ticket_id", t.optString("ticket_id"))
            .put("amend_id", t.optString("amend_id")).put("result", result).put("reason", why).put("form", dump ?: JSONObject())
        if (slRead != null) b.put("sl_read", slRead)
        if (tpRead != null) b.put("tp_read", tpRead)
        api.post("report", b)
    }

    /** What the page SHOWS for the server read-back: raw strings, never our own computed numbers. Side and order type
     *  are the SELECTED tabs read off the ticket; "" when none or several are selected (the server refuses ""). */
    private suspend fun readBack(id: String, tk: JSONObject, f: List<Field>, unitSrc: String): JSONObject {
        val tabs = tk.optJSONArray("tabs") ?: JSONArray()
        fun selected(re: Regex): String {
            val hit = (0 until tabs.length()).map { tabs.optJSONObject(it) }.filter { it != null && it.optBoolean("selected") && re.matches(it.optString("text").trim()) }
            return if (hit.size == 1) hit[0]!!.optString("text").trim() else ""
        }
        val sub = tk.optJSONObject("submit")
        val vals = f.map { (readField(it)?.optString("value") ?: "").trim() }
        val tpsl = if (tk.isNull("tpsl")) "" else tk.optBoolean("tpsl").toString()
        return JSONObject().put("ticket_id", id).put("symbol", tk.optString("symbol").trim())
            .put("side", selected(Regex("(?i)buy|sell"))).put("order_type", selected(Regex("(?i)market|limit|trigger|stop")))
            .put("price", vals[0]).put("qty", vals[1]).put("qty_unit", unitSrc.replace("\n", " ").trim()).put("tp", vals[2]).put("sl", vals[3])
            .put("submit_label", (sub?.optString("text") ?: "").trim())
            .put("submit_disabled", if (sub == null) "" else sub.optBoolean("disabled").toString()).put("tpsl", tpsl)
    }

    private fun readBackLines(rb: JSONObject) = READBACK_FIELDS.joinToString("\n") { "$it=" + rb.optString(it).trim() }

    private fun sha256(s: String) = java.security.MessageDigest.getInstance("SHA-256").digest(s.toByteArray(Charsets.UTF_8)).joinToString("") { "%02x".format(it) }

    /** A ticket field named by its LABEL (regex), with an optional narrowing regex when several labels match. */
    private class Field(val name: String, val re: String, val prefer: String = "")

    private suspend fun readField(f: Field): JSONObject? = jsObj("__ex.readByLabel(${q(f.re)}, ${q(f.prefer)})")

    private fun close(got: Double?, want: Double, step: Double) = got != null && abs(got - want) <= step / 2 + 1e-9

    /** Drive the page to the venue symbol: one page-side move per step, read back between moves, at most 8 moves
     *  (the market chip takes up to 4: open, [type in its search], pick, [fail -> close]; the watchlist then needs 1-2).
     *  Returns the route taken (for the refusal reason / the dump); "already" when the ticket named the asset. */
    private suspend fun selectSymbol(venue: String, base: String): String {
        var route = ""
        var prev = ""
        for (step in 0 until 8) {
            if (js("__ex.symbolOnTicket()").uppercase().contains(base)) return route.ifEmpty { "already" }
            val r = js("__ex.symbolStep(${q(venue)})")
            route += (if (route.isEmpty()) "" else ">") + r
            if (r == "done" || r == "none" || r == "ambiguous" || r == "no_result" || r == "search_not_set" || r == "bad_host" || r == "not_a_symbol") return route
            if (r == prev && (r == "clicked_symbol" || r == "clicked_label" || r == "clicked_watch" || r == "opened_picker" ||
                    r == "opened_market" || r == "picked_market" || r == "typed_market_search")) return "$route>stuck"
            prev = r
            delay(1500); ensure()
        }
        return route
    }

    /** Native value setter first; if the page does not keep it, real key events into the focused field. Both the
     *  set and the read-back locate the field by label at that moment. */
    private suspend fun typeInto(f: Field, text: String, want: Double, step: Double): Boolean {
        val res = jsObj("__ex.setByLabel(${q(f.re)}, ${q(f.prefer)}, ${q(text)})") ?: return false
        if (res.optInt("n", 0) != 1) return false
        delay(300)
        if (close(num(readField(f)?.optString("value")), want, step)) return true
        if (js("__ex.focusByLabel(${q(f.re)}, ${q(f.prefer)})") != "focused") return false
        typeKeys(text); delay(400)
        return close(num(readField(f)?.optString("value")), want, step)
    }

    private suspend fun refuse(id: String, why: String, dump: JSONObject?) {
        ledger.append(id, "refused")
        report(id, "refused", why, dump)
        api.event("refusal", engTag() + why, id)
        try { js("(function(){var t=__ex.ticket();t.inputs.forEach(function(i){if(/quantity/i.test(i.label))__ex.setInput(i.k,'')});return 'ok'})()") } catch (_: Exception) {}
        setStatus("ticket $id REFUSED: $why")
    }

    /** "(bg:overlay) " while a background ticket runs, else "": which page did the work rides the report and the event
     *  (PHONE-FGS-EXEC; parentheses survive the server's reason scrub, brackets do not). */
    private fun engTag(): String = if (cur === fg) "" else "(bg:${BgWeb.engine}) "

    private suspend fun report(id: String, result: String, why: String, dump: JSONObject?) {
        api.post("report", JSONObject().put("kind", "ticket_result").put("ticket_id", id).put("result", result)
            .put("reason", engTag() + why).put("form", dump ?: JSONObject()))
    }
}
