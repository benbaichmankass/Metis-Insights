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

    suspend fun event(kind: String, reason: String = "", ticket: String = "") =
        post("event", JSONObject().put("event", kind).put("reason", reason.take(150)).put("ticket_id", ticket))

    /** "terminal did not load": the page's control texts (our own UI labels, digits masked page-side). */
    suspend fun terminalMiss(reason: String, controls: JSONArray) =
        post("event", JSONObject().put("event", "terminal_miss").put("reason", reason.take(150)).put("controls", controls))
}
