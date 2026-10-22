package com.unciv.l10n

import com.badlogic.gdx.utils.JsonReader
import org.junit.Assert.*
import org.junit.Test
import java.nio.file.Files
import java.nio.file.Paths
import java.util.Locale

class L10nDumpTest {
    private val screen = L10nRect(0.0, 0.0, 2400.0, 1080.0)
    private fun quad(left: Double, top: Double, width: Double = 10.0) = listOf(
        L10nPoint(left, top), L10nPoint(left + width, top), L10nPoint(left + width, top + 20), L10nPoint(left, top + 20))
    private fun element(glyphs: List<L10nDrawGlyph> = listOf(L10nDrawGlyph("ü", quad(10.0, 10.0), 1.0, 0)),
                        scissor: L10nRect? = null, bounds: L10nRect? = screen) = L10nDrawElement(
        "root/0:Label/draw-0", "root/0:Label", "root/0:Label", "Example", emptyList(), "ü hidden suffix",
        bounds, glyphs, screen, scissor, true, listOf(0.18, 0.18), listOf(0.36, 0.36))

    @Test fun partialAndHiddenGlyphsRetainOnlyTheirActualDrawnStringsAndGeometry() {
        val data = element(listOf(
            L10nDrawGlyph("ü", quad(10.0, 10.0), 1.0, 0),
            L10nDrawGlyph("Ж", quad(20.0, 10.0), 1.0, 0),
            L10nDrawGlyph("!", quad(30.0, 10.0), 1.0, 0)), L10nRect(0.0, 0.0, 25.0, 1080.0)).json()
        assertEquals("üЖ!", data["display_text"])
        assertEquals("ü hidden suffix", data["input_text"])
        assertEquals("partial", data["visibility"])
        assertEquals("ancestor_scissor", data["clip_cause"])
        assertEquals(0.5, data["clipped_fraction"] as Double, 0.0)
        val parsed = JsonReader().parse(L10nCanonicalJson.bytes(data).toString(Charsets.UTF_8))
        val glyphs = parsed.get("lines").get(0).get("glyphs")
        assertEquals("full", glyphs.get(0).getString("visibility"))
        assertEquals("partial", glyphs.get(1).getString("visibility"))
        assertEquals("hidden", glyphs.get(2).getString("visibility"))
        assertTrue(glyphs.get(2).get("visible_bounds").isNull)
        assertEquals(25.0, glyphs.get(1).get("visible_bounds").getDouble(2), 0.0)
    }

    @Test fun areaMeasurementUsesRotatedQuadsAndScreenAndScissorIntersection() {
        val diamond = listOf(L10nPoint(-10.0, 0.0), L10nPoint(0.0, -10.0),
            L10nPoint(10.0, 0.0), L10nPoint(0.0, 10.0))
        val data = element(listOf(L10nDrawGlyph("A", diamond, 1.0, 0)), L10nRect(0.0, 0.0, 5.0, 50.0)).json()
        assertEquals("mixed", data["clip_cause"])
        assertEquals(0.8125, data["clipped_fraction"] as Double, 0.0)
        assertTrue(data["overflow_x"] as Boolean)
        assertTrue(data["overflow_y"] as Boolean)
    }

    @Test fun alphaZeroAndFullyClippedTextNeverBecomeVisible() {
        val zero = element(listOf(L10nDrawGlyph("secret", quad(10.0, 10.0), 0.0, 0))).json()
        assertFalse(zero["drawn"] as Boolean)
        assertEquals("hidden", zero["visibility"])
        assertNull(zero["visible_bounds"])
        val clipped = element(scissor = L10nRect(500.0, 500.0, 600.0, 600.0)).json()
        assertEquals(1.0, clipped["clipped_fraction"] as Double, 0.0)
        assertEquals("hidden", clipped["visibility"])
        assertEquals("ü", clipped["display_text"])
    }

    @Test fun repeatedAndDecorativeDrawsRetainDistinctIdsAndExplicitLogicalGrouping() {
        val ordinary = element()
        val shadow = ordinary.copy(id = "root/0:Label/draw-1", decoration = true)
        val other = ordinary.copy(id = "root/1:Label/draw-0", logicalId = "root/1:Label")
        val rows = listOf(ordinary, shadow, other).map { it.json() }
        assertEquals(3, rows.map { it["id"] }.distinct().size)
        assertEquals(rows[0]["logical_id"], rows[1]["logical_id"])
        assertNotEquals(rows[0]["logical_id"], rows[2]["logical_id"])
        assertEquals(true, rows[1]["decoration"])
    }

    @Test fun wrappedAndExplicitBlankLinesRetainOrderAndUnknownOwnerIsExplicit() {
        val data = element(listOf(L10nDrawGlyph("A", quad(10.0, 10.0), 1.0, 0),
            L10nDrawGlyph("B", quad(10.0, 60.0), 1.0, 2)), bounds = null).copy(actorPath = null,
                emptyLineBounds = mapOf(1 to L10nRect(10.0, 40.0, 10.0, 40.0))).json()
        assertEquals("A\n\nB", data["display_text"])
        assertNull(data["bounds"])
        assertTrue((data["warnings"] as List<*>).contains("unknown_owner"))
        val parsed = JsonReader().parse(L10nCanonicalJson.bytes(data).toString(Charsets.UTF_8))
        assertEquals(40.0, parsed.get("lines").get(1).get("bounds").getDouble(1), 0.0)
    }

    @Test fun canonicalBytesAreLocaleIndependentAndDoNotRoundGeometry() {
        val original = Locale.getDefault()
        try {
            val expected = L10nCanonicalJson.bytes(element().json())
            Locale.setDefault(Locale.GERMANY)
            assertArrayEquals(expected, L10nCanonicalJson.bytes(element().json()))
            assertEquals('\n'.code.toByte(), expected.last())
            assertNotEquals(l10nSha256(expected), l10nSha256(L10nCanonicalJson.bytes(element().copy(
                ownerBounds = L10nRect(0.000000001, 0.0, 2400.0, 1080.0)).json())))
            assertThrows(IllegalArgumentException::class.java) { L10nCanonicalJson.bytes(Double.NaN) }
            assertThrows(IllegalArgumentException::class.java) { L10nCanonicalJson.bytes(Double.POSITIVE_INFINITY) }
            assertEquals("\"ü Ж 🦀\"\n", L10nCanonicalJson.bytes("ü Ж 🦀").toString(Charsets.UTF_8))
            assertThrows(java.nio.charset.CharacterCodingException::class.java) { L10nCanonicalJson.bytes("\uD800") }
        } finally { Locale.setDefault(original) }
    }

    @Test fun atomicPublisherRefusesBothFinalAndTemporaryCollisions() {
        val evidence = Paths.get("../../build/issue47-fix-evidence")
        Files.createDirectories(evidence)
        val directory = Files.createTempDirectory(evidence, "publisher-").toFile()
        val publisher = L10nDumpPublisher(directory)
        val bytes = L10nCanonicalJson.bytes(element().json())
        val first = publisher.publish("one", bytes)
        assertEquals("files/l10n-hook/one.json", first.path)
        assertEquals(l10nSha256(bytes), first.sha256)
        assertArrayEquals(bytes, directory.resolve("l10n-hook/one.json").readBytes())
        assertFalse(directory.resolve("l10n-hook/one.json.tmp").exists())
        assertThrows(IllegalStateException::class.java) { publisher.publish("one", byteArrayOf()) }
        directory.resolve("l10n-hook/two.json.tmp").writeText("retained temporary")
        assertThrows(IllegalStateException::class.java) { publisher.publish("two", byteArrayOf()) }
        assertThrows(IllegalArgumentException::class.java) { publisher.publish("../escape", bytes) }
        assertArrayEquals(bytes, directory.resolve("l10n-hook/one.json").readBytes())
    }

    @Test fun protocolStateDigestUsesExactSuppliedBytesAndIgnoresRequestTokens() {
        val json = """{"schema_version":1,"save":"issue-8-pair","selection":{"technology":"Mining"},"tab":null,"expanded_sections":[],"scroll":{},"scroll_to":{},"camera":null,"new_game":null}"""
        val first = L10nRequest.parse("tech-picker", "first", json, "2")
        val second = L10nRequest.parse("tech-picker", "second", json, "2")
        val frame = L10nDrawObserver.Frame(listOf(2400, 1080), screen, listOf(element()))
        val bytes = frame.dump(1271048, first, "German")
        assertArrayEquals(bytes, frame.dump(1271048, second, "German"))
        assertEquals(l10nSha256(json.toByteArray(Charsets.UTF_8)), first.stateSha256)
        assertNotEquals(first.stateSha256, L10nRequest.parse("tech-picker", "space", "$json ", "2").stateSha256)
        assertEquals(1, L10nRequest.parse("tech-picker", "old", json).protocolVersion)
        assertThrows(IllegalArgumentException::class.java) { L10nRequest.parse("tech-picker", "bad", json, "3") }
        Files.createDirectories(Paths.get("../../build/issue47-fix-evidence"))
        Files.write(Paths.get("../../build/issue47-fix-evidence/sample.texts.json"), bytes)
    }
}
