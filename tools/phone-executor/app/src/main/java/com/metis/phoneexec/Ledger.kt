package com.metis.phoneexec

import android.content.Context
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream

/** Append-only, fsynced intent ledger (design 3.3). Written BEFORE any click. A ticket whose last state is
 *  `intended` or `submitted` on restart is NEVER retried: it is reported as unresolved for a human. */
class Ledger(c: Context) {
    private val f = File(c.filesDir, "ledger.jsonl")

    fun append(ticket: String, state: String, extra: JSONObject = JSONObject()) {
        val line = extra.put("ticket_id", ticket).put("state", state).put("t", System.currentTimeMillis()).toString() + "\n"
        FileOutputStream(f, true).use { it.write(line.toByteArray()); it.fd.sync() }
    }

    fun unresolved(): List<String> {
        if (!f.exists()) return emptyList()
        val last = LinkedHashMap<String, String>()
        f.readLines().forEach { l -> try { val o = JSONObject(l); last[o.getString("ticket_id")] = o.getString("state") } catch (_: Exception) {} }
        return last.filter { it.value == "intended" || it.value == "submitted" }.keys.toList()
    }

    /** The ticket's latest ledger state, or null when the ledger never saw it. */
    fun last(ticket: String): String? {
        if (!f.exists()) return null
        var st: String? = null
        f.readLines().forEach { l -> try { val o = JSONObject(l); if (o.getString("ticket_id") == ticket) st = o.getString("state") } catch (_: Exception) {} }
        return st
    }

    fun seen(ticket: String): Boolean = f.exists() && f.readLines().any { it.contains("\"ticket_id\":\"$ticket\"") }
}
