package com.metis.phoneexec

/**
 * PHONE-AUTOLOGIN-2 / § 7.19: the dedicated-inbox credentials, normalised and DESCRIBED without their values.
 * Pure (no Android), so the JVM tests cover it.
 *
 * Why this exists: Setup removed only the ASCII space from the app password. A Gmail app password is 16 letters
 * a-z shown in four groups; copied from Google's page or a password manager, the gaps (and the ends) can arrive as
 * a NO-BREAK SPACE (U+00A0), a thin/narrow space (U+2009/U+202F), a zero-width char (U+200B-U+200D, U+2060, U+FEFF)
 * or a newline. Any one of those left in the password makes Gmail answer "[AUTHENTICATIONFAILED] Invalid credentials"
 * however right the 16 letters are -- and the app reported only the exception CLASS, so it read as a wrong password.
 */
object Creds {
    private val INVISIBLE = setOf('​', '‌', '‍', '⁠', '﻿', '­')

    private fun junk(c: Char) = c.isWhitespace() || c.isISOControl() || c in INVISIBLE || Character.isSpaceChar(c)

    /** App password: every whitespace / invisible / control char removed, anywhere. Applied at save AND at read, so a
     *  value stored by an older build is cleaned without being re-entered. */
    fun pass(raw: String): String = raw.filterNot(::junk)

    /** Inbox address: junk removed (an address never contains any). Case is kept (Gmail ignores it). */
    fun user(raw: String): String = raw.filterNot(::junk)

    private val KNOWN = listOf("gmail.com", "googlemail.com")

    /** user field shape, never the value: length, has '@', ends with a known Gmail domain. */
    fun userShape(u: String): String =
        "ulen=${u.length} uat=${u.contains('@')} udom=${KNOWN.any { u.lowercase().endsWith("@$it") }}"

    /** password shape, never the value: length (16 expected) and how many chars are NOT a-z (0 expected). */
    fun passShape(p: String): String =
        "plen=${p.length} p_upper=${p.count { it in 'A'..'Z' }} p_other=${p.count { it !in 'a'..'z' && it !in 'A'..'Z' }}"

    private val EMAIL = Regex("[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+")
    private val URL = Regex("https?://\\S+")

    /** The server's own words from an exception message (e.g. "[AUTHENTICATIONFAILED] Invalid credentials (Failure)",
     *  "[ALERT] Application-specific password required", "Please log in via your web browser"), scrubbed: the user
     *  and password values, any address and any link are removed (Gmail's text never carries them; filtered anyway),
     *  brackets become parentheses and only the server's diag charset survives. */
    fun scrub(msg: String?, user: String?, pass: String?, max: Int = 110): String {
        var s = msg ?: return "-"
        for (v in listOfNotNull(user, pass).filter { it.length >= 3 }) s = s.replace(v, "(v)", ignoreCase = true)
        s = URL.replace(s, "(url)")
        s = EMAIL.replace(s, "(addr)")
        s = s.replace('[', '(').replace(']', ')')
        s = s.map { if (it.isLetterOrDigit() && it.code < 128 || it in " _.:/()=+-") it else ' ' }.joinToString("")
        return s.replace(Regex(" +"), " ").trim().take(max).ifEmpty { "-" }
    }
}
