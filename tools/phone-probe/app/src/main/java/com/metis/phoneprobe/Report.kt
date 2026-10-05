package com.metis.phoneprobe

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/** The probe's whole result, kept as one JSON object and persisted after every change so a killed app loses nothing. */
class Report(private val ctx: Context) {
    val root: JSONObject
    private val file = File(ctx.filesDir, "report.json")

    init {
        root = try { JSONObject(file.readText()) } catch (e: Exception) { fresh() }
        event("app_start")
    }

    private fun fresh() = JSONObject()
        .put("probe", "phone-probe-1a").put("report_version", 1)
        .put("probes", JSONArray()).put("navs", JSONArray()).put("captures", JSONArray())
        .put("autologin", JSONArray()).put("links", JSONArray()).put("fixtures", JSONObject()).put("events", JSONArray()).put("session", JSONObject())

    fun clear() {
        for (k in root.keys().asSequence().toList()) root.remove(k)
        val f = fresh(); for (k in f.keys()) root.put(k, f.get(k))
        event("report_cleared"); save()
    }

    fun save() { try { file.writeText(root.toString()) } catch (_: Exception) {} }

    fun append(key: String, o: JSONObject, cap: Int) {
        if (!root.has(key)) root.put(key, JSONArray())
        val a = root.getJSONArray(key); a.put(o)
        if (a.length() > cap) { val n = JSONArray(); for (i in a.length() - cap until a.length()) n.put(a.get(i)); root.put(key, n) }
        save()
    }

    fun event(what: String, extra: JSONObject? = null) {
        val o = JSONObject().put("at", now()).put("e", what)
        extra?.keys()?.forEach { o.put(it, extra.get(it)) }
        append("events", o, 200)
    }

    /** Wall clock in UTC, to the second. */
    companion object {
        fun now(): String = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US).apply { timeZone = TimeZone.getTimeZone("UTC") }.format(Date())

        /** Strings that must never leave the phone: long digit runs (ids), '@', bearer tokens. Returns offending paths. */
        fun leaks(v: Any?, path: String = "$", out: MutableList<String> = mutableListOf()): List<String> {
            when (v) {
                is JSONObject -> v.keys().forEach { leaks(v.get(it), "$path.$it", out) }
                is JSONArray -> for (i in 0 until v.length()) leaks(v.get(i), "$path[$i]", out)
                is String -> if (Regex("\\d{6,}").containsMatchIn(v) || v.contains('@') || v.contains("Bearer", true)) out.add(path)
            }
            return out
        }
    }
}
