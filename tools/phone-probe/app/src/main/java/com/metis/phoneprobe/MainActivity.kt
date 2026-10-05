package com.metis.phoneprobe

import android.annotation.SuppressLint
import android.app.Activity
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
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.webkit.CookieManager
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import androidx.webkit.WebMessageCompat
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import org.json.JSONArray
import org.json.JSONObject
import org.json.JSONTokener
import java.io.ByteArrayInputStream

/**
 * Phase 1a probe. A kiosk WebView with a page-state classifier and a REDACTED shape capture.
 * It places no orders, taps nothing on a Breakout page, reads no input value, saves no credential.
 * The user agent is the stock WebView one: no spoofing, no challenge handling, no reload loop.
 */
class MainActivity : Activity() {
    private val SITE = "https://app.breakoutprop.com/"
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

    // capture in flight: id -> frames collected
    private var inflightId: String? = null
    private var inflightManual = false
    private var inflightLabel = ""
    private val frames = JSONArray()
    private val fxReplies = JSONArray()
    private var navTimer: Runnable? = null

    private val tick = object : Runnable {
        override fun run() {
            if (!fixtureMode && inflightId == null && web.url != null && web.url!!.startsWith("https://app.breakoutprop")) capture("tick", false, "tick")
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
        if (featDoc) WebViewCompat.addDocumentStartJavaScript(web, assets.open("probe.js").bufferedReader().readText(), setOf("*"))

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
                if (fixtureMode) return
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
        root.addView(row("1 Load site" to { loadSite("load") }, "2 Capture" to { capture("manual", true, "manual") }, "3 Recheck" to { loadSite("recheck") }))
        root.addView(row("Fixtures" to { runFixtures() }, "Copy" to { export(false) }, "Share" to { export(true) }, "Clear" to { rep.clear(); setStatus("report cleared") }))
        root.addView(status)
        root.addView(web, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        setContentView(root)

        val d = JSONObject().put("sdk", Build.VERSION.SDK_INT).put("release", Build.VERSION.RELEASE).put("model", Build.MODEL).put("brand", Build.MANUFACTURER)
        val wvp = WebView.getCurrentWebViewPackage()
        val ua = web.settings.userAgentString ?: ""
        rep.root.put("device", d).put("webview", JSONObject().put("pkg", wvp?.packageName ?: "?").put("ver", wvp?.versionName ?: "?").put("ua_wv_marker", ua.contains("; wv)")))
            .put("features", JSONObject().put("document_start_script", featDoc).put("web_message_listener", featMsg))
        rep.save()
        ui.postDelayed(tick, 30_000)
        setStatus("ready · doc-start=$featDoc listener=$featMsg · tap 1")
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

    private fun loadSite(label: String) {
        fixtureMode = false
        rep.event("load_requested", JSONObject().put("label", label).put("net", net()))
        web.loadUrl(SITE)
    }

    // ---- capture pipeline ------------------------------------------------------------------------------------

    private fun capture(id: String, manual: Boolean, label: String) {
        if (inflightId != null) { setStatus("capture already running"); return }
        inflightId = id + "-" + System.currentTimeMillis(); inflightManual = manual; inflightLabel = label
        while (frames.length() > 0) frames.remove(0)
        web.evaluateJavascript("window.__probeCapture ? window.__probeCapture('$inflightId') : 'no_probe_js'") { r ->
            if (r?.contains("no_probe_js") == true) rep.event("probe_js_missing")
        }
        ui.postDelayed({ finalizeCapture() }, 3_500)
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
            m.optBoolean("otc") || (m.optBoolean("twofa") && !m.optBoolean("pw")) -> "code_or_2fa"
            m.optBoolean("pw") -> "login"
            (m.optBoolean("buy") && m.optBoolean("sell")) || m.optBoolean("send") -> "terminal"
            else -> "other_served"
        }
    }

    private fun finalizeCapture() {
        val id = inflightId ?: return
        var top: JSONObject? = null
        for (i in 0 until frames.length()) { val f = frames.getJSONObject(i); if (f.optBoolean("main_frame")) top = f }
        val state = classify(top)
        val h = host(web.url)
        val summary = JSONObject().put("at", Report.now()).put("label", inflightLabel).put("host", h).put("state", state)
            .put("http", lastHttp).put("cf_mitigated", lastCf).put("net", net())
            .put("ms_since_load_start", SystemClock.elapsedRealtime() - loadStartMs).put("frames", frames.length())
        top?.optJSONObject("shape")?.let { summary.put("counts", it.optJSONObject("counts")).put("markers", it.optJSONObject("markers")) }
        val changed = state != lastState
        lastState = state
        trackSession(state)
        if (inflightManual) {
            val cap = JSONObject().put("at", Report.now()).put("label", inflightLabel).put("state", state).put("net", net())
            val fr = JSONArray(); for (i in 0 until frames.length()) { val f = JSONObject(frames.getJSONObject(i).toString()); f.remove("id"); fr.put(f) }
            cap.put("frames", fr)
            rep.append("captures", cap, 6)
            rep.append("probes", summary, 60)
        } else if (changed || inflightLabel == "nav") {
            rep.append("navs", summary, 60)
        }
        setStatus("state=$state host=$h http=$lastHttp frames=${frames.length()}")
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

    // ---- local fixtures --------------------------------------------------------------------------------------

    private fun unq(raw: String?): String = try { JSONArray("[" + raw + "]").get(0).toString() } catch (e: Exception) { raw ?: "" }
    private fun js(code: String, cb: (String) -> Unit) = web.evaluateJavascript(code) { cb(unq(it)) }

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
        if (inflightId != null) return
        fixtureMode = true
        val res = JSONObject(); rep.root.put("fixtures", res)
        res.put("at", Report.now()).put("features", JSONObject().put("doc_start", featDoc).put("listener", featMsg))
        setStatus("fixtures: loading…")
        while (fxReplies.length() > 0) fxReplies.remove(0)
        web.loadUrl("https://fixture-a.test/a.html")
        ui.postDelayed({ fxSteps(res) }, 3_500)
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
                // shape capture across both frames, as the real-page capture does
                fixtureMode = false; capture("fixcap", false, "fixture-capture")
                ui.postDelayed({
                    var iframeControls = 0
                    for (i in 0 until frames.length()) {
                        val f = frames.getJSONObject(i)
                        if (!f.optBoolean("main_frame")) iframeControls += f.optJSONObject("shape")?.optJSONArray("controls")?.length() ?: 0
                    }
                    res.put("shape_capture_frames", frames.length())
                    res.put("shape_capture_iframe_controls_seen", iframeControls)
                    res.put("note", "fixtures use the app's own pages; they prove mechanics, not Breakout's layout")
                    rep.save(); fixtureMode = true
                    setStatus("fixtures done: " + res.toString().take(300))
                }, 4_200)
            }, 800)
        }
    }

    // ---- export ----------------------------------------------------------------------------------------------

    private fun export(share: Boolean) {
        val bad = Report.leaks(rep.root)
        if (bad.isNotEmpty()) { setStatus("REFUSED: ${bad.size} suspicious strings, e.g. ${bad.first()} — not exported"); rep.event("export_refused"); return }
        val text = rep.root.toString()
        if (share) startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, text), "Share report"))
        else (getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager).setPrimaryClip(ClipData.newPlainText("probe-report", text))
        setStatus("report ${if (share) "shared" else "copied"}: ${text.length} chars, redaction check clean")
    }
}
