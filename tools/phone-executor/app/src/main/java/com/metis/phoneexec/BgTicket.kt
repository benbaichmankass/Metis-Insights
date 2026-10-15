package com.metis.phoneexec

/**
 * BACKGROUND TICKETS (PHONE-FGS-EXEC; operator 2026-10-15 "Phone fills in background").
 *
 * MEASURED 2026-10-10: a waiting ticket brought MainActivity forward, and Android's background-activity-start limits can
 * refuse that ("bring-to-front already requested"), so a ticket waited until the operator opened the app. The login checks,
 * re-login and account read already ran in the background engine (BgWeb); this moves the ticket there too: pending peek,
 * claim, fill, every read-back and the server verify / go-token run on the engine's page, with no bring-to-front.
 *
 * The old path stays as the FALLBACK, never as a halt (operator 2026-10-09 "There is no halting"):
 *  - the engine is unavailable (no overlay window / two jobs in a row with no answer) -> bring-to-front, as before;
 *  - an engine job reached a logged-in page but no claim (no terminal) -> bring-to-front for this ticket, and the
 *    engine is retried for tickets after [NO_CLAIM_RETRY_MS];
 *  - a ticket the engine claimed ended refused / unconfirmed / mid-fill -> the next tickets go to the front for
 *    [BAD_RESULT_RETRY_MS] (one bg_ticket_fallback event says so), then the engine is tried again.
 * A claimed ticket is one attempt only, wherever it runs; that rule is unchanged.
 *
 * Pure decisions only, so they are unit-tested on the JVM (BgTicketTest).
 */
object BgTicket {
    const val NO_CLAIM_RETRY_MS = 10 * 60_000L
    const val BAD_RESULT_RETRY_MS = 6 * 3_600_000L

    enum class Route { ENGINE, FRONT }

    /** Where a waiting ticket runs. [engineNoAnswer] = consecutive engine jobs whose page never answered. */
    fun route(now: Long, engineNoAnswer: Int, engineOffUntilMs: Long): Route =
        if (engineNoAnswer >= 2 || now < engineOffUntilMs) Route.FRONT else Route.ENGINE

    /** Whether one background attempt counts AGAINST the engine. [kind] "entry" | "amend"; [ledgerState] = the ledger's
     *  last state for the claimed id (null = nothing claimed). An entry ticket that ends refused, unconfirmed, contained
     *  or mid-fill (intended / submitted left as the last state) counts. An amend counts only when the page failed it
     *  (mid-fill or unconfirmed): amend refusals are expected while the sheet edit is unmeasured (SHEET_EDIT_MEASURED). */
    fun countsAgainst(kind: String, ledgerState: String?): Boolean = when (ledgerState) {
        null, "dry_filled", "placed", "skipped", "amended", "dry_amended", "no_position", "human_moved" -> false
        "refused", "contained" -> kind != "amend"
        else -> true   // intended / submitted (stopped mid-attempt), unconfirmed, anything unknown
    }
}
