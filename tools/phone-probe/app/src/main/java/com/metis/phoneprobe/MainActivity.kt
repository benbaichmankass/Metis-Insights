package com.metis.phoneprobe

import android.annotation.SuppressLint
import android.app.Activity
import android.app.AlertDialog
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.text.InputType
import android.view.KeyCharacterMap
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.webkit.CookieManager
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebStorage
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import androidx.webkit.WebMessageCompat
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayInputStream

/**
 * Phase 1a probe. A kiosk WebView with a page-state classifier and a REDACTED shape capture.
 * It places no orders, taps nothing on a Breakout trading page, reads no input value and logs no credential.
 * The user agent is the stock WebView one: no spoofing, no challenge solving, no bypass, no reload loop.
 *
 * "4 Auto-login" (1a.3) tests whether an unattended re-login works: it fills the operator's own saved login into the
 * login form of a breakoutprop.com page, submits, and RECORDS what happened. A CAPTCHA, Turnstile, email code, 2FA or
 * number-match is recorded and the test STOPS there; nothing is solved or bypassed.
 */
class MainActivity : Activity() {
    private val SITE = "https://trade.breakoutprop.com/"
    private val APP = "https://app.breakoutprop.com/"
    private val ui = Handler(Looper.getMainLooper())
    private lateinit var web: WebView
    private lateinit var status: TextView
    private lateinit var rep: Report

    private var featDoc = false
    private var featMsg = false
    private var lastHttp = 0
    private var lastCf = false
    private var loadStartMs = 0L
    private var lastState = ""
    private var fixtureMode = false
    private var fixturesRunning = false
    private var alBusy = false

    // capture in flight
    private var gen = 0
    private var inflightId: String? = null
    private var inflightManual = false
    private var inflightLabel = ""
    private val frames = JSONArray()
    private val fxReplies = JSONArray()
    private var directTop: JSONObject? = null
    private val directSame = JSONArray()
    private val directCross = JSONArray()
    private var navTimer: Runnable? = null

    private val probeSrc: String by lazy { assets.open("probe.js").bufferedReader().readText() }

    private val tick = object : Runnable {
        override fun run() {
            val u = web.url
            if (!fixtureMode && !fixturesRunning && !alBusy && inflightId == null && u != null && host(u).endsWith("breakoutprop.com")) capture("tick", false, "tick")
            ui.postDelayed(this, 30_000)
        }
    }

    @SuppressLint("SetJavaScriptEnabled", "ClickableViewAccessibility")
    override fun onCreate(b: Bundle?) {
        super.onCreate(b)
        window.addFlags(WindowManager.LayoutParams.FLAG_SECURE or WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        rep = Report(this)

        status = TextView(this).apply { textSize = 11f; setPadding(8, 4, 8, 4); text = "ready" }
        web = WebView(this)
        val s = web.settings
        s.javaScriptEnabled = true; s.domStorageEnabled = true
        s.allowFileAccess = false; s.allowContentAccess = false
        s.mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
        // stock user agent: deliberately NOT touched
        CookieManager.getInstance().apply { setAcceptCookie(true); setAcceptThirdPartyCookies(web, true) }

        featDoc = WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT)
        featMsg = WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)
        if (featMsg) WebViewCompat.addWebMessageListener(web, "PhoneProbe", setOf("*")) { _, m, origin, isMain, _ -> onFrameMessage(m, origin, isMain) }
        if (featDoc) WebViewCompat.addDocumentStartJavaScript(web, probeSrc, setOf("*"))

        web.webViewClient = object : WebViewClient() {
            override fun shouldInterceptRequest(v: WebView, r: WebResourceRequest): WebResourceResponse? {
                val u = r.url
                if (u.host == "fixture-a.test" || u.host == "fixture-b.test") {
                    return try {
                        val name = u.lastPathSegment ?: "a.html"
                        WebResourceResponse("text/html", "utf-8", ByteArrayInputStream(assets.open("fixtures/$name").readBytes()))
                    } catch (e: Exception) { WebResourceResponse("text/plain", "utf-8", 404, "nf", mapOf(), ByteArrayInputStream(ByteArray(0))) }
                }
                return null
            }
            override fun onPageStarted(v: WebView, url: String, f: Bitmap?) { if (!url.startsWith("https://fixture")) { lastHttp = 0; lastCf = false; loadStartMs = SystemClock.elapsedRealtime() } }
            override fun onReceivedHttpError(v: WebView, r: WebResourceRequest, e: WebResourceResponse) {
                if (r.isForMainFrame) { lastHttp = e.statusCode; lastCf = e.responseHeaders?.keys?.any { it.equals("cf-mitigated", true) } == true }
            }
            override fun onReceivedError(v: WebView, r: WebResourceRequest, e: android.webkit.WebResourceError) {
                if (r.isForMainFrame) { lastHttp = -1; rep.event("load_error", JSONObject().put("code", e.errorCode)) }
            }
            override fun onPageFinished(v: WebView, url: String) {
                if (fixtureMode || fixturesRunning || alBusy) return
                setStatus("loaded ${host(url)}; capturing…")
                navTimer?.let { ui.removeCallbacks(it) }
                navTimer = Runnable { if (inflightId == null) capture("nav", false, "nav") }
                ui.postDelayed(navTimer!!, 4_000)
            }
        }

        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        fun row(vararg bs: Pair<String, () -> Unit>) = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            bs.forEach { (t, f) -> addView(Button(this@MainActivity).apply { text = t; textSize = 11f; isAllCaps = false; setPadding(4, 0, 4, 0)
                setOnClickListener { f() } }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)) }
        }
        root.addView(row("1 Load site" to { loadSite("load", SITE) }, "2 Capture" to { capture("manual", true, "manual") }, "3 Recheck" to { loadSite("recheck", SITE) }))
        root.addView(row("4 Auto-login" to { autoLogin() }, "Set login" to { showLoginDialog() }, "Load app." to { loadSite("load_app", APP) }))
        root.addView(row("Fixtures" to { runFixtures() }, "Copy" to { export(false) }, "Share" to { export(true) }, "Clear" to { rep.clear(); setStatus("report cleared") }))
        root.addView(status)
        root.addView(web, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        setContentView(root)

        val d = JSONObject().put("sdk", Build.VERSION.SDK_INT).put("release", Build.VERSION.RELEASE).put("model", Build.MODEL).put("brand", Build.MANUFACTURER)
            .put("app_version", "1a.3").put("login_saved", Creds.has(this))
        val wvp = WebView.getCurrentWebViewPackage()
        val ua = web.settings.userAgentString ?: ""
        rep.root.put("device", d).put("webview", JSONObject().put("pkg", wvp?.packageName ?: "?").put("ver", wvp?.versionName ?: "?").put("ua_wv_marker", ua.contains("; wv)")))
            .put("features", JSONObject().put("document_start_script", featDoc).put("web_message_listener", featMsg))
        rep.save()
        ui.postDelayed(tick, 30_000)
        setStatus("1a.3 · doc-start=$featDoc listener=$featMsg · login saved=${Creds.has(this)} · tap 1")
    }

    override fun onResume() { super.onResume(); rep.event("resume") }
    override fun onPause() { super.onPause(); rep.event("pause") }
    @Deprecated("kiosk back") override fun onBackPressed() { if (web.canGoBack()) web.goBack() else super.onBackPressed() }

    private fun host(u: String?): String = try { android.net.Uri.parse(u).host ?: "?" } catch (e: Exception) { "?" }
    private fun setStatus(t: String) { status.text = t }

    private fun net(): JSONObject {
        val cm = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        val c = cm.getNetworkCapabilities(cm.activeNetwork)
        return JSONObject().put("wifi", c?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true)
            .put("cellular", c?.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) == true)
            .put("vpn", c?.hasTransport(NetworkCapabilities.TRANSPORT_VPN) == true)
    }

    private fun loadSite(label: String, url: String) {
        fixtureMode = false
        rep.event("load_requested", JSONObject().put("label", label).put("host", host(url)).put("net", net()))
        web.loadUrl(url)
    }

    // ---- capture pipeline ------------------------------------------------------------------------------------

    private fun unq(raw: String?): String = try { JSONArray("[" + raw + "]").get(0).toString() } catch (e: Exception) { raw ?: "" }
    private fun js(code: String, cb: (String) -> Unit) = web.evaluateJavascript(code) { cb(unq(it)) }

    /** The document-start script normally ran already; if a page was loaded without it, inject it now so Capture works on ANY page. */
    private fun ensureProbe(cb: () -> Unit) {
        js("typeof window.__probeCapture") { t -> if (t == "function") cb() else web.evaluateJavascript(probeSrc) { cb() } }
    }

    private fun capture(id: String, manual: Boolean, label: String) {
        // A manual Capture must never be dropped because a background one is in flight: it takes the slot over.
        if (inflightId != null && !manual) return
        val g = ++gen
        inflightId = id + "-" + System.currentTimeMillis(); inflightManual = manual; inflightLabel = label
        while (frames.length() > 0) frames.remove(0)
        directTop = null
        while (directSame.length() > 0) directSame.remove(0)
        while (directCross.length() > 0) directCross.remove(0)
        val cid = inflightId
        if (manual) setStatus("capturing…")
        ensureProbe {
            // direct, listener-independent read: main frame + same-origin iframes + cross-origin iframe hosts
            js("window.__probeShapeAll ? window.__probeShapeAll() : ''") { r ->
                if (g != gen) return@js
                try {
                    val o = JSONObject(r)
                    directTop = o.optJSONObject("top")
                    o.optJSONArray("same")?.let { for (i in 0 until it.length()) directSame.put(it.get(i)) }
                    o.optJSONArray("cross")?.let { for (i in 0 until it.length()) directCross.put(it.get(i)) }
                } catch (e: Exception) { rep.event("direct_capture_unparsed") }
            }
            // listener path: frames (incl. cross-origin iframes) report themselves
            web.evaluateJavascript("window.__probeCapture('$cid')", null)
        }
        ui.postDelayed({ if (g == gen) finalizeCapture() }, 3_500)
    }

    private fun onFrameMessage(m: WebMessageCompat, origin: android.net.Uri, isMain: Boolean) {
        val s = m.data ?: return
        val o = try { JSONObject(s) } catch (e: Exception) { return }
        o.put("main_frame", isMain)
        if (o.optString("kind") == "fx") { fxReplies.put(o); return }
        if (o.optString("id") == inflightId) frames.put(o)
    }

    private fun classify(top: JSONObject?): String {
        if (top == null) return "no_report"
        val shape = top.optJSONObject("shape") ?: return "no_report"
        val m = shape.optJSONObject("markers") ?: return "no_report"
        val els = shape.optJSONObject("counts")?.optInt("el") ?: 0
        return when {
            lastCf || m.optBoolean("cf") -> "challenged"
            m.optBoolean("blocked") || (lastHttp == 403 && els < 80) -> "blocked"
            m.optBoolean("otc") || (m.optBoolean("otp_like") && !m.optBoolean("pw")) || (m.optBoolean("twofa") && !m.optBoolean("pw")) -> "code_or_2fa"
            m.optBoolean("pw") -> "login"
            (m.optBoolean("buy") && m.optBoolean("sell")) || m.optBoolean("send") -> "terminal"
            else -> "other_served"
        }
    }

    private fun finalizeCapture() {
        if (inflightId == null) return
        // assemble every frame we heard from; the direct read is authoritative for the main frame
        val all = JSONArray()
        var top: JSONObject? = null
        directTop?.let {
            top = JSONObject().put("kind", "shape").put("main_frame", true).put("src", "direct").put("shape", it)
            all.put(top)
            for (i in 0 until directSame.length()) all.put(JSONObject().put("kind", "shape").put("main_frame", false).put("src", "direct_same_origin").put("shape", directSame.get(i)))
        }
        val hosts = HashSet<String>()
        for (i in 0 until directCross.length()) { val h = directCross.getString(i); if (h.isNotEmpty()) hosts.add(h) }
        for (i in 0 until frames.length()) {
            val f = JSONObject(frames.getJSONObject(i).toString()); f.remove("id")
            if (f.optBoolean("main_frame")) { if (top == null) top = f; if (directTop != null) continue }
            else { val h = f.optString("host"); if (h.isNotEmpty() && h != host(web.url)) hosts.add(h) }
            f.put("src", "listener"); all.put(f)
        }
        val state = classify(top)
        val h = host(web.url)
        val summary = JSONObject().put("at", Report.now()).put("label", inflightLabel).put("host", h).put("state", state)
            .put("http", lastHttp).put("cf_mitigated", lastCf).put("net", net())
            .put("ms_since_load_start", SystemClock.elapsedRealtime() - loadStartMs).put("frames", all.length())
            .put("same_origin_frames", directSame.length()).put("iframe_hosts", JSONArray(hosts.toList()))
        top?.optJSONObject("shape")?.let { summary.put("counts", it.optJSONObject("counts")).put("markers", it.optJSONObject("markers")) }
        val changed = state != lastState
        lastState = state
        trackSession(state)
        if (inflightManual) {
            val cap = JSONObject().put("at", Report.now()).put("label", inflightLabel).put("state", state).put("net", net()).put("frames", all)
            rep.append("captures", cap, 6)
            rep.append("probes", summary, 60)
        } else if (changed || inflightLabel == "nav") {
            rep.append("navs", summary, 60)
        }
        setStatus("state=$state host=$h http=$lastHttp frames=${all.length()} same-origin=${directSame.length()} cross-origin-hosts=${hosts.size}")
        inflightId = null
    }

    /** Session lifetime bookkeeping: when the terminal was first seen after a login, and when it was last seen / lost. */
    private fun trackSession(state: String) {
        val s = rep.root.getJSONObject("session")
        val now = Report.now(); val ms = System.currentTimeMillis()
        if (state == "terminal") {
            if (!s.has("first_terminal_at")) { s.put("first_terminal_at", now); s.put("first_terminal_ms", ms) }
            s.put("last_terminal_at", now); s.put("last_terminal_ms", ms)
        } else if (state == "login" && s.has("first_terminal_ms")) {
            val mins = (ms - s.getLong("first_terminal_ms")) / 60000
            if (!s.has("login_after_terminal_min")) s.put("login_after_terminal_min", mins)
            s.put("last_login_seen_at", now)
        }
        if (state == "login" && !s.has("first_login_seen_at")) s.put("first_login_seen_at", now)
        if (state == "code_or_2fa") s.put("code_or_2fa_seen_count", s.optInt("code_or_2fa_seen_count") + 1)
        rep.save()
    }

    // ---- saved login + auto-login test -----------------------------------------------------------------------

    private fun showLoginDialog() {
        val box = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(40, 20, 40, 0) }
        val u = EditText(this).apply { hint = "username / email"; inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_EMAIL_ADDRESS
            importantForAutofill = View.IMPORTANT_FOR_AUTOFILL_NO }
        val p = EditText(this).apply { hint = "password"; inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            importantForAutofill = View.IMPORTANT_FOR_AUTOFILL_NO }
        box.addView(u); box.addView(p)
        val dlg = AlertDialog.Builder(this)
            .setTitle("Set login (kept encrypted on this phone only)")
            .setView(box)
            .setPositiveButton("Save") { _, _ ->
                val us = u.text.toString(); val ps = p.text.toString()
                if (us.isNotEmpty() && ps.isNotEmpty()) { Creds.save(this, us, ps); setStatus("login saved (encrypted)") } else setStatus("nothing saved: both fields needed")
                u.setText(""); p.setText("")
                rep.root.optJSONObject("device")?.put("login_saved", Creds.has(this)); rep.save()
            }
            .setNeutralButton("Delete saved") { _, _ -> Creds.clear(this); rep.root.optJSONObject("device")?.put("login_saved", false); rep.save(); setStatus("saved login deleted") }
            .setNegativeButton("Cancel", null)
            .create()
        dlg.window?.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        dlg.show()
    }

    private fun typeKeys(s: String) {
        val km = KeyCharacterMap.load(KeyCharacterMap.VIRTUAL_KEYBOARD)
        val evs = km.getEvents(s.toCharArray()) ?: return
        for (e in evs) web.dispatchKeyEvent(e)
    }

    private fun autoLogin() {
        if (alBusy || fixturesRunning) { setStatus("busy: wait for the running test"); return }
        val cred = Creds.load(this)
        if (cred == null) { setStatus("no login saved: tap Set login first"); return }
        alBusy = true; fixtureMode = false
        inflightId = null; gen++                       // cancel any capture in flight
        val rec = JSONObject().put("at", Report.now()).put("net", net())
        val t0 = SystemClock.elapsedRealtime()
        // fresh start: drop the web session so this measures a real unattended login, not a leftover cookie
        CookieManager.getInstance().removeAllCookies(null); CookieManager.getInstance().flush()
        WebStorage.getInstance().deleteAllData(); web.clearCache(true)
        rec.put("session_cleared", true)
        setStatus("auto-login: loading…")
        alTry(listOf(SITE, APP), 0, rec, t0, cred)
    }

    private fun alTry(urls: List<String>, i: Int, rec: JSONObject, t0: Long, cred: Pair<String, String>) {
        rec.put("tried_$i", host(urls[i]))
        web.loadUrl(urls[i])
        ui.postDelayed({
            alPollFind(SystemClock.elapsedRealtime() + 14_000) { o ->
                when {
                    o.optBoolean("cf") -> alEnd(rec, "challenge_before_form", t0, 0)
                    o.optBoolean("blocked") -> alEnd(rec, "blocked", t0, 0)
                    o.optBoolean("pw") -> alFill(o, rec, t0, cred)
                    i + 1 < urls.size -> alTry(urls, i + 1, rec, t0, cred)
                    else -> alEnd(rec, "no_login_form", t0, 0)
                }
            }
        }, 5_000)
    }

    private fun alPollFind(deadline: Long, cb: (JSONObject) -> Unit) {
        ensureProbe {
            js("window.__probeLogin ? window.__probeLogin.find() : '{}'") { r ->
                val o = try { JSONObject(r) } catch (e: Exception) { JSONObject() }
                if (o.optBoolean("pw") || o.optBoolean("cf") || o.optBoolean("blocked") || SystemClock.elapsedRealtime() > deadline) cb(o)
                else ui.postDelayed({ alPollFind(deadline, cb) }, 2_000)
            }
        }
    }

    private fun alFill(o: JSONObject, rec: JSONObject, t0: Long, cred: Pair<String, String>) {
        rec.put("form_host", host(web.url)).put("form_found", true).put("id_field", o.optBoolean("id")).put("submit_found", o.optBoolean("submit"))
            .put("captcha_widget", o.optJSONObject("captcha"))
        if (!o.optBoolean("host_ok") || !o.optBoolean("action_ok")) { alEnd(rec, "refused_foreign_host", t0, 0); return }
        if ((o.optJSONObject("captcha")?.optInt("h") ?: 0) >= 50) { alEnd(rec, "interactive_challenge_before_submit", t0, 0); return }
        val (user, pass) = cred
        fun lit(s: String) = JSONObject.quote(s)
        fun fill(which: String, v: String, cb: (Boolean) -> Unit) = js("window.__probeLogin.fill('$which', ${lit(v)})") { cb(it == "ok") }
        fun afterFill(route: String) {
            rec.put("route", route)
            if (rec.optBoolean("id_field") && route == "native_setter") { /* length already verified */ }
            js("String(window.__probeLogin.submitDisabled())") { dis ->
                rec.put("submit_disabled_after_fill", dis)
                if (dis == "true" && route == "native_setter") { alKeys(rec, t0, cred); return@js }
                alSubmit(rec, t0)
            }
        }
        val idPresent = o.optBoolean("id")
        val doPw = { fill("pw", pass) { okPw -> rec.put("filled_password", okPw); if (okPw) afterFill("native_setter") else alKeys(rec, t0, cred) } }
        if (idPresent) fill("id", user) { okId -> rec.put("filled_identity", okId); doPw() } else doPw()
    }

    /** Fallback route: real key events into the focused field (the typing route the fixtures validate for React-style forms). */
    private fun alKeys(rec: JSONObject, t0: Long, cred: Pair<String, String>) {
        val (user, pass) = cred
        rec.put("route", "key_events")
        fun typeInto(which: String, v: String, next: () -> Unit) {
            js("window.__probeLogin.focus('$which')") { f ->
                if (f != "focused") { next(); return@js }
                web.requestFocus()
                ui.postDelayed({ typeKeys(v); ui.postDelayed({ next() }, 400) }, 300)
            }
        }
        val usePw = { typeInto("pw", pass) { js("window.__probeLogin.filledLen('pw', ${pass.length})") { ok -> rec.put("filled_password", ok == "ok"); alSubmit(rec, t0) } } }
        if (rec.optBoolean("id_field")) typeInto("id", user) { usePw() } else usePw()
    }

    private fun alSubmit(rec: JSONObject, t0: Long) {
        js("window.__probeLogin.click()") { c ->
            rec.put("submit_result", c)
            if (c == "no" || c == "no_submit") { alEnd(rec, "submit_failed", t0, 0); return@js }
            rec.put("submitted", true)
            alPollOutcome(SystemClock.elapsedRealtime(), rec, t0, false)
        }
    }

    private fun alPollOutcome(tSubmit: Long, rec: JSONObject, t0: Long, openClicked: Boolean) {
        ui.postDelayed({
            ensureProbe {
                js("window.__probeLogin ? window.__probeLogin.status() : '{}'") { r ->
                    val s = try { JSONObject(r) } catch (e: Exception) { JSONObject() }
                    val el = SystemClock.elapsedRealtime() - tSubmit
                    val capH = s.optJSONObject("captcha")?.optInt("h") ?: 0
                    val pw = s.optBoolean("pw")
                    when {
                        s.optBoolean("cf") -> alEnd(rec, "challenge_after_submit", t0, el)
                        s.optBoolean("blocked") -> alEnd(rec, "blocked_after_submit", t0, el)
                        capH >= 50 && pw -> alEnd(rec, "interactive_challenge_after_submit", t0, el)
                        (s.optBoolean("otp") || s.optBoolean("twofa")) && !pw -> alEnd(rec, "code_or_2fa", t0, el)
                        !pw && s.optBoolean("buyish") -> alEnd(rec, "logged_in_terminal", t0, el)
                        !pw && s.optBoolean("open_terminal") && !openClicked -> {
                            // a plain navigation control on the dashboard, not a trading control
                            js("window.__probeLogin.openTerminal()") { rec.put("open_terminal_clicked", it == "clicked") }
                            alPollOutcome(tSubmit, rec, t0, true)
                        }
                        !pw && el > 12_000 -> alEnd(rec, "logged_in_non_terminal", t0, el)
                        pw && el > 25_000 -> alEnd(rec, "still_login_form", t0, el)
                        el > 60_000 -> alEnd(rec, "timeout", t0, el)
                        else -> alPollOutcome(tSubmit, rec, t0, openClicked)
                    }
                }
            }
        }, 1_500)
    }

    private fun alEnd(rec: JSONObject, outcome: String, t0: Long, sinceSubmitMs: Long) {
        rec.put("outcome", outcome).put("ms_total", SystemClock.elapsedRealtime() - t0)
        if (outcome.startsWith("logged_in")) rec.put("ms_submit_to_logged_in", sinceSubmitMs)
        rep.append("autologin", rec, 20)
        alBusy = false
        setStatus("auto-login: $outcome (${SystemClock.elapsedRealtime() - t0} ms)")
        // store the post-login shape too (a manual-style capture), unless a human challenge stopped us
        if (!outcome.contains("challenge") && outcome != "blocked") ui.postDelayed({ capture("autologin", true, "autologin") }, 1_500)
    }

    // ---- local fixtures --------------------------------------------------------------------------------------

    private fun tap(id: String, then: () -> Unit) {
        js("__fx.rect('$id')") { r ->
            val o = JSONObject(r); val dpr = o.getDouble("dpr").toFloat()
            val x = o.getDouble("x").toFloat() * dpr; val y = o.getDouble("y").toFloat() * dpr
            val t = SystemClock.uptimeMillis()
            web.dispatchTouchEvent(MotionEvent.obtain(t, t, MotionEvent.ACTION_DOWN, x, y, 0))
            web.dispatchTouchEvent(MotionEvent.obtain(t, t + 60, MotionEvent.ACTION_UP, x, y, 0))
            ui.postDelayed(then, 350)
        }
    }

    private fun runFixtures() {
        if (inflightId != null || fixturesRunning || alBusy) { setStatus("busy: wait for the running test"); return }
        fixtureMode = true; fixturesRunning = true
        val res = JSONObject(); rep.root.put("fixtures", res)
        res.put("at", Report.now()).put("features", JSONObject().put("doc_start", featDoc).put("listener", featMsg)).put("complete", false)
        setStatus("fixtures RUNNING: do not copy yet…")
        while (fxReplies.length() > 0) fxReplies.remove(0)
        web.loadUrl("https://fixture-a.test/a.html")
        ui.postDelayed({ fxSteps(res) }, 3_500)
        // safety: never leave Copy locked
        ui.postDelayed({ if (fixturesRunning) { fixturesRunning = false; res.put("timed_out", true); rep.save(); setStatus("fixtures timed out: results incomplete") } }, 60_000)
    }

    private fun fxSteps(res: JSONObject) {
        fun state(cb: (JSONObject) -> Unit) = js("__fx.state()") { cb(JSONObject(it)) }
        js("document.title") { t ->
            res.put("main_frame_js_reach", t == "Fixture A")
            js("__fx.frameDirect()") { fd ->
                res.put("main_cannot_touch_cross_origin_iframe_dom", fd.startsWith("blocked"))
                js("__fx.reset()") {
                    js("__fx.naive()") { n ->
                        res.put("typing_js_value_plus_input_event_registered", n == "1.5")   // React quirk: expected false
                        js("__fx.nativeSetter()") { n2 ->
                            res.put("typing_native_setter_registered", n2 == "2.5")
                            js("__fx.focusQty()") { f ->
                                web.requestFocus()
                                ui.postDelayed({
                                    for (k in intArrayOf(KeyEvent.KEYCODE_3, KeyEvent.KEYCODE_PERIOD, KeyEvent.KEYCODE_5)) {
                                        web.dispatchKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN, k)); web.dispatchKeyEvent(KeyEvent(KeyEvent.ACTION_UP, k))
                                    }
                                    ui.postDelayed({ state { s1 ->
                                        res.put("typing_real_key_events_registered", f == "focused" && s1.optString("qty") == "3.5")
                                        tap("buy") { tap("slsw") { tap("send") { state { s2 ->
                                            res.put("touch_tap_side_registered", s2.optString("side") == "buy")
                                            res.put("touch_tap_switch_registered", s2.optBoolean("sl"))
                                            res.put("touch_tap_send_registered", s2.optInt("sent") == 1)
                                            fxIframe(res)
                                        } } } }
                                    } }, 500)
                                }, 500)
                            }
                        }
                    }
                }
            }
        }
    }

    private fun fxIframe(res: JSONObject) {
        js("document.getElementById('fr').contentWindow.postMessage({__probe:'fx',op:'type',val:'4.5',id:'fxb'},'*'); 'sent'") {
            ui.postDelayed({
                var ok = false; var fromIframe = false
                for (i in 0 until fxReplies.length()) {
                    val r = fxReplies.getJSONObject(i)
                    if (!r.optBoolean("main_frame")) { fromIframe = true; if (r.optJSONObject("res")?.optString("qty") == "4.5") ok = true }
                }
                res.put("iframe_doc_start_script_runs_in_cross_origin_frame", fromIframe)
                res.put("iframe_typing_via_parent_postmessage_registered", ok)
                // shape capture across both frames, the same path the real-page capture uses
                fixtureMode = false; capture("fixcap", false, "fixture-capture")
                ui.postDelayed({
                    var iframeControls = 0
                    for (i in 0 until frames.length()) {
                        val f = frames.getJSONObject(i)
                        if (!f.optBoolean("main_frame")) iframeControls += f.optJSONObject("shape")?.optJSONArray("controls")?.length() ?: 0
                    }
                    res.put("shape_capture_frames", frames.length())
                    res.put("shape_capture_iframe_controls_seen", iframeControls)
                    res.put("shape_capture_cross_origin_iframe_hosts", directCross.length())
                    res.put("note", "fixtures use the app's own pages; they prove mechanics, not Breakout's layout")
                    res.put("complete", true)
                    fixtureMode = true; fixturesRunning = false
                    rep.save()
                    setStatus("fixtures done: " + res.toString().take(300))
                }, 4_200)
            }, 800)
        }
    }

    // ---- export ----------------------------------------------------------------------------------------------

    private fun export(share: Boolean) {
        if (fixturesRunning) { setStatus("fixtures RUNNING: wait for \"fixtures done\", then Copy"); return }
        if (alBusy) { setStatus("auto-login RUNNING: wait for its result, then Copy"); return }
        val bad = Report.leaks(rep.root)
        if (bad.isNotEmpty()) { setStatus("REFUSED: ${bad.size} suspicious strings, e.g. ${bad.first()} — not exported"); rep.event("export_refused"); return }
        val text = rep.root.toString()
        // the saved login must never appear in the report, in any form
        Creds.load(this)?.let { (u, p) ->
            if ((u.length >= 3 && text.contains(u)) || (p.length >= 3 && text.contains(p))) { setStatus("REFUSED: report contains the saved login — not exported"); rep.event("export_refused_login"); return }
        }
        if (share) startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, text), "Share report"))
        else (getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager).setPrimaryClip(ClipData.newPlainText("probe-report", text))
        setStatus("report ${if (share) "shared" else "copied"}: ${text.length} chars, redaction check clean")
    }
}
