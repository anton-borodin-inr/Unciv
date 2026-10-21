package com.unciv.l10n

import com.badlogic.gdx.scenes.scene2d.Actor
import com.badlogic.gdx.scenes.scene2d.Group
import com.badlogic.gdx.scenes.scene2d.ui.ScrollPane
import com.unciv.testing.GdxTestRunner
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(GdxTestRunner::class)
class L10nScrollStateTest {
    private fun ui(offsets: String = "{}", anchors: String): Pair<L10nScrollState, ScrollPane> {
        val content = Group().apply {
            setSize(400f, 600f)
            addActor(Actor().apply { name = "l10n-anchor:technology:Mining"; setBounds(200f, 200f, 50f, 40f) })
        }
        val pane = ScrollPane(content, ScrollPane.ScrollPaneStyle()).apply {
            name = "l10n-pane:tech-tree"
            setSize(100f, 100f)
            setScrollingDisabled(false, false)
            validate()
        }
        val root = Group().apply { addActor(pane) }
        val request = L10nRequest.parse("tech-picker", "test-scroll", """{
          "schema_version":1,"save":"fixture","selection":{"technology":"Archery"},
          "tab":null,"expanded_sections":[],"scroll":$offsets,"scroll_to":$anchors,
          "camera":null,"new_game":null
        }""")
        return L10nScrollState(root, request) to pane
    }

    @Test fun resolvesSourceKeyAnchorAndVerifiesItAfterScrolling() {
        val (state, pane) = ui(anchors = """{"tech-tree":{"technology":"Mining","align":"visible"}}""")
        state.apply()
        assertTrue(pane.scrollX > 0f)
        assertTrue(pane.scrollY > 0f)
        state.verify()
    }

    @Test fun unresolvedAnchorCannotTurnIntoACleanCapture() {
        val (state, _) = ui(anchors = """{"tech-tree":{"technology":"Typo","align":"visible"}}""")
        assertThrows(IllegalArgumentException::class.java) { state.apply() }
    }

    @Test fun offsetsOutsideThePaneAreNotSilentlyClamped() {
        val (state, _) = ui("""{"tech-tree":{"x":999999,"y":0}}""", "{}")
        assertThrows(IllegalArgumentException::class.java) { state.apply() }
    }

    @Test fun everyPaneNeedsAnInstruction() {
        val (state, _) = ui(anchors = "{}")
        assertThrows(IllegalArgumentException::class.java) { state.apply() }
    }

    @Test fun laterScrollChangesAreDetectedBeforeReady() {
        val (state, pane) = ui(anchors = """{"tech-tree":{"edge":"start","align":"visible"}}""")
        state.apply()
        pane.scrollY = 10f
        assertThrows(IllegalArgumentException::class.java) { state.verify() }
    }
    @Test fun aChangedContentHeightCanBeReappliedBeforeTheQuietFrameGate() {
        val (state, pane) = ui(anchors = """{"tech-tree":{"technology":"Mining","align":"visible"}}""")
        state.apply()
        pane.actor.height += 100f
        pane.invalidate()
        pane.validate()
        state.apply()
        state.verify()
    }

    @Test fun anOversizedAnchorFailsRatherThanHidingItsClippedEdge() {
        val (state, pane) = ui(anchors = """{"tech-tree":{"technology":"Mining","align":"visible"}}""")
        (pane.actor as Group).children.first().width = 150f
        assertThrows(IllegalArgumentException::class.java) { state.apply() }
    }

}
