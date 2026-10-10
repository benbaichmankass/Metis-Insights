package com.metis.phoneexec

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.PixelFormat
import android.provider.Settings
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.webkit.CookieManager
import android.webkit.WebView
import android.webkit.WebViewClient
import kotlinx.coroutines.delay
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONArray
import org.json.JSONObject
import kotlin.coroutines.resume

class JsTimeout : Exception("page did not answer")

/** One WebView + exec.js. Every call is bounded: a frozen WebView never answers, and an unbounded wait wedged the
 *  loop (PI-20261006-APBY4NTV-0006). Main thread only. */
class Page(val web: WebView, private val execSrc: String) {
    var timeouts = 0
    private fun unq(raw: String?): String = try { JSONArray("[" + raw + "]").get(0).toString() } catch (e: Exception) { raw ?: "" }
    suspend fun js(code: String): String =
        withTimeoutOrNull(JS_TIMEOUT_MS) { suspendCancellableCoroutine<String> { c -> web.evaluateJavascript(code) { if (c.isActive) c.resume(unq(it)) } } }
            ?: run { timeouts += 1; throw JsTimeout() }
    suspend fun ensure() { if (js("typeof window.__ex") != "object") { js(execSrc + ";'ok'"); delay(100) } }
    /** null on anything unreadable, a timeout included (the kiosk's jsObj semantics; js() still counts the timeout) */
    suspend fun obj(code: String): JSONObject? = try { JSONObject(js("JSON.stringify($code)")) } catch (e: Exception) { null }
    fun load(url: String) = web.loadUrl(url)
    /** After a navigation: poll (every 2 s, up to [maxMs]) until the document is complete and __ex.state() reads. */
    suspend fun settle(maxMs: Long): JSONObject? {
        val end = System.currentTimeMillis() + maxMs
        while (System.currentTimeMillis() < end) {
            delay(2000)
            try { if (js("document.readyState") == "complete") { ensure(); obj("__ex.state()")?.let { return it } } } catch (e: Exception) { }
        }
        return null
    }

    companion object { const val JS_TIMEOUT_MS = 10_000L }
}

/**
 * BACKGROUND ENGINE (PHONE-AUTOLOGIN-2; operator 2026-10-10: "it would be more stable if it could do it in the
 * background ... the more automated and backgrounded it could be, the better"). A SECOND WebView owned by the
 * process (the foreground KeepAliveService keeps the process alive), sharing the kiosk WebView's cookie store and
 * site storage (one WebView profile per app), so a login made here is the kiosk's login too.
 *
 * Chromium throttles or stops a WebView whose window is not visible, which is what froze the kiosk WebView in the
 * background (2026-10-06). So for each job (a login check / re-login / account panel read) the engine view is
 * attached to a full-size, fully TRANSPARENT, NOT-touchable, NOT-focusable overlay window ("Display over other apps",
 * already granted for the boot restart) and detached again when the job ends, so the overlay never lingers over what
 * the operator is doing. Without the overlay permission it falls back to a detached view laid out at screen size
 * (engine "detached"); if that does not answer the app falls back to bringing the Activity forward.
 * It never runs a ticket: orders stay on the foreground path.
 */
object BgWeb {
    private var page: Page? = null
    private var attached = false
    var engine = ""          // "overlay" | "detached" | "" (never created)
        private set

    @SuppressLint("SetJavaScriptEnabled")
    fun attach(c: Context, execSrc: String): Page? {
        val app = c.applicationContext
        val p = page ?: try {
            val w = WebView(app)
            w.settings.javaScriptEnabled = true
            w.settings.domStorageEnabled = true
            w.settings.allowFileAccess = false; w.settings.allowContentAccess = false
            w.settings.mixedContentMode = android.webkit.WebSettings.MIXED_CONTENT_NEVER_ALLOW
            CookieManager.getInstance().setAcceptCookie(true)
            CookieManager.getInstance().setAcceptThirdPartyCookies(w, true)
            w.webViewClient = WebViewClient()   // every navigation stays in this WebView (as in the kiosk)
            Page(w, execSrc).also { page = it }
        } catch (e: Exception) { return null }
        if (attached) return p
        val dm = app.resources.displayMetrics
        val w = p.web
        engine = "detached"
        if (Settings.canDrawOverlays(app)) {
            try {
                val lp = WindowManager.LayoutParams(dm.widthPixels, dm.heightPixels,
                    WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                    WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
                        WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
                    PixelFormat.TRANSLUCENT).apply { gravity = Gravity.TOP or Gravity.START; alpha = 0f }
                app.getSystemService(WindowManager::class.java).addView(w, lp)
                engine = "overlay"
            } catch (e: Exception) { engine = "detached" }
        }
        if (engine == "detached") {
            w.measure(View.MeasureSpec.makeMeasureSpec(dm.widthPixels, View.MeasureSpec.EXACTLY),
                View.MeasureSpec.makeMeasureSpec(dm.heightPixels, View.MeasureSpec.EXACTLY))
            w.layout(0, 0, dm.widthPixels, dm.heightPixels)
        }
        w.onResume(); w.resumeTimers()
        attached = true
        return p
    }

    /** End of a job: take the overlay down (the page and its session stay in memory for the next job). */
    fun detach(c: Context) {
        val w = page?.web ?: return
        if (!attached) return
        attached = false
        if (engine == "overlay") try { c.applicationContext.getSystemService(WindowManager::class.java).removeView(w) } catch (e: Exception) { }
    }
}
