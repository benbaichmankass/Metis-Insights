package com.metis.phoneexec

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/** The VM contract (the /api/bot/prop/phone routes), per-device bearer. HTTPS only. Errors return null (fail closed). */
class Api(private val c: Context) {
    private fun base(): String = (Store.get(c, Store.API) ?: Store.DEFAULT_API).trimEnd('/')

    suspend fun post(path: String, body: JSONObject = JSONObject()): JSONObject? = withContext(Dispatchers.IO) {
        try {
            val u = URL(base() + "/api/bot/prop/phone/" + path)
            if (u.protocol != "https") return@withContext null
            val conn = u.openConnection() as HttpURLConnection
            conn.requestMethod = "POST"
            conn.connectTimeout = 10000; conn.readTimeout = 20000
            conn.setRequestProperty("Content-Type", "application/json")
            conn.setRequestProperty("Authorization", "Bearer " + Store.token(c))
            conn.doOutput = true
            conn.outputStream.use { it.write(body.toString().toByteArray()) }
            val code = conn.responseCode
            val txt = (if (code in 200..299) conn.inputStream else conn.errorStream)?.bufferedReader()?.readText() ?: ""
            if (code !in 200..299) JSONObject().put("ok", false).put("http", code) else JSONObject(txt)
        } catch (e: Exception) { null }
    }

    /** READ-ONLY peek used while the Activity is backgrounded: tickets waiting for this device's account, or null
     *  when the VM is unreachable / refused. Claims nothing (the claim happens only once the Activity is resumed). */
    suspend fun pending(): Int? = withContext(Dispatchers.IO) {
        try {
            val u = URL(base() + "/api/bot/prop/phone/pending")
            if (u.protocol != "https") return@withContext null
            val conn = u.openConnection() as HttpURLConnection
            conn.connectTimeout = 10000; conn.readTimeout = 20000
            conn.setRequestProperty("Authorization", "Bearer " + Store.token(c))
            if (conn.responseCode !in 200..299) null
            else JSONObject(conn.inputStream.bufferedReader().readText()).optInt("pending", -1).takeIf { it >= 0 }
        } catch (e: Exception) { null }
    }

    suspend fun event(kind: String, reason: String = "", ticket: String = "") =
        post("event", JSONObject().put("event", kind).put("reason", reason.take(150)).put("ticket_id", ticket))

    /** Heartbeat: the on-screen status line + the app's own state (quiet; latest kept server-side). */
    suspend fun heartbeat(statusLine: String, state: JSONObject) =
        post("event", JSONObject().put("event", "heartbeat").put("reason", statusLine.take(150)).put("state", state))

    /** "terminal did not load": the page's control texts (our own UI labels, digits masked page-side). */
    suspend fun terminalMiss(reason: String, controls: JSONArray) =
        post("event", JSONObject().put("event", "terminal_miss").put("reason", reason.take(150)).put("controls", controls))

    /** Account panel read (balance / equity as MEASURED off the terminal). Null fields stay absent, never 0. */
    suspend fun accountStatus(balance: Double?, equity: Double?, equityLabel: String) =
        post("report", JSONObject().put("kind", "account_status").put("source", "phone_executor")
            .put("provenance", "MEASURED").put("equity_label", equityLabel)
            .put("balance", balance ?: JSONObject.NULL).put("equity", equity ?: JSONObject.NULL))
}
