package com.metis.phoneexec

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** PHONE-AUTOLOGIN-2: the pure login matcher on SYNTHETIC mails (Breakout's real mail shape is not in the repo). */
class LoginMatchTest {
    private val lineupMail = MailNums.parse(
        "<html><head><style>.a{width:12px}</style></head><body><p>Select this number in the app:</p>" +
            "<div style='font-size:40px'>47</div><p>Expires in 10 minutes.</p><td>Suite 12</td></body></html>", null)
    private val linksMail = MailNums.parse(
        "<p>Tap the number shown on your screen</p><a href=\"https://x.breakoutprop.com/a?t=1&amp;n=12\">12</a>" +
            "<a href=\"https://x.breakoutprop.com/a?t=2\">47</a><a href=\"https://x.breakoutprop.com/a?t=3\"><b>83</b></a>", null)

    @Test fun parseReadsShownNumbersButNotCss() {
        assertEquals(setOf("47"), lineupMail.shown)
        assertEquals(listOf("https://x.breakoutprop.com/a?t=1&n=12"), linksMail.links["12"])
        assertEquals(setOf("12", "47", "83"), linksMail.links.keys)
    }

    @Test fun lineupTapsTheOneChoiceTheMailShows() {
        val d = LoginMatch.decide(listOf("12", "47", "83"), null, listOf(lineupMail))
        assertEquals("tap", d.kind); assertEquals("47", d.value)
    }

    @Test fun lineupWithThreeNumberLinksIsAmbiguous() {
        assertEquals("ambiguous", LoginMatch.decide(listOf("12", "47", "83"), null, listOf(linksMail)).kind)
    }

    @Test fun lineupPhraseTierAndMinutesAreNotANumber() {
        val m = MailNums.parse(null, "Your login number is 83. It expires in 12 min.")
        assertEquals(setOf("83"), m.phrased)
        val d = LoginMatch.decide(listOf("12", "47", "83"), null, listOf(m))
        assertEquals("tap", d.kind); assertEquals("83", d.value)
    }

    @Test fun lineupNoMatchKeepsPolling() {
        val other = MailNums.parse("<p>Welcome to Breakout</p>", null)
        assertEquals("none", LoginMatch.decide(listOf("12", "47", "83"), null, listOf(other)).kind)
    }

    @Test fun newestDecidingMailWins() {
        val d = LoginMatch.decide(listOf("12", "47", "83"), null, listOf(MailNums.parse("<p>news</p>", null), lineupMail))
        assertEquals("47", d.value)
    }

    @Test fun singleNumberOpensTheOneMatchingLink() {
        val d = LoginMatch.decide(emptyList(), "47", listOf(linksMail))
        assertEquals("link", d.kind); assertEquals("https://x.breakoutprop.com/a?t=2", d.value)
        assertEquals("none", LoginMatch.decide(emptyList(), "99", listOf(linksMail)).kind)
    }

    @Test fun pageShapes() {
        assertEquals(listOf("12", "47", "83"), LoginMatch.lineup(listOf("12", "47", "83"), listOf("12", "47", "83")))
        assertEquals(emptyList<String>(), LoginMatch.lineup(listOf("47"), emptyList()))
        assertEquals("47", LoginMatch.single(listOf("47"), emptyList()))
        assertNull(LoginMatch.single(listOf("12", "47"), emptyList()))
    }
}
