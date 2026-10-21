package com.unciv.l10n

import org.junit.Assert.*
import org.junit.Test

class L10nReadyFramesTest {
    @Test fun twoCompletedQuietFramesAreRequired() {
        val gate = L10nReadyFrames()
        assertFalse(gate.rendered(true, listOf(1f)))
        assertTrue(gate.rendered(true, listOf(1f)))
    }

    @Test fun pendingWorkAndLayoutChangesRestartTheGate() {
        val gate = L10nReadyFrames()
        assertFalse(gate.rendered(true, listOf(1f)))
        assertFalse(gate.rendered(false, listOf(1f)))
        assertFalse(gate.rendered(true, listOf(1f)))
        assertFalse(gate.rendered(true, listOf(2f)))
        assertTrue(gate.rendered(true, listOf(2f)))
        gate.reset()
        assertFalse(gate.rendered(true, listOf(2f)))
    }
}
