package com.unciv.l10n

import java.util.concurrent.atomic.AtomicBoolean
import com.badlogic.gdx.Graphics

/** A request owns its callbacks even before its state has been parsed. Terminal events are GL-thread only. */
class L10nTicket(val screen: String, val token: String, val json: String?,
                 val protocol: String? = null, val fault: String? = null) {
    @Volatile var retired = false
        private set
    var terminal = false
        private set

    fun finish(action: () -> Unit): Boolean {
        if (terminal) return false
        terminal = true
        action()
        return true
    }

    fun retire(action: () -> Unit) {
        retired = true
        finish(action)
    }
}

/** A duplicate/retired completion cannot freeze rendering belonging to the next request. */
fun L10nTicket.finishRendering(graphics: Graphics, action: () -> Unit): Boolean = finish {
    graphics.isContinuousRendering = false
    action()
}

/** The same ownership policy covers queued, loading and drawn requests. */
class L10nRequestQueue(private val error: (L10nTicket, String) -> Unit) {
    val pending = ArrayDeque<L10nTicket>()
    var current: L10nTicket? = null
        private set

    fun enqueue(ticket: L10nTicket) {
        retireAll("superseded")
        pending.addLast(ticket)
    }

    fun retireAll(reason: String) {
        current?.let { owner -> owner.retire { error(owner, reason) } }
        pending.forEach { owner -> owner.retire { error(owner, reason) } }
        pending.clear()
    }

    fun startNext(): L10nTicket = pending.removeFirst().also { current = it }
    fun isCurrent(owner: L10nTicket?) = owner != null && current === owner && !owner.retired && !owner.terminal
}

/** Both background failure and GL completion must converge here, on the GL thread. */
class L10nPendingTask(private val release: () -> Unit, private val failure: (Exception) -> Unit) {
    private val completed = AtomicBoolean()

    fun complete(action: () -> Unit = {}) {
        if (!completed.compareAndSet(false, true)) return
        var error: Exception? = null
        try { action() } catch (ex: Exception) { error = ex }
        finally { release() }
        error?.let(failure)
    }

    fun fail(error: Exception) = complete { throw error }
}
