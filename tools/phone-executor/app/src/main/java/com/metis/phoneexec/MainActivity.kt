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
    private var lastState = ""
    private var reloginFailures = 0
    private var execSrc = ""

    companion object {
        const val TRADE_URL = "https://trade.breakoutprop.com/"
        const val TICK_MS = 30_000L
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(b: Bundle?) {
        super.onCreate(b)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON or WindowManager.LayoutParams.FLAG_SECURE)
        api = Api(this); ledger = Ledger(this)
        execSrc = assets.open("exec.js").bufferedReader().readText()

        web = WebView(this)
        web.settings.javaScriptEnabled = true
        web.settings.domStorageEnabled = true
        CookieManager.getInstance().setAcceptCookie(true)
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, true)
        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(v: WebView, r: android.webkit.WebResourceRequest): Boolean =
                r.url.scheme != "https"  // never leave https; everything else stays in THIS WebView
        }
        status = TextView(this).apply { setPadding(16, 8, 16, 8); setBackgroundColor(Color.parseColor("#202833")); setTextColor(Color.WHITE); textSize = 12f }
        armBtn = Button(this)
        val bar = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            fun btn(label: String, f: () -> Unit) = addView(Button(this@MainActivity).apply { text = label; isAllCaps = false; setOnClickListener { f() } })
            addView(armBtn.apply { isAllCaps = false; setOnClickListener { toggleArm() } })
            btn("Setup") { setupDialog() }
            btn("Dry test") { dryTest() }
            btn("Share ID") { shareFingerprint() }
            btn("Reload") { web.loadUrl(TRADE_URL) }
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

    private fun setStatus(s: String) { status.text = s }
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
        try {
            ensure()
            val s = jsObj("__ex.state()") ?: run { setStatus("page not readable"); return }
            val st = when {
                s.optBoolean("challenged") -> "challenged"
                s.optBoolean("loggedIn") -> "logged_in"
                s.optBoolean("pw") || s.optBoolean("email") || s.optBoolean("codeWait") -> "login"
                else -> "other"
            }
            if (st != lastState) {
                if (st == "login" && lastState == "logged_in") api.event("logout_seen", "")
                if (st == "challenged") api.event("error", "challenge page shown; executor stopped (no solving, no retry loop)")
                lastState = st
            }
            when (st) {
                "challenged" -> setStatus("STOPPED: challenge page. Operator: open the app and check.")
                "logged_in" -> { reloginFailures = 0; claimAndRun() }
                "login" -> relogin(s)
                else -> { setStatus("not on the terminal (${s.optString("host")}); reloading"); web.loadUrl(TRADE_URL) }
            }
        } finally { busy = false }
    }

    // ---------------- auto re-login ----------------
    private suspend fun relogin(s: JSONObject) {
        if (Store.flag(this, Store.RELOGIN_LATCHED)) { setStatus("logged out; auto re-login STOPPED after 2 failures (tap Reset login after logging in by hand)"); return }
        val email = Store.get(this, Store.LOGIN_EMAIL)
        val user = Store.get(this, Store.INBOX_USER)
        val pass = Store.get(this, Store.INBOX_PASS)
        if (email == null || user == null || pass == null) { setStatus("logged out; auto re-login not configured (Setup)"); return }
        setStatus("logged out: starting email login")
        api.event("login_started", "")
        val since = Date(System.currentTimeMillis() - 60_000)
        if (!s.optBoolean("codeWait")) {
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
        web.loadUrl(TRADE_URL); delay(10_000); ensure()
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
        web.loadUrl(TRADE_URL)
    }

    // ---------------- tickets ----------------
    private suspend fun claimAndRun() {
        val r = api.post("claim") ?: run { setStatus("logged in · VM unreachable (no claim, no click)"); return }
        if (!r.optBoolean("ok")) { setStatus("logged in · claim refused (http ${r.optInt("http")})"); return }
        val t = r.optJSONObject("ticket")
        if (t == null) { setStatus("logged in · ${if (armed()) "ARMED" else "dry"} · no ticket · ${java.text.DateFormat.getTimeInstance().format(Date())}"); return }
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

        // 1. ticket open, symbol
        if (js("__ex.openTicket()") == "no_order_control") return refuse(id, "order control not found", null)
        delay(1500); ensure()
        js("__ex.selectSymbol(${q(venue)})"); delay(2000); ensure()
        if (js("__ex.openTicket()") == "clicked") delay(1500)
        var tk = jsObj("__ex.ticket()") ?: return refuse(id, "ticket unreadable", null)
        if (!tk.optBoolean("open")) return refuse(id, "ticket not open", tk)
        val symText = (tk.optJSONArray("heading")?.toString() ?: "") + tk.optJSONObject("submit")?.optString("text") + tk.optJSONArray("alerts")
        if (!symText.uppercase().contains(base)) return refuse(id, "symbol $base not verified on the ticket", tk)
        // 2. type + side
        js("__ex.tab('Limit')"); delay(500); js("__ex.tab(${q(sideTab)})"); delay(700)
        if (js("__ex.tabSelected('Limit')") != "true" || js("__ex.tabSelected(${q(sideTab)})") != "true") return refuse(id, "Limit/$sideTab tab not selected", jsObj("__ex.ticket()"))
        // 3. price + quantity
        tk = jsObj("__ex.ticket()") ?: return refuse(id, "ticket unreadable", null)
        val pIdx = idx(tk, Regex("limit price", RegexOption.IGNORE_CASE)) ?: return refuse(id, "Limit price field not unique", tk)
        val qIdx = idx(tk, Regex("quantity", RegexOption.IGNORE_CASE)) ?: return refuse(id, "Quantity field not unique", tk)
        if (!typeInto(pIdx, fmt(entry, pStep), entry, pStep)) return refuse(id, "limit price did not read back", jsObj("__ex.ticket()"))
        if (!typeInto(qIdx, fmt(qty, qStep), qty, qStep)) return refuse(id, "quantity did not read back", jsObj("__ex.ticket()"))
        // quantity unit: the "Quantity ... available" alert must name the base asset, not USD
        tk = jsObj("__ex.ticket()") ?: return refuse(id, "ticket unreadable", null)
        val alerts = (tk.optJSONArray("alerts")?.toString() ?: "").uppercase()
        if (!alerts.contains(base)) return refuse(id, "quantity unit not verified as $base", tk)
        // 4. TP/SL
        if (js("__ex.setTpsl(true)") != "ok") return refuse(id, "TP/SL box not ticked", tk)
        delay(900); ensure()
        tk = jsObj("__ex.ticket()") ?: return refuse(id, "ticket unreadable", null)
        val tpIdx = idx(tk, Regex("take ?profit|\\btp\\b", RegexOption.IGNORE_CASE), prefer = Regex("price", RegexOption.IGNORE_CASE))
            ?: return refuse(id, "TP price field not unique (labels in the dump)", tk)
        val slIdx = idx(tk, Regex("stop ?loss|\\bsl\\b", RegexOption.IGNORE_CASE), prefer = Regex("price", RegexOption.IGNORE_CASE))
            ?: return refuse(id, "SL price field not unique (labels in the dump)", tk)
        if (tpIdx == slIdx || tpIdx == pIdx || slIdx == pIdx || tpIdx == qIdx || slIdx == qIdx) return refuse(id, "TP/SL fields collide", tk)
        if (!typeInto(tpIdx, fmt(tp, pStep), tp, pStep)) return refuse(id, "TP did not read back", jsObj("__ex.ticket()"))
        if (!typeInto(slIdx, fmt(sl, pStep), sl, pStep)) return refuse(id, "SL did not read back", jsObj("__ex.ticket()"))
        // 5. full read-back
        delay(500)
        tk = jsObj("__ex.ticket()") ?: return refuse(id, "ticket unreadable", null)
        val ins = tk.optJSONArray("inputs") ?: JSONArray()
        fun v(k: Int) = num(ins.optJSONObject(k)?.optString("value"))
        val ok = close(v(pIdx), entry, pStep) && close(v(qIdx), qty, qStep) && close(v(tpIdx), tp, pStep) && close(v(slIdx), sl, pStep)
        val sub = tk.optJSONObject("submit")
        val subText = sub?.optString("text") ?: ""
        if (!ok) return refuse(id, "read-back mismatch", tk)
        if (tk.optBoolean("tpsl") != true) return refuse(id, "TP/SL box not on at read-back", tk)
        if (!sideRe.containsMatchIn(subText) || oppRe.containsMatchIn(subText)) return refuse(id, "submit label does not match the side", tk)
        if (sub?.optBoolean("disabled") != false) return refuse(id, "submit disabled after fill", tk)

        if (!live) {
            ledger.append(id, "dry_filled")
            report(id, "dry_filled", if (test) "test ticket (always dry)" else if (!armed()) "app not armed" else "server mode dry", tk)
            api.event("dry_fill_ok", "filled + read back, NOT submitted: $sideTab ${fmt(qty, qStep)} $venue lim ${fmt(entry, pStep)} tp ${fmt(tp, pStep)} sl ${fmt(sl, pStep)}", id)
            js("__ex.setInput($qIdx, '')")
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

    private fun idx(tk: JSONObject, re: Regex, prefer: Regex? = null): Int? {
        val ins = tk.optJSONArray("inputs") ?: return null
        val hits = (0 until ins.length()).map { ins.getJSONObject(it) }.filter { re.containsMatchIn(it.optString("label")) }
        val pick = if (hits.size > 1 && prefer != null) hits.filter { prefer.containsMatchIn(it.optString("label")) } else hits
        return if (pick.size == 1) pick[0].optInt("k") else null
    }

    private fun close(got: Double?, want: Double, step: Double) = got != null && abs(got - want) <= step / 2 + 1e-9

    /** Native value setter first; if the page does not keep it, real key events into the focused field. */
    private suspend fun typeInto(k: Int, text: String, want: Double, step: Double): Boolean {
        js("__ex.setInput($k, ${q(text)})"); delay(300)
        var back = num((jsObj("__ex.ticket()")?.optJSONArray("inputs")?.optJSONObject(k))?.optString("value"))
        if (close(back, want, step)) return true
        if (js("__ex.focusInput($k)") != "focused") return false
        typeKeys(text); delay(400)
        back = num((jsObj("__ex.ticket()")?.optJSONArray("inputs")?.optJSONObject(k))?.optString("value"))
        return close(back, want, step)
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
