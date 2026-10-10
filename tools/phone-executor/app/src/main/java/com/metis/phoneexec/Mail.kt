package com.metis.phoneexec

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.util.Date
import java.util.Properties
import javax.mail.Folder
import javax.mail.Multipart
import javax.mail.Part
import javax.mail.Session
import javax.mail.search.FromStringTerm

/**
 * The DEDICATED inbox (a Gmail used only for Breakout; the operator's main Gmail forwards Breakout login
 * mails to it). Opened READ_ONLY over IMAPS; nothing is moved, flagged or deleted. The newest (at most
 * [MAX_SCAN]) messages from breakoutprop.com received after `since` are read, newest first, and reduced to their
 * NUMBERS ([MailNums]): which 1-3 digit numbers the mail shows, and which https link carries each number as its text.
 * The body, the numbers, the links and the address are never logged, stored or reported; only counts leave
 * ([LoginMatch.decide] and the login_diag event).
 */
object Mail {
    const val MAX_SCAN = 5

    /** One poll of the inbox. `error` = the exception class when the inbox could not be read (counts only). */
    class Scan(val mails: List<MailNums>, val error: String?)

    suspend fun scan(user: String, appPassword: String, since: Date): Scan =
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
                    // newest first: a newer unrelated Breakout mail never hides the login mail behind it
                    Scan(msgs.take(MAX_SCAN).map { MailNums.parse(body(it, "text/html"), body(it, "text/plain")) }, null)
                } finally { inbox.close(false) }
            } catch (e: Exception) { Scan(emptyList(), e.javaClass.simpleName) } finally { try { store.close() } catch (_: Exception) {} }
        }

    private fun body(p: Part, mime: String): String? {
        if (p.isMimeType(mime)) return p.content as? String
        if (p.isMimeType("multipart/*")) {
            val mp = p.content as Multipart
            for (i in 0 until mp.count) body(mp.getBodyPart(i), mime)?.let { return it }
        }
        return null
    }
}
