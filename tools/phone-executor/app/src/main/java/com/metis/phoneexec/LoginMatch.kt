package com.metis.phoneexec

/**
 * PURE login matching (no Android, no I/O; app/src/test/.../LoginMatchTest.kt runs it on the JVM in CI).
 * PHONE-AUTOLOGIN-2 (operator 2026-10-10: "it's a login check that verifies you can choose the correct 2 digit number
 * from a line up of 3"). Two shapes are supported and the PAGE decides which one is in front of us:
 *   LINEUP  the page shows >= 2 numbers to tap; the mail names the right one -> tap the ONE page choice the mail names.
 *   LINK    the page shows ONE number; the mail carries numbered links (the 2026-10-05 measured flow: "tapped the
 *           number link in Breakout's email") -> open the ONE https link whose text is the page's number.
 * Fail closed: zero or more than one candidate is never acted on. Values stay in memory; only counts are reported.
 */
class MailNums(
    /** number -> distinct https links whose visible text is exactly that number */
    val links: Map<String, List<String>>,
    /** numbers shown on their own (a whole element's text, or a whole line of the plain part) */
    val shown: Set<String>,
    /** numbers bound to a phrase ("number", "code", "select", "tap", "choose", "matching") within a few words */
    val phrased: Set<String>,
    /** PHONE-AUTOLOGIN-3 (counts only, for the login_diag): distinct https links in the mail, and how many of them
     *  read as a sign-in action ("log in", "sign in", "verify", "confirm", "approve", "continue", or a login/auth/verify
     *  path) -- the "one magic link, no lineup" shape */
    val allLinks: Int = 0,
    val actLinks: Int = 0,
) {
    companion object {
        private val A_RE = Regex("<a\\b[^>]*href\\s*=\\s*\"([^\"]+)\"[^>]*>(.*?)</a>", setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL))
        private val NUM = Regex("^\\d{1,3}$")
        private val PHRASE = Regex("(?:number|code|select|tap|choose|matching)\\D{0,25}?\\b(\\d{1,3})\\b(?!\\s*(?:min|sec|hour|%))", RegexOption.IGNORE_CASE)

        private val ACT = Regex("log ?in|sign ?in|verify|confirm|approve|continue|magic", RegexOption.IGNORE_CASE)
        private val ACT_HREF = Regex("login|log-in|signin|sign-in|auth|verify|magic|token|confirm", RegexOption.IGNORE_CASE)

        private fun clean(s: String) = s.replace("&nbsp;", " ").replace("&#160;", " ").trim()

        fun parse(html: String?, plain: String?): MailNums {
            val links = HashMap<String, MutableList<String>>()
            val shown = HashSet<String>()
            val all = HashSet<String>(); val act = HashSet<String>()
            var text = ""
            if (html != null) {
                // drop <style>/<script>/<head> so CSS numbers are never read as content
                val h = html.replace(Regex("<(style|script|head)\\b.*?</\\1>", setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL)), " ")
                for (m in A_RE.findAll(h)) {
                    val t = clean(m.groupValues[2].replace(Regex("<[^>]+>"), ""))
                    val href = m.groupValues[1].replace("&amp;", "&")
                    if (href.startsWith("https://")) {
                        all.add(href)
                        if (!NUM.matches(t) && (ACT.containsMatchIn(t) || ACT_HREF.containsMatchIn(href.substringBefore('?')))) act.add(href)
                    }
                    if (NUM.matches(t) && href.startsWith("https://")) {
                        val l = links.getOrPut(t) { mutableListOf() }
                        if (href !in l) l.add(href)
                    }
                }
                for (m in Regex(">([^<]*)<").findAll(h)) { val t = clean(m.groupValues[1]); if (NUM.matches(t)) shown.add(t) }
                text = clean(h.replace(Regex("<[^>]+>"), " ")).replace(Regex("\\s+"), " ")
            }
            if (plain != null) {
                for (line in plain.lines()) { val t = clean(line); if (NUM.matches(t)) shown.add(t) }
                if (text.isEmpty()) text = plain.replace(Regex("\\s+"), " ")
            }
            val phrased = PHRASE.findAll(text).map { it.groupValues[1] }.toSet()
            return MailNums(links, shown, phrased, all.size, act.size)
        }
    }
}

object LoginMatch {
    /** PHONE-AUTOLOGIN-3: the shape of the newest Breakout mail, for the diag only (never acted on here):
     *  "none" (no mail), "numbered_links" (>= 1 https link whose text is a number), "numbers" (numbers shown, no
     *  number links), "one_login_link" (exactly one sign-in link and no number to match: the magic-link shape),
     *  "links" (other links only), "text" (no links, no numbers). */
    fun mailShape(newest: MailNums?): String = when {
        newest == null -> "none"
        newest.links.isNotEmpty() -> "numbered_links"
        newest.shown.isNotEmpty() || newest.phrased.isNotEmpty() -> "numbers"
        newest.actLinks == 1 -> "one_login_link"
        newest.allLinks > 0 -> "links"
        else -> "text"
    }

    /** kind: "tap" (value = the page choice to tap), "link" (value = the link to open), "none" (nothing matched
     *  yet: keep polling), "ambiguous" (more than one candidate: fail closed). n = candidates in the deciding mail. */
    class Decision(val kind: String, val value: String?, val n: Int, val tier: String = "")

    /** The page's lineup: tappable choices when there are >= 2, else the >= 2 numbers at the top font size. */
    fun lineup(top: List<String>, btn: List<String>): List<String> = when {
        btn.distinct().size >= 2 -> btn.distinct()
        top.distinct().size >= 2 -> top.distinct()
        else -> emptyList()
    }

    /** The page's ONE number (the LINK shape), or null. */
    fun single(top: List<String>, btn: List<String>): String? =
        if (top.distinct().size == 1 && btn.distinct().size < 2) top[0] else null

    /** [mails] newest first. The newest mail that decides, decides; an older mail never overrides a newer one. */
    fun decide(choices: List<String>, single: String?, mails: List<MailNums>): Decision {
        if (choices.size >= 2) {
            for (m in mails) {
                // tier 1: numbers the mail shows on their own (incl. a lone number link); tier 2: phrase-bound numbers
                val t1 = choices.filter { it in m.shown || it in m.links.keys }
                if (t1.size == 1) return Decision("tap", t1[0], 1, "shown")
                if (t1.size > 1) return Decision("ambiguous", null, t1.size, "shown")
                val t2 = choices.filter { it in m.phrased }
                if (t2.size == 1) return Decision("tap", t2[0], 1, "phrase")
                if (t2.size > 1) return Decision("ambiguous", null, t2.size, "phrase")
            }
            return Decision("none", null, 0)
        }
        if (single != null) {
            for (m in mails) {
                val hrefs = m.links[single] ?: continue
                if (hrefs.size == 1) return Decision("link", hrefs[0], 1, "link")
                return Decision("ambiguous", null, hrefs.size, "link")
            }
        }
        return Decision("none", null, 0)
    }
}
