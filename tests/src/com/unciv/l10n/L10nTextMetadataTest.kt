package com.unciv.l10n

import com.badlogic.gdx.Gdx
import com.badlogic.gdx.Graphics
import com.badlogic.gdx.scenes.scene2d.Actor
import com.unciv.UncivGame
import com.unciv.models.translations.TranslationEntry
import com.unciv.models.translations.getPlaceholderText
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.mockito.Mockito.mock
import org.mockito.Mockito.mockConstruction
import java.io.File

/** Exercise the actual bind path that crashed on Android, including its v2 guard. */
class L10nTextMetadataTest {
    private val currentField = UncivGame::class.java.getDeclaredField("Current").apply { isAccessible = true }
    private var previousCurrent: Any? = null
    private var previousGraphics: Graphics? = null
    private lateinit var game: UncivGame

    @Before fun setup() {
        previousCurrent = currentField.get(null)
        previousGraphics = Gdx.graphics
        Gdx.graphics = mock(Graphics::class.java)
        game = UncivGame()
        UncivGame.Current = game
        for (key in listOf("Gold", "Science", "Göld Ж", "Gold\nScience", "A + [] (x)?"))
            game.translations[key] = TranslationEntry(key)
        enableProtocol("2")
    }

    @After fun restore() {
        currentField.set(null, previousCurrent)
        Gdx.graphics = previousGraphics
    }

    private fun enableProtocol(protocol: String?) {
        game.l10nHook = L10nScreenHook(game, 1271048, File("../../build/issue47-fix-evidence")).also {
            // Enqueue suffices to activate metadata collection before screen construction.
            // No render, load, filesystem output or device operation is invoked.
            it.enqueue("options-gameplay", "metadata-test", null, protocol)
        }
    }

    private fun bind(source: String, translated: String = "translated input"): L10nTextMetadata.Source {
        val actor = Actor()
        L10nTextMetadata.bind(actor, source, translated)
        return L10nTextMetadata.get(actor, translated) ?: error("metadata_not_bound")
    }

    @Test fun blankAndUnbracedInputsStillBindWithoutCompilingAComponentPattern() {
        for (source in listOf("", "unknown", "literal } text", "unfinished {")) {
            val result = bind(source, "")
            assertEquals("", result.input)
            assertNull(result.key)
            assertTrue(result.keys.isEmpty())
        }
    }

    @Test fun bindingDoesNotCompileRuntimeRegularExpressions() {
        // Initialize the existing translation helpers' static patterns first. Any
        // subsequent Regex construction by metadata is the portability regression.
        "".getPlaceholderText()
        mockConstruction(Regex::class.java) { _, _ -> error("unexpected_dynamic_regex") }.use {
            assertTrue(bind("").keys.isEmpty())
            assertEquals(listOf("Gold"), bind("{Gold}").keys)
        }
    }

    @Test fun resolvesOnlyForwardKnownComponentsAndPreservesTheirOccurrenceOrder() {
        val result = bind("{Science}: {Gold} / {Unknown} + {Gold}", "Wissenschaft: Gold")
        assertNull(result.key)
        assertEquals(listOf("Science", "Gold", "Gold"), result.keys)
        assertEquals("Wissenschaft: Gold", result.input)
    }

    @Test fun literalParsingHandlesEmptyUnmatchedAndNestedBracesWithoutGuessingOuterKeys() {
        val cases = linkedMapOf(
            "{} { }" to emptyList(),
            "{Gold" to emptyList(),
            "Gold}" to emptyList(),
            "{{Gold}}" to listOf("Gold"),
            "{Gold {Science} remainder}" to listOf("Science"),
            "{{Gold}{Science}}" to listOf("Gold", "Science"),
            "}{Gold{Science}" to listOf("Science"),
            "｛Gold｝" to emptyList()
        )
        for ((source, expected) in cases) assertEquals(source, expected, bind(source).keys)
    }

    @Test fun preservesUnicodeNewlinesPunctuationAndPlaceholderLookupSemantics() {
        val result = bind("{Göld Ж}\n{Gold\nScience} {A + [3] (x)?}", "[RED]ü Ж 🦀[]\n42")
        assertEquals(listOf("Göld Ж", "Gold\nScience", "A + [] (x)?"), result.keys)
        assertEquals("[RED]ü Ж 🦀[]\n42", result.input)
    }

    @Test fun aKnownWholeKeyTakesPrecedenceOverItsComponents() {
        game.translations["{Gold} + {Science}"] = TranslationEntry("{Gold} + {Science}")
        val direct = bind("{Gold} + {Science}")
        assertEquals("{Gold} + {Science}", direct.key)
        assertTrue(direct.keys.isEmpty())
        val placeholder = bind("A + [3] (x)?")
        assertEquals("A + [] (x)?", placeholder.key)
        assertTrue(placeholder.keys.isEmpty())
    }

    @Test fun neverReverseMatchesTheTranslatedValueAndRejectsStaleInput() {
        val actor = Actor()
        L10nTextMetadata.bind(actor, "unknown source", "Gold")
        val result = L10nTextMetadata.get(actor, "Gold")!!
        assertNull(result.key)
        assertTrue(result.keys.isEmpty())
        assertNull(L10nTextMetadata.get(actor, "changed text"))
    }

    @Test fun v1AndOrdinaryStartupDoNotCollectMetadata() {
        for (mode in listOf("v1", "ordinary", "uninitialized")) {
            when (mode) {
                "v1" -> enableProtocol(null)
                "ordinary" -> game.l10nHook = null
                else -> currentField.set(null, null)
            }
            val actor = Actor()
            L10nTextMetadata.bind(actor, "{Gold}", "Gold")
            assertNull(mode, L10nTextMetadata.get(actor, "Gold"))
        }
    }
}
