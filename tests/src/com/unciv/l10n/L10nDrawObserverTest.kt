package com.unciv.l10n

import com.badlogic.gdx.Gdx
import com.badlogic.gdx.Graphics
import com.badlogic.gdx.graphics.GL20
import com.badlogic.gdx.graphics.Texture
import com.badlogic.gdx.graphics.g2d.Batch
import com.badlogic.gdx.graphics.g2d.BitmapFont
import com.badlogic.gdx.graphics.g2d.GlyphLayout.GlyphRun
import com.badlogic.gdx.graphics.g2d.TextureRegion
import com.badlogic.gdx.math.Matrix4
import com.badlogic.gdx.math.Rectangle
import com.badlogic.gdx.scenes.scene2d.Group
import com.badlogic.gdx.scenes.scene2d.Stage
import com.badlogic.gdx.scenes.scene2d.ui.Label
import com.badlogic.gdx.scenes.scene2d.ui.Skin
import com.badlogic.gdx.scenes.scene2d.ui.SelectBox
import com.badlogic.gdx.scenes.scene2d.ui.ScrollPane
import com.badlogic.gdx.scenes.scene2d.ui.TextField
import com.badlogic.gdx.scenes.scene2d.utils.BaseDrawable
import com.badlogic.gdx.graphics.Color
import com.unciv.ui.components.widgets.L10nSelectBox
import com.unciv.ui.components.widgets.TextFieldWithFixes
import com.badlogic.gdx.scenes.scene2d.utils.ScissorStack
import com.badlogic.gdx.utils.Align
import com.badlogic.gdx.utils.viewport.StretchViewport
import com.unciv.testing.GdxTestRunner
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.mockito.ArgumentMatchers.*
import org.mockito.Mockito.*
import java.nio.IntBuffer

/** Real libGDX layout/cache/stage, with only GL and texture submission mocked. */
@RunWith(GdxTestRunner::class)
class L10nDrawObserverTest {
    private lateinit var oldGraphics: Graphics
    private lateinit var oldGl: GL20
    private lateinit var stage: Stage
    private lateinit var font: BitmapFont
    private val submissions = ArrayList<FloatArray>()
    private var scissorEnabled = false
    private var scissor = intArrayOf(0, 0, 2400, 1080)

    private class TracedData : BitmapFont.BitmapFontData(), L10nTracedFontData {
        override val l10nLayoutTrace = L10nGlyphLayoutTrace { true }
        init {
            lineHeight = 10f; capHeight = 10f; down = -10f; ascent = 0f
            spaceXadvance = 5f; markupEnabled = true
            for (char in ((32..126).map { it.toChar() } + listOf('ü', 'Ж', '…'))) {
                val glyph = BitmapFont.Glyph().apply {
                    id = char.code; width = if (char == ' ') 5 else 10
                    height = 10; xadvance = width; page = 0
                }
                setGlyph(char.code, glyph)
            }
        }
        override fun getGlyphs(run: GlyphRun, str: CharSequence, start: Int, end: Int, lastGlyph: BitmapFont.Glyph?) {
            l10nLayoutTrace.glyphs(run) { super.getGlyphs(run, str, start, end, lastGlyph) }
        }
    }

    @Before fun setup() {
        oldGraphics = Gdx.graphics; oldGl = Gdx.gl
        Gdx.graphics = mock(Graphics::class.java).also {
            `when`(it.width).thenReturn(600); `when`(it.height).thenReturn(300)
            `when`(it.backBufferWidth).thenReturn(2400); `when`(it.backBufferHeight).thenReturn(1080)
        }
        Gdx.gl = mock(GL20::class.java); Gdx.gl20 = Gdx.gl
        doAnswer { call ->
            val values = when (call.getArgument<Int>(0)) {
                GL20.GL_VIEWPORT -> intArrayOf(0, 0, 2400, 1080)
                GL20.GL_SCISSOR_BOX -> scissor
                else -> intArrayOf(0)
            }
            val buffer = call.getArgument<IntBuffer>(1)
            values.forEachIndexed { index, value -> buffer.put(index, value) }
            null
        }.`when`(Gdx.gl).glGetIntegerv(anyInt(), any(IntBuffer::class.java))
        `when`(Gdx.gl.glIsEnabled(GL20.GL_SCISSOR_TEST)).thenAnswer { scissorEnabled }
        doAnswer { scissorEnabled = true; null }.`when`(Gdx.gl).glEnable(GL20.GL_SCISSOR_TEST)
        doAnswer { scissorEnabled = false; null }.`when`(Gdx.gl).glDisable(GL20.GL_SCISSOR_TEST)
        doAnswer { call -> scissor = IntArray(4) { call.getArgument(it) }; null }
            .`when`(Gdx.gl).glScissor(anyInt(), anyInt(), anyInt(), anyInt())
        val batch = mock(Batch::class.java)
        val projection = Matrix4(); val transform = Matrix4()
        `when`(batch.projectionMatrix).thenReturn(projection)
        `when`(batch.transformMatrix).thenReturn(transform)
        doAnswer { projection.set(it.getArgument<Matrix4>(0)); null }.`when`(batch).setProjectionMatrix(any(Matrix4::class.java))
        doAnswer { transform.set(it.getArgument<Matrix4>(0)); null }.`when`(batch).setTransformMatrix(any(Matrix4::class.java))
        doAnswer { call ->
            val vertices = call.getArgument<FloatArray>(1)
            val offset = call.getArgument<Int>(2); val count = call.getArgument<Int>(3)
            submissions.add(vertices.copyOfRange(offset, offset + count)); null
        }.`when`(batch).draw(any(Texture::class.java), any(FloatArray::class.java), anyInt(), anyInt())
        stage = Stage(StretchViewport(600f, 300f), batch)
        stage.viewport.update(600, 300, true)
        val texture = mock(Texture::class.java)
        `when`(texture.width).thenReturn(256); `when`(texture.height).thenReturn(256)
        font = L10nBitmapFont(TracedData(), com.badlogic.gdx.utils.Array.with(TextureRegion(texture)))
        font.setUseIntegerPositions(false)
    }

    @After fun restore() {
        stage.dispose()
        Gdx.graphics = oldGraphics; Gdx.gl = oldGl; Gdx.gl20 = oldGl
    }

    private fun label(text: String, width: Float = 100f): Label = Label(text, Label.LabelStyle(font, null)).apply {
        setBounds(10f, 20f, width, 30f); setAlignment(Align.left)
        this@L10nDrawObserverTest.stage.addActor(this)
    }

    private fun draw(): L10nDrawObserver.Frame = L10nDrawObserver.collect(stage) { stage.draw() }

    @Test fun recordsActualEllipsisEvenWhenItReplacesAnIdenticalLiteralSuffix() {
        val label = label("...", 15f)
        label.setEllipsis(true)
        val row = draw().elements.single().json()
        assertEquals("...", row["input_text"])
        assertEquals("...", row["display_text"])
        assertEquals(true, row["ellipsis_applied"])
        label.setSize(100f, 30f)
        val unshortened = draw().elements.single().json()
        assertEquals("...", unshortened["display_text"])
        assertEquals(false, unshortened["ellipsis_applied"])
    }

    @Test fun unicodeMarkupAndHiddenSuffixComeFromFinalLayoutRatherThanLabelText() {
        val label = label("[RED]üЖ[] long suffix", 50f)
        label.setEllipsis(true)
        val row = draw().elements.single().json()
        assertEquals("[RED]üЖ[] long suffix", row["input_text"])
        assertFalse((row["display_text"] as String).contains("suffix"))
        assertFalse((row["display_text"] as String).contains("[RED]"))
        assertTrue((row["display_text"] as String).endsWith("..."))
        assertEquals(true, row["ellipsis_applied"])
    }

    @Test fun glyphAndOwnerBoxesUsePhysicalPixelsAndTopLeftOrigin() {
        label("A")
        val row = draw().elements.single().json()
        val owner = row["bounds"] as List<*>
        val glyph = row["glyph_bounds"] as List<*>
        assertEquals(40.0, owner[0] as Double, 0.001)
        assertEquals(900.0, owner[1] as Double, 0.001)
        assertEquals(440.0, owner[2] as Double, 0.001)
        assertEquals(1008.0, owner[3] as Double, 0.001)
        assertEquals(40.0, glyph[0] as Double, 0.001)
        assertEquals(40.0, (glyph[2] as Double) - (glyph[0] as Double), 0.001)
        val scale = row["pixel_scale"] as List<*>
        assertEquals(4.0, scale[0] as Double, 0.001)
        assertEquals(3.6, scale[1] as Double, 0.001)
    }

    @Test fun nestedScissorsAreObservedAtSubmissionAndDoNotCompletePartialGlyphs() {
        val outer = object : Group() {
            override fun draw(batch: Batch, parentAlpha: Float) {
                assertTrue(ScissorStack.pushScissors(Rectangle(0f, 0f, 100f, 300f)))
                try { super.draw(batch, parentAlpha) } finally { ScissorStack.popScissors() }
            }
        }
        val inner = object : Group() {
            override fun draw(batch: Batch, parentAlpha: Float) {
                assertTrue(ScissorStack.pushScissors(Rectangle(10f, 0f, 15f, 300f)))
                try { super.draw(batch, parentAlpha) } finally { ScissorStack.popScissors() }
            }
        }
        stage.addActor(outer); outer.addActor(inner)
        inner.addActor(label("AB"))
        val row = draw().elements.single().json()
        assertEquals("partial", row["visibility"])
        assertEquals("ancestor_scissor", row["clip_cause"])
        val clip = row["clip_bounds"] as List<*>
        assertEquals(40.0, clip[0] as Double, 0.001)
        assertEquals(100.0, clip[2] as Double, 0.001)
        assertEquals(0.25, row["clipped_fraction"] as Double, 0.001)
        assertFalse(scissorEnabled)
    }

    @Test fun observationForwardsExactlyTheSameSubmittedVerticesAndNeverWarmsGlyphs() {
        val label = label("üЖ A")
        stage.draw()
        val ordinary = submissions.map { it.copyOf() }
        submissions.clear()
        val before = label.glyphLayout.runs.flatMap { it.glyphs.toList() }
        draw()
        assertEquals(ordinary.size, submissions.size)
        ordinary.indices.forEach { assertArrayEquals(ordinary[it], submissions[it], 0f) }
        assertEquals(before, label.glyphLayout.runs.flatMap { it.glyphs.toList() })
        assertFalse(label.needsLayout())
    }

    @Test fun explicitLeadingTrailingBlankLinesAndWrappingKeepRenderedLineOrder() {
        val label = label("\nA\n\nB\n", 100f)
        assertEquals("\nA\n\nB\n", draw().elements.single().json()["display_text"])
        label.setText("AA BB CC"); label.setSize(30f, 100f); label.setWrap(true)
        val row = draw().elements.single().json()
        assertEquals("AA\nBB\nCC", row["display_text"])
        assertEquals(false, row["ellipsis_applied"])
    }

    @Test fun zeroAlphaOwnerIsMarkedHiddenAndActorTransformsAffectTheGlyphs() {
        val group = Group().apply { setPosition(100f, 40f); setScale(2f); setSize(200f, 200f) }
        stage.addActor(group)
        val label = label("A"); group.addActor(label)
        val row = draw().elements.single().json()
        val scale = row["pixel_scale"] as List<*>
        assertEquals(8.0, scale[0] as Double, 0.001)
        assertEquals(7.2, scale[1] as Double, 0.001)
        label.color.a = 0f
        val hidden = draw().elements.single().json()
        assertEquals("hidden", hidden["visibility"])
        assertFalse(hidden["drawn"] as Boolean)
        assertNull(hidden["visible_bounds"])
    }

    @Test fun selectedValueAndNumericFieldDirectFontDrawsHaveOwnersAndActualRanges() {
        val listStyle = com.badlogic.gdx.scenes.scene2d.ui.List.ListStyle(font, Color.WHITE, Color.WHITE, BaseDrawable())
        val skin = Skin().apply {
            add("default", SelectBox.SelectBoxStyle(font, Color.WHITE, BaseDrawable(), ScrollPane.ScrollPaneStyle(), listStyle))
        }
        val selected = L10nSelectBox<String>(skin).apply {
            setItems("12", "42"); setSelected("42"); setBounds(10f, 20f, 100f, 30f)
        }
        stage.addActor(selected)
        val first = draw().elements.single().json()
        assertEquals("42", first["display_text"])
        assertNotNull(first["actor_path"])
        assertNotNull(first["bounds"])
        selected.remove()
        val constructor = TextFieldWithFixes::class.java.getDeclaredConstructor(String::class.java, TextField.TextFieldStyle::class.java)
            .apply { isAccessible = true }
        val field = constructor.newInstance("123456789", TextField.TextFieldStyle().apply { font = this@L10nDrawObserverTest.font; fontColor = Color.WHITE })
        field.setBounds(10f, 20f, 30f, 30f)
        stage.addActor(field)
        val number = draw().elements.single().json()
        assertEquals("123456789", number["input_text"])
        assertNotEquals(number["input_text"], number["display_text"])
        assertTrue("123456789".contains(number["display_text"] as String))
        assertNotNull(number["bounds"])
        assertFalse((number["warnings"] as List<*>).contains("unknown_owner"))
    }

    @Test fun producesAHostSampleFromActualCacheDraws() {
        label("[RED]üЖ[] long suffix", 50f).setEllipsis(true)
        val frame = draw()
        val state = """{"schema_version":1,"save":"host-synthetic","selection":{"technology":"Mining"},"tab":null,"expanded_sections":[],"scroll":{},"scroll_to":{},"camera":null,"new_game":null}"""
        val request = L10nRequest.parse("tech-picker", "host-synthetic", state, "2")
        val bytes = frame.dump(1271048, request, "German")
        val path = java.nio.file.Paths.get("../../build/issue47-fix-evidence/host-draw-sample.texts.json")
        java.nio.file.Files.createDirectories(path.parent)
        java.nio.file.Files.write(path, bytes)
        assertFalse(bytes.toString(Charsets.UTF_8).contains("request"))
    }

    @Test fun metadataUsesTheActualWidgetAtDrawInsteadOfTraversalTime() {
        val label = object : Label("old", LabelStyle(font, null)) {
            override fun draw(batch: Batch, parentAlpha: Float) {
                setBounds(20f, 30f, 100f, 30f)
                setText("new")
                super.draw(batch, parentAlpha)
            }
        }
        label.setBounds(0f, 0f, 1f, 1f)
        stage.addActor(label)
        val row = draw().elements.single().json()
        assertEquals("new", row["input_text"])
        assertEquals("new", row["display_text"])
        assertEquals(80.0, (row["bounds"] as List<*>)[0] as Double, 0.001)
    }
}
