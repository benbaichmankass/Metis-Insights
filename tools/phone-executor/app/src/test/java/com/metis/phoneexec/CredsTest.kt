package com.metis.phoneexec

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** § 7.19: inbox credential normalisation and the value-free diag. SYNTHETIC values only. */
class CredsTest {
    @Test fun passStripsEveryKindOfGap() {
        // ASCII spaces (the only thing Setup used to strip), NBSP, narrow NBSP, zero-width space, BOM, newline, tab
        val pasted = "abcd efgh ijkl​mnop﻿\n\t "
        assertEquals("abcdefghijklmnop", Creds.pass(pasted))
        assertEquals("abcdefghijklmnop", Creds.pass("abcd efgh ijkl mnop"))
    }

    @Test fun oldNormalisationLeftTheNbspIn() {
        // the pre-§7.19 save: replace(" ", "") -- a pasted NBSP survives and Gmail refuses the 19-char "password"
        assertEquals(19, "abcd efgh ijkl mnop".replace(" ", "").length)
    }

    @Test fun shapesCarryNoValue() {
        assertEquals("ulen=13 uat=true udom=true", Creds.userShape("x.y@gmail.com"))
        assertEquals("ulen=5 uat=false udom=false", Creds.userShape("x.y.z"))
        assertEquals("plen=16 p_upper=0 p_other=0", Creds.passShape("abcdefghijklmnop"))
        assertEquals("plen=17 p_upper=1 p_other=1", Creds.passShape("Abcdefghijklmnop1"))
    }

    @Test fun scrubKeepsGmailWordsDropsValues() {
        val u = "someone@gmail.com"; val p = "abcdefghijklmnop"
        assertEquals("(AUTHENTICATIONFAILED) Invalid credentials (Failure)",
            Creds.scrub("[AUTHENTICATIONFAILED] Invalid credentials (Failure)", u, p))
        val s = Creds.scrub("[ALERT] Please log in via your web browser: https://support.google.com/mail/x (Failure) user someone@gmail.com pw abcdefghijklmnop", u, p)
        assertFalse(s.contains("someone")); assertFalse(s.contains(p)); assertFalse(s.contains("support.google"))
        assertTrue(s.startsWith("(ALERT) Please log in via your web browser"))
        assertEquals("-", Creds.scrub(null, u, p))
    }
}
