package com.unciv.l10n

import org.junit.Assert.*
import org.junit.Test
import com.badlogic.gdx.Graphics
import com.badlogic.gdx.graphics.g2d.BitmapFont
import com.unciv.UncivGame
import com.unciv.ui.components.fonts.Fonts
import org.mockito.Mockito.*

class L10nWorkTest {
    @Test fun readyAndErrorFreezeRenderingAndLateCompletionCannotFreezeTheNextRequest() {
        for (event in listOf("ready", "error")) {
            val graphics = mock(Graphics::class.java)
            val ticket = L10nTicket("world", event, null)
            var emitted = 0
            ticket.finishRendering(graphics) { emitted++ }
            verify(graphics).setContinuousRendering(false)
            graphics.isContinuousRendering = true // the next enqueue wakes rendering
            clearInvocations(graphics)
            ticket.finishRendering(graphics) { emitted++ }
            verifyNoInteractions(graphics)
            assertEquals(1, emitted)
        }
    }

    @Test fun fontRetirementDoesNotAccessAnUninitializedCurrentGame() {
        val current = UncivGame::class.java.getDeclaredField("Current").apply { isAccessible = true }
        val saved = current.get(null)
        try {
            current.set(null, null)
            assertFalse(UncivGame.isCurrentInitialized())
            Fonts.retireFontForHook(mock(BitmapFont::class.java))
        } finally { current.set(null, saved) }
    }

    @Test fun queuedAndLoadingRequestsAreSupersededExactlyOnce() {
        val events = ArrayList<String>()
        val queue = L10nRequestQueue { owner, reason -> events.add("${owner.token}:$reason") }
        val queued = L10nTicket("world", "queued", null)
        val loading = L10nTicket("world", "loading", null)
        val next = L10nTicket("world", "next", null)
        queue.enqueue(queued)
        queue.enqueue(loading)
        assertEquals(loading, queue.startNext())
        queue.enqueue(next)
        assertFalse(queue.isCurrent(loading))
        assertFalse(loading.finish { events.add("late-ready") })
        assertEquals(next, queue.startNext())
        assertTrue(queue.isCurrent(next))
        // A late success or failure cannot claim the next request's ownership.
        if (queue.isCurrent(loading)) next.finish { events.add("wrong-failure") }
        next.finish { events.add("next:ready") }
        queue.retireAll("normal_launch_requested")
        assertEquals(listOf("queued:superseded", "loading:superseded", "next:ready"), events)
    }

    @Test fun normalLaunchRetiresBothPendingAndActiveAndRejectsLateCallbacks() {
        val events = ArrayList<String>()
        val queue = L10nRequestQueue { owner, reason -> events.add("${owner.token}:$reason") }
        queue.enqueue(L10nTicket("world", "active", null)); val active = queue.startNext()
        queue.enqueue(L10nTicket("world", "pending", null))
        queue.retireAll("normal_launch_requested")
        queue.retireAll("normal_launch_requested")
        assertEquals(listOf("active:superseded", "pending:normal_launch_requested"), events)
        assertFalse(queue.isCurrent(active))
        assertTrue(queue.pending.isEmpty())
    }

    @Test fun backgroundAndGlFailureReleasePendingWorkBeforeReportingTheActualCause() {
        for (background in listOf(true, false)) {
            var pending = 1; var callbackRuns = 0
            val errors = ArrayList<String>()
            val completion = L10nPendingTask({ pending-- }) {
                assertEquals(0, pending); errors.add(it.message!!)
            }
            if (background) completion.fail(IllegalStateException("background_cause"))
            else completion.complete { callbackRuns++; error("gl_callback_cause") }
            completion.complete { callbackRuns++ }
            completion.fail(IllegalStateException("late_failure"))
            assertEquals(0, pending)
            assertEquals(if (background) 0 else 1, callbackRuns)
            assertEquals(listOf(if (background) "background_cause" else "gl_callback_cause"), errors)
        }
    }

    @Test fun successfulCompletionReleasesOnlyOnceAndDoesNotSwallowOrdinaryFailure() {
        var pending = 1
        val success = L10nPendingTask({ pending-- }) { throw it }
        success.complete(); success.complete()
        assertEquals(0, pending)
        pending++
        val failure = L10nPendingTask({ pending-- }) { throw it }
        val error = assertThrows(IllegalArgumentException::class.java) {
            failure.complete { throw IllegalArgumentException("ordinary_failure") }
        }
        assertEquals("ordinary_failure", error.message)
        assertEquals(0, pending)
    }
}
