package com.metis.phoneexec

import android.annotation.SuppressLint
import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.graphics.Color
import android.net.Uri
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
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import org.json.JSONArray
import org.json.JSONObject
import java.util.Date
import kotlin.coroutines.resume
import kotlin.math.abs
import kotlin.math.floor
import kotlin.math.round

/**
 * Phase-1b executor (design docs/integrations/breakout-phone-executor-DESIGN.md § 3, § 7.9).
 *
 * A screen-on kiosk WebView on trade.breakoutprop.com, with the stock WebView identity (no UA change, no
 * stealth, no challenge solving: a challenge STOPS the loop). Every 30 s it:
 *   - logged out  -> auto re-login: type the account email, read the number the page shows, find the ONE link
 *                    with that number in the newest Breakout mail in the dedicated inbox, open it in THIS WebView.
 *                    Two failures latch it off and ping the operator.
 *   - logged in   -> claim the next ticket from the VM (atomic, one attempt per ticket), fill it, read EVERY field
 *                    back (the submit label must carry the side), then:
 *                      DRY  (default; any test ticket; server says dry; or the app is not ARMED): do NOT submit,
 *                           report the read-back and clear the quantity.
 *                      LIVE (server says live AND the app is ARMED): submit, read Open orders / Positions back,
 *                           report the placement to the VM ledger; an opposite-side position is flattened.
 * Any unread, ambiguous or mismatched value is a refusal (fail closed), reported with the ticket dump.
 */
class MainActivity : Activity() {
    private lateinit var web: WebView
    private lateinit var status: TextView
    private lateinit var armBtn: Button
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private lateinit var api: Api
    private lateinit var ledger: Ledger
    private var busy = false
    private var lastHbMs = 0L
    private var lastStatus = ""
    // HOTFIX 2026-10-05 (manual login was being reset by the loop): any touch/key on the screen holds every
    // automatic navigation (reload, re-login) for USER_HOLD_MS; "Pause" holds everything until the page reads
    // logged in, or PAUSE_MAX_MS passes.
    private var lastUserInputMs = 0L
    private var pausedUntilMs = 0L
    private lateinit var pauseBtn: Button
    private var lastState = ""
    private var reloginFailures = 0
    private var execSrc = ""

    companion object {
        const val TRADE_URL = "https://trade.breakoutprop.com/"
        // The login entry that WORKED in the 1a probe (11:44-11:47Z): portal code step -> app.breakoutprop.com
        // (Cloudflare check, then served) -> trade host SSO -> logged-in landing. The trade host's own password
        // form is not the way in.
        const val APP_URL = "https://app.breakoutprop.com/"
        const val TICK_MS = 30_000L
        const val HEARTBEAT_MS = 120_000L
        const val USER_HOLD_MS = 5 * 60_000L
        const val PAUSE_MAX_MS = 10 * 60_000L
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(b: Bundle?) {
        super.onCreate(b)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON or WindowManager.LayoutParams.FLAG_SECURE)
        api = Api(this); ledger = Ledger(this)
        execSrc = assets.open("exec.js").bufferedReader().readText()

        web = WebView(this)
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
        web.webViewClient = WebViewClient()
        status = TextView(this).apply { setPadding(16, 8, 16, 8); setBackgroundColor(Color.parseColor("#202833")); setTextColor(Color.WHITE); textSize = 12f }
        armBtn = Button(this)
        val bar = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            fun btn(label: String, f: () -> Unit) = addView(Button(this@MainActivity).apply { text = label; isAllCaps = false; setOnClickListener { f() } })
            addView(armBtn.apply { isAllCaps = false; setOnClickListener { toggleArm() } })
            btn("Setup") { setupDialog() }
            btn("Dry test") { dryTest() }
            btn("Share ID") { shareFingerprint() }
            addView(Button(this@MainActivity).apply { pauseBtn = this; isAllCaps = false; text = "Pause"; setOnClickListener { togglePause() } })
            btn("Login") { lastUserInputMs = System.currentTimeMillis(); setStatus("login: opening app.breakoutprop.com (log in there, then tap Reload)"); web.loadUrl(APP_URL) }
            btn("Reload") { web.loadUrl(home()) }
            btn("Reset login") { Store.setFlag(this@MainActivity, Store.RELOGIN_LATCHED, false); reloginFailures = 0; setStatus("auto re-login re-enabled") }
        }
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(HorizontalScrollView(this@MainActivity).apply { addView(bar) })
            addView(status)
            addView(web, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        }
        setContentView(root)
        renderArm()
        KeepAliveService.start(this, launch = false)
        web.loadUrl(TRADE_URL)

        scope.launch {
            val stuck = ledger.unresolved()
            if (stuck.isNotEmpty()) api.event("error", "restart found unresolved tickets; not retried; check the terminal", stuck.first())
            api.event("app_started", "")
            while (true) { delay(TICK_MS); tick() }
        }
    }

    override fun onDestroy() { scope.cancel(); super.onDestroy() }

    private fun setStatus(s: String) { status.text = s; lastStatus = s }
    /** The touch-hold protects a HUMAN LOGIN only (operator 2026-10-06 09:06Z): once logged in it never blocks claim,
     *  navigation or execution. The Pause button is the only manual stop. */
    private fun userActive() = lastState != "logged_in" && System.currentTimeMillis() - lastUserInputMs < USER_HOLD_MS
    private fun paused() = System.currentTimeMillis() < pausedUntilMs
    private fun togglePause() {
        pausedUntilMs = if (paused()) 0L else System.currentTimeMillis() + PAUSE_MAX_MS
        pauseBtn.text = if (paused()) "PAUSED (tap to resume)" else "Pause"
        setStatus(if (paused()) "paused: no reload, no re-login, no ticket until logged in or 10 min" else "resumed")
    }
    private fun armed() = Store.flag(this, Store.ARMED)
    private fun renderArm() { armBtn.text = if (armed()) "ARMED (live)" else "Dry (not armed)"; armBtn.setTextColor(if (armed()) Color.RED else Color.DKGRAY) }

    private fun toggleArm() {
        if (armed()) { Store.setFlag(this, Store.ARMED, false); renderArm(); return }
        AlertDialog.Builder(this).setTitle("Arm live submits?")
            .setMessage("Live orders are submitted only when the server ALSO says live (the account is live). Test tickets are never submitted.")
            .setPositiveButton("Arm") { _, _ -> Store.setFlag(this, Store.ARMED, true); renderArm() }
            .setNegativeButton("Cancel", null).show()
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
                Store.put(this, Store.INBOX_USER, user.text.toString().trim())
                if (pass.text.isNotEmpty()) Store.put(this, Store.INBOX_PASS, pass.text.toString().replace(" ", ""))
                val pm = getSystemService(PowerManager::class.java)
                setStatus("saved. overlay=${Settings.canDrawOverlays(this)} battery_exempt=${pm.isIgnoringBatteryOptimizations(packageName)}")
            }.setNegativeButton("Cancel", null).create()
        dlg.window?.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        dlg.show()
    }

    private fun dryTest() {
        scope.launch {
            val r = api.post("test-ticket", JSONObject().put("symbol", "ETHUSDT"))
            setStatus("test ticket: " + (r?.optString("ticket_id") ?: "VM unreachable") + (r?.optInt("http", 0)?.takeIf { it > 0 }?.let { " http $it" } ?: ""))
            tick()
        }
    }

    // ---------------- page bridge ----------------
    private fun unq(raw: String?): String = try { JSONArray("[" + raw + "]").get(0).toString() } catch (e: Exception) { raw ?: "" }
    private suspend fun js(code: String): String = suspendCancellableCoroutine { c -> web.evaluateJavascript(code) { c.resume(unq(it)) } }
    private suspend fun ensure() { if (js("typeof window.__ex") != "object") { js(execSrc + ";'ok'"); delay(100) } }
    private suspend fun jsObj(code: String): JSONObject? = try { JSONObject(js("JSON.stringify($code)")) } catch (e: Exception) { null }
    private fun q(s: String) = JSONObject.quote(s)
    private fun typeKeys(s: String) {
        val evs = KeyCharacterMap.load(KeyCharacterMap.VIRTUAL_KEYBOARD).getEvents(s.toCharArray()) ?: return
        for (e in evs) web.dispatchKeyEvent(e)
    }

    private suspend fun tick() {
        if (busy) return
        busy = true
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
            if (paused()) {
                if (st == "logged_in") { pausedUntilMs = 0L; pauseBtn.text = "Pause"; setStatus("logged in: pause lifted") }
                else { setStatus("paused (${(pausedUntilMs - System.currentTimeMillis()) / 60_000 + 1} min left): no automatic action"); return }
            } else if (pauseBtn.text.toString() != "Pause") pauseBtn.text = "Pause"
            if (st != lastState) {
                // Any arrival at the login page pings at once (also right after a restart), so one human tap can
                // re-log in while auto re-login is unconfigured or latched. No claim/fill/submit happens while logged out.
                if (st == "login") api.event("logout_seen", if (Store.get(this, Store.INBOX_PASS) == null || Store.flag(this, Store.RELOGIN_LATCHED)) "LOGGED OUT: open the app and log in (no orders until then)" else "logged out; trying auto re-login (no orders until logged in)")
                if (st == "challenged") api.event("error", "challenge page shown; executor stopped (no solving, no retry loop)")
                lastState = st
            }
            when (st) {
                "challenged" -> setStatus("STOPPED: challenge page. Operator: open the app and check.")
                "logged_in" -> { reloginFailures = 0; onLoggedIn(s) }
                "login" -> relogin(s)
                else -> if (userActive()) setStatus("not on the terminal; you are using the screen, so no reload")
                        else { setStatus("not on the terminal (${s.optString("host")}); reloading"); web.loadUrl(home()) }
            }
        } finally {
            try { heartbeat(st, s) } catch (e: Exception) { }
            busy = false
        }
    }

    /** HEARTBEAT (2026-10-06 08:15Z: an hour with no claim and no event, because every no-claim branch but one only
     *  set the on-screen status). On EVERY tick path, at most every 2 min: the status line, the page state, the
     *  pause / touch-hold flags and the terminal gate. Our own state and UI labels only; never links or values. */
    private suspend fun heartbeat(st: String, s: JSONObject?) {
        val now = System.currentTimeMillis()
        if (now - lastHbMs < HEARTBEAT_MS) return
        lastHbMs = now
        val state = JSONObject().put("st", st).put("paused", paused()).put("hold", userActive()).put("armed", armed())
            .put("host", s?.optString("host") ?: "").put("onAccount", s?.optBoolean("onAccount") ?: false)
            .put("path_depth", s?.optInt("path_depth") ?: 0)
        jsObj("__ex.terminal()")?.let { tm ->
            for (k in listOf("ready", "probe", "panels", "orderControl", "ticketOpen", "buySell")) state.put(k, tm.optBoolean(k))
            state.put("tabs", tm.optInt("tabs")).put("inputs", tm.optInt("inputs"))
        }
        api.heartbeat(lastStatus, state)
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
        if (userActive()) { setStatus("logged in, not on the terminal; you are using the screen, so no navigation"); return false }
        val href = js("__ex.terminalHref()")
        if (!href.startsWith("https://trade.breakoutprop.com/")) { setStatus("logged in, not on the terminal and no account path: tap into the account once"); return false }
        setStatus("logged in: opening the account's terminal")
        web.loadUrl(href)
        for (i in 0 until 12) { delay(2500); ensure(); if (terminalReady()) return true }
        setStatus("terminal did not load (no Order/Order form control, Buy-Sell tabs, panels or buy+sell markers): no claim")
        // Self-diagnosing miss (fix 06:17Z): post the page's control texts once per load attempt, never a report.
        val tm = jsObj("__ex.terminal()")
        val ctl = try { JSONArray(js("JSON.stringify(__ex.controls())")) } catch (e: Exception) { JSONArray() }
        api.terminalMiss("tabs=${tm?.optInt("tabs")} inputs=${tm?.optInt("inputs")} probe=${tm?.optBoolean("probe")}", ctl)
        return false
    }

    // ---------------- auto re-login ----------------
    private suspend fun relogin(s: JSONObject) {
        if (userActive()) { setStatus("logged out; you are using the screen, so auto re-login waits 5 min after your last tap"); return }
        if (Store.flag(this, Store.RELOGIN_LATCHED)) { setStatus("logged out; auto re-login STOPPED after 2 failures (tap Reset login after logging in by hand)"); return }
        val email = Store.get(this, Store.LOGIN_EMAIL)
        val user = Store.get(this, Store.INBOX_USER)
        val pass = Store.get(this, Store.INBOX_PASS)
        if (email == null || user == null || pass == null) { setStatus("logged out; auto re-login not configured (Setup)"); return }
        setStatus("logged out: starting email login")
        api.event("login_started", "")
        val since = Date(System.currentTimeMillis() - 60_000)
        var st0 = s
        if (!st0.optBoolean("codeWait") && st0.optString("host").startsWith("trade.")) {
            web.loadUrl(APP_URL); delay(10_000); ensure()
            st0 = jsObj("__ex.state()") ?: return loginFail("app.breakoutprop.com not readable")
            if (st0.optBoolean("challenged")) return loginFail("app.breakoutprop.com shows a challenge; manual login needed")
        }
        if (!st0.optBoolean("codeWait")) {
            val r = js("__ex.loginEmail(${q(email)})")
            if (r != "clicked" && r != "submitted") return loginFail("email step: $r")
            delay(6000); ensure()
        }
        val num = js("__ex.loginNumber()")
        if (!Regex("^\\d{1,3}$").matches(num)) return loginFail("no single number on the waiting page")
        setStatus("waiting for the Breakout email in the dedicated inbox")
        var link: String? = null
        for (i in 0 until 18) { link = Mail.findLink(user, pass, num, since); if (link != null) break; delay(10_000) }
        if (link == null) return loginFail("no Breakout mail with exactly one matching link within 3 min")
        web.loadUrl(link)            // same WebView, same cookie store
        delay(8000)
        web.loadUrl(home()); delay(10_000); ensure()
        val after = jsObj("__ex.state()")
        if (after?.optBoolean("loggedIn") == true) {
            reloginFailures = 0; lastState = "logged_in"; setStatus("re-login OK")
            api.event("login_ok", "auto re-login via dedicated inbox")
        } else loginFail("not logged in after opening the link")
    }

    private suspend fun loginFail(why: String) {
        reloginFailures += 1
        if (reloginFailures >= 2) {
            Store.setFlag(this, Store.RELOGIN_LATCHED, true)
            api.event("login_failed", "STOPPED after 2 failures; manual login needed: $why")
        } else api.event("login_failed", why)
        setStatus("login failed ($reloginFailures): $why")
        if (!userActive()) web.loadUrl(home())
    }

    // ---------------- tickets ----------------
    private suspend fun claimAndRun() {
        val r = api.post("claim") ?: run { setStatus("logged in · VM unreachable (no claim, no click)"); return }
        if (!r.optBoolean("ok")) { setStatus("logged in · claim refused (http ${r.optInt("http")})"); return }
        val t = r.optJSONObject("ticket")
        if (t == null) { setStatus("logged in · ${if (armed()) "ARMED" else "dry"} · waiting for a ticket (none queued) · ${java.text.DateFormat.getTimeInstance().format(Date())}"); return }
        execute(t, r.optJSONObject("config") ?: JSONObject())
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
        val live = t.optString("submit") == "live" && armed() && !test
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
        var route = selectSymbol(venue, base)
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
        // 2. type + side
        js("__ex.tab('Limit')"); delay(500); js("__ex.tab(${q(sideTab)})"); delay(700)
        if (js("__ex.tabSelected('Limit')") != "true" || js("__ex.tabSelected(${q(sideTab)})") != "true") return refuse(id, "Limit/$sideTab tab not selected", jsObj("__ex.ticket()"))
        if (!js("__ex.symbolOnTicket()").uppercase().contains(base)) return refuse(id, "submit label lost $base after the tabs", jsObj("__ex.ticket()"))
        // 3. price + quantity, BY LABEL (never by index alone): each field is located by its label at the moment it
        //    is typed and again when it is read back; n != 1 (missing or ambiguous label) refuses with the labels seen.
        val fPrice = Field("Limit price", "limit price"); val fQty = Field("Quantity", "quantity")
        for (f in listOf(fPrice, fQty)) { val n = readField(f)?.optInt("n", 0) ?: 0; if (n != 1) return refuse(id, "${f.name} field not unique (n=$n)", jsObj("__ex.ticket()")) }
        if (!typeInto(fPrice, fmt(entry, pStep), entry, pStep)) return refuse(id, "limit price did not read back", jsObj("__ex.ticket()"))
        if (!typeInto(fQty, fmt(qty, qStep), qty, qStep)) return refuse(id, "quantity did not read back", jsObj("__ex.ticket()"))
        // quantity unit: the "Quantity ... available" alert OR the quantity field's own adornment (unit toggle) must
        // name the base asset, not USD; the refusal carries both texts so the next dump measures the real shape.
        tk = jsObj("__ex.ticket()") ?: return refuse(id, "ticket unreadable", null)
        val alerts = (tk.optJSONArray("alerts")?.toString() ?: "").uppercase()
        val qNear = (readField(fQty)?.optString("near") ?: "").uppercase()
        if (!alerts.contains(base) && !qNear.contains(base)) return refuse(id, "quantity unit not verified as $base (alerts $alerts; near '$qNear')", tk)
        // 4. TP/SL
        if (js("__ex.setTpsl(true)") != "ok") return refuse(id, "TP/SL box not ticked", tk)
        delay(900); ensure()
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

        if (!live) {
            ledger.append(id, "dry_filled")
            report(id, "dry_filled", if (test) "test ticket (always dry)" else if (!armed()) "app not armed" else "server mode dry", tk)
            api.event("dry_fill_ok", "filled + read back, NOT submitted: $sideTab ${fmt(qty, qStep)} $venue lim ${fmt(entry, pStep)} tp ${fmt(tp, pStep)} sl ${fmt(sl, pStep)}", id)
            js("__ex.setByLabel('quantity', '', '')")
            setStatus("DRY ticket $id filled and read back; not submitted")
            return
        }
        // 6. LIVE submit
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
                val opp = view == "^positions$" && rows.split("],[").any { r -> (r.contains(venue.uppercase()) || r.contains(base)) && oppRe.containsMatchIn(r) && !sideRe.containsMatchIn(r) }
                if (opp) {
                    js("__ex.rowClose(${q(venue)}, ${q(oppRe.pattern)})"); delay(1200); js("__ex.dialogClick('confirm|close|yes|ok')")
                    ledger.append(id, "contained")
                    api.event("flattened", "opposite-side position found after submit; close clicked; CHECK the terminal", id)
                    report(id, "mismatch_flattened", "opposite side seen", tk); return
                }
                if (hit) {
                    ledger.append(id, "placed")
                    api.post("report", JSONObject().put("kind", "fill").put("status", "placed").put("ticket_id", id)
                        .put("symbol", sym).put("direction", if (long) "long" else "short")
                        .put("entry", entry).put("sl", sl).put("tp", tp).put("qty", qty).put("source", "phone_executor"))
                    api.event("submitted", "placed $sideTab ${fmt(qty, qStep)} $venue lim ${fmt(entry, pStep)} (seen in ${view.trim('^', '$')})", id)
                    setStatus("LIVE ticket $id placed and seen")
                    return
                }
            }
        }
        ledger.append(id, "unconfirmed")
        api.event("mismatch", "submitted but not found in Open orders/Positions after 3 reads; CHECK the terminal", id)
        setStatus("ticket $id UNCONFIRMED — operator check")
    }

    /** A ticket field named by its LABEL (regex), with an optional narrowing regex when several labels match. */
    private class Field(val name: String, val re: String, val prefer: String = "")

    private suspend fun readField(f: Field): JSONObject? = jsObj("__ex.readByLabel(${q(f.re)}, ${q(f.prefer)})")

    private fun close(got: Double?, want: Double, step: Double) = got != null && abs(got - want) <= step / 2 + 1e-9

    /** Drive the page to the venue symbol: one page-side move per step, read back between moves, at most 6 moves.
     *  Returns the route taken (for the refusal reason / the dump); "already" when the ticket named the asset. */
    private suspend fun selectSymbol(venue: String, base: String): String {
        var route = ""
        var prev = ""
        for (step in 0 until 6) {
            if (js("__ex.symbolOnTicket()").uppercase().contains(base)) return route.ifEmpty { "already" }
            val r = js("__ex.symbolStep(${q(venue)})")
            route += (if (route.isEmpty()) "" else ">") + r
            if (r == "done" || r == "none" || r == "ambiguous" || r == "no_result" || r == "search_not_set" || r == "bad_host" || r == "not_a_symbol") return route
            if (r == prev && (r == "clicked_symbol" || r == "clicked_label" || r == "clicked_watch" || r == "opened_picker")) return "$route>stuck"
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
        api.event("refusal", why, id)
        try { js("(function(){var t=__ex.ticket();t.inputs.forEach(function(i){if(/quantity/i.test(i.label))__ex.setInput(i.k,'')});return 'ok'})()") } catch (_: Exception) {}
        setStatus("ticket $id REFUSED: $why")
    }

    private suspend fun report(id: String, result: String, why: String, dump: JSONObject?) {
        api.post("report", JSONObject().put("kind", "ticket_result").put("ticket_id", id).put("result", result)
            .put("reason", why).put("form", dump ?: JSONObject()))
    }
}
