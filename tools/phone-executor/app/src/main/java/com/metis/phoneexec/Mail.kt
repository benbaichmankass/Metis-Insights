package com.metis.phoneexec

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.util.Date
import java.util.Properties
import javax.mail.Folder
import javax.mail.Message
import javax.mail.Multipart
import javax.mail.Part
import javax.mail.Session
import javax.mail.search.FromStringTerm

/**
 * The DEDICATED inbox (a Gmail used only for Breakout; the operator's main Gmail forwards Breakout login
 * mails to it). Opened READ_ONLY over IMAPS; nothing is moved, flagged or deleted. Only the NEWEST message
 * from breakoutprop.com received after [since] is read, and only to find the ONE link whose visible text is
 * exactly the number the login page shows. The body, link and address are never logged or reported.
 */
object Mail {
    suspend fun findLink(user: String, appPassword: String, number: String, since: Date): String? =
        withContext(Dispatchers.IO) {
            val props = Properties().apply {
                put("mail.store.protocol", "imaps"); put("mail.imaps.host", "imap.gmail.com")
                put("mail.imaps.port", "993"); put("mail.imaps.ssl.checkserveridentity", "true")
                put("mail.imaps.connectiontimeout", "15000"); put("mail.imaps.timeout", "20000")
            }
            val store = Session.getInstance(props).getStore("imaps")
            try {
                store.connect("imap.gmail.com", user, appPassword)
                val inbox = store.getFolder("INBOX"); inbox.open(Folder.READ_ONLY)
                try {
                    val msgs = inbox.search(FromStringTerm("breakoutprop.com"))
                        .filter { (it.receivedDate ?: it.sentDate)?.after(since) == true }
                        .sortedByDescending { (it.receivedDate ?: it.sentDate)?.time ?: 0L }
                    val newest: Message = msgs.firstOrNull() ?: return@withContext null
                    pick(html(newest) ?: return@withContext null, number)
                } finally { inbox.close(false) }
            } catch (e: Exception) { null } finally { try { store.close() } catch (_: Exception) {} }
        }

    private fun html(p: Part): String? {
        if (p.isMimeType("text/html")) return p.content as? String
        if (p.isMimeType("multipart/*")) {
            val mp = p.content as Multipart
            for (i in 0 until mp.count) html(mp.getBodyPart(i))?.let { return it }
        }
        return null
    }

    /** Exactly one https anchor whose visible text equals [number]; anything else is null (fail closed). */
    fun pick(html: String, number: String): String? {
        val re = Regex("<a\\b[^>]*href\\s*=\\s*\"([^\"]+)\"[^>]*>(.*?)</a>", setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL))
        val hits = re.findAll(html).mapNotNull { m ->
            val text = m.groupValues[2].replace(Regex("<[^>]+>"), "").replace("&nbsp;", " ").trim()
            val href = m.groupValues[1].replace("&amp;", "&")
            if (text == number && href.startsWith("https://")) href else null
        }.distinct().toList()
        return if (hits.size == 1) hits[0] else null
    }
}
