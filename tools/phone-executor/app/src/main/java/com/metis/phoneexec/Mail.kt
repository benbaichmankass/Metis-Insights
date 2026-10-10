package com.metis.phoneexec

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.util.Date
import java.util.Properties
import javax.mail.AuthenticationFailedException
import javax.mail.Folder
import javax.mail.Multipart
import javax.mail.Part
import javax.mail.Session
import javax.mail.internet.ContentType
import javax.mail.internet.MimeMultipart
import javax.mail.search.FromStringTerm
import javax.mail.util.ByteArrayDataSource
import java.io.InputStream

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

    /** One poll of the inbox. `error` = the exception class when the inbox could not be read; `detail` = the server's
     *  own words, SCRUBBED ([Creds.scrub]); `mech` = the IMAP login mechanism of the last try; `authFailed` = Gmail
     *  refused the credentials (retrying them every 10 s cannot help and only invites Gmail's login throttle). */
    class Scan(val mails: List<MailNums>, val error: String?, val detail: String = "", val mech: String = "",
               val authFailed: Boolean = false)

    private fun props(mech: String) = Properties().apply {
        put("mail.store.protocol", "imaps"); put("mail.imaps.host", "imap.gmail.com")
        put("mail.imaps.port", "993"); put("mail.imaps.ssl.enable", "true"); put("mail.imaps.ssl.checkserveridentity", "true")
        put("mail.imaps.connectiontimeout", "15000"); put("mail.imaps.timeout", "20000")
        // Gmail takes PLAIN and LOGIN with an app password; never XOAUTH2 (needs a token, not a password)
        put("mail.imaps.auth.xoauth2.disable", "true"); put("mail.imaps.sasl.enable", "false")
        if (mech == "LOGIN") put("mail.imaps.auth.plain.disable", "true")
    }

    /** Connect with AUTHENTICATE PLAIN; on an authentication failure try the IMAP LOGIN command once, so a mechanism
     *  quirk can be told apart from a refused credential (both refused = the credential). Credentials are normalised
     *  here too ([Creds]) so a value stored by an older build is cleaned. */
    private fun open(user: String, pass: String): Pair<javax.mail.Store, String> {
        var last: AuthenticationFailedException? = null
        for (mech in listOf("PLAIN", "LOGIN")) {
            val store = Session.getInstance(props(mech)).getStore("imaps")
            try { store.connect("imap.gmail.com", user, pass); return store to mech }
            catch (e: AuthenticationFailedException) { last = e; try { store.close() } catch (_: Exception) {} }
        }
        throw MechFail(last!!)
    }
    private class MechFail(val auth: AuthenticationFailedException) : Exception(auth.message)

    suspend fun scan(rawUser: String, rawPass: String, since: Date): Scan =
        withContext(Dispatchers.IO) {
            val user = Creds.user(rawUser); val pass = Creds.pass(rawPass)
            var store: javax.mail.Store? = null
            try {
                val (s, mech) = open(user, pass); store = s
                val inbox = s.getFolder("INBOX"); inbox.open(Folder.READ_ONLY)
                try {
                    val msgs = inbox.search(FromStringTerm("breakoutprop.com"))
                        .filter { (it.receivedDate ?: it.sentDate)?.after(since) == true }
                        .sortedByDescending { (it.receivedDate ?: it.sentDate)?.time ?: 0L }
                    // newest first: a newer unrelated Breakout mail never hides the login mail behind it
                    Scan(msgs.take(MAX_SCAN).map { MailNums.parse(body(it, "text/html"), body(it, "text/plain")) }, null, mech = mech)
                } finally { inbox.close(false) }
            } catch (e: MechFail) {
                Scan(emptyList(), "AuthenticationFailedException", Creds.scrub(e.auth.message, user, pass), "PLAIN+LOGIN", authFailed = true)
            } catch (e: Exception) {
                Scan(emptyList(), e.javaClass.simpleName, Creds.scrub(e.message ?: e.cause?.message, user, pass))
            } finally { try { store?.close() } catch (_: Exception) {} }
        }

    /** One immediate connect (Setup's "Test inbox"): the scrubbed outcome plus the credential SHAPES, no value. */
    suspend fun test(rawUser: String, rawPass: String): String {
        val u = Creds.user(rawUser); val p = Creds.pass(rawPass)
        val sc = scan(rawUser, rawPass, Date(System.currentTimeMillis() - 24 * 3600_000L))
        val res = if (sc.error == null) "ok mech=${sc.mech} bo_mails_24h=${sc.mails.size}"
            else "FAIL ${sc.error} mech=${sc.mech.ifEmpty { "-" }} srv=(${sc.detail})"
        return "$res ${Creds.userShape(u)} ${Creds.passShape(p)} raw_plen=${rawPass.length}"
    }

    /** A part's text of the wanted MIME type, or null. On Android the JavaMail data handlers are often not
     *  registered (no mailcap), so `Part.content` can come back as the RAW stream (IMAPInputStream) instead of a
     *  String / Multipart -- MEASURED 2026-10-10 20:35 local: Test inbox, first successful login, failed with
     *  "IMAPInputStream cannot be cast to javax.mail.Multipart". Both shapes are handled here; anything else is null. */
    private fun body(p: Part, mime: String): String? {
        if (p.isMimeType(mime)) return when (val c = content(p)) {
            is String -> c
            is InputStream -> c.use { String(it.readBytes(), charsetOf(p)) }
            else -> null
        }
        if (p.isMimeType("multipart/*")) {
            val mp: Multipart = when (val c = content(p)) {
                is Multipart -> c
                is InputStream -> c.use { MimeMultipart(ByteArrayDataSource(it, p.contentType)) }
                else -> null
            } ?: return null
            for (i in 0 until mp.count) body(mp.getBodyPart(i), mime)?.let { return it }
        }
        return null
    }

    private fun content(p: Part): Any? = try { p.content } catch (e: Exception) { null }

    private fun charsetOf(p: Part): java.nio.charset.Charset = try {
        java.nio.charset.Charset.forName(ContentType(p.contentType).getParameter("charset") ?: "UTF-8")
    } catch (e: Exception) { Charsets.UTF_8 }
}
