package com.unciv.l10n

import com.badlogic.gdx.Gdx
import com.badlogic.gdx.graphics.GL20
import com.badlogic.gdx.graphics.g2d.Batch
import com.badlogic.gdx.graphics.g2d.BitmapFont
import com.badlogic.gdx.graphics.g2d.BitmapFontCache
import com.badlogic.gdx.graphics.g2d.GlyphLayout
import com.badlogic.gdx.math.Matrix4
import com.badlogic.gdx.math.Vector3
import com.badlogic.gdx.scenes.scene2d.Actor
import com.badlogic.gdx.scenes.scene2d.Group
import com.badlogic.gdx.scenes.scene2d.Stage
import com.badlogic.gdx.scenes.scene2d.ui.Label
import com.badlogic.gdx.scenes.scene2d.ui.SelectBox
import com.badlogic.gdx.scenes.scene2d.ui.TextField
import com.badlogic.gdx.utils.BufferUtils
import com.badlogic.gdx.utils.NumberUtils
import com.unciv.UncivGame
import com.unciv.models.translations.getPlaceholderText
import com.unciv.ui.components.fonts.DiacriticSupport
import com.unciv.ui.components.fonts.FontRulesetIcons
import com.unciv.ui.components.fonts.Fonts
import com.unciv.ui.components.widgets.ShadowedLabel
import com.unciv.ui.components.widgets.TranslatedSelectBox
import java.util.IdentityHashMap
import java.util.WeakHashMap
import kotlin.math.hypot
import kotlin.math.roundToInt

/** Forward provenance from translating factories, never a reverse translated-string lookup. */
object L10nTextMetadata {
    data class Source(val input: String, val key: String?, val keys: List<String>)
    private val sources = WeakHashMap<Actor, Source>()

    fun bind(actor: Actor, source: String, translated: String) {
        if (!UncivGame.isCurrentInitialized() || UncivGame.Current.l10nHook?.observesText != true) return
        val translations = UncivGame.Current.translations
        fun known(text: String) = translations[text.getPlaceholderText()]?.entry
        val key = known(source)
        val components = if (key == null) componentKeys(source, ::known) else emptyList()
        sources[actor] = Source(translated, key, components)
    }

    /** Innermost nonempty literal brace spans, in order, including repeated occurrences.
     * Avoid regex compilation here: Android ICU rejects a bare closing literal brace
     * that the host JVM accepted. Opening another brace starts a new inner component;
     * an unmatched closing brace or unfinished component supplies no provenance.
     */
    private fun componentKeys(source: String, known: (String) -> String?): List<String> {
        val keys = ArrayList<String>()
        var start = -1
        source.forEachIndexed { index, char ->
            when (char) {
                '{' -> start = index + 1
                '}' -> {
                    if (start >= 0 && index > start)
                        known(source.substring(start, index))?.let { keys.add(it) }
                    start = -1
                }
            }
        }
        return keys
    }

    fun get(actor: Actor, input: String?): Source? = sources[actor]?.takeIf { it.input == input }
}

/** Same drawing implementation as BitmapFont; cache overrides only observe final submissions. */
class L10nBitmapFont(data: BitmapFont.BitmapFontData, regions: com.badlogic.gdx.utils.Array<com.badlogic.gdx.graphics.g2d.TextureRegion>) :
    BitmapFont(data, regions, false) {
    override fun newFontCache(): BitmapFontCache = L10nBitmapFontCache(this)
}

class L10nBitmapFontCache(font: BitmapFont) : BitmapFontCache(font) {
    internal data class LayoutScale(val x: Float, val y: Float, val down: Float, val blank: Float, val cap: Float,
                                    val originX: Float, val originY: Float, val cacheX: Float, val cacheY: Float)
    internal val scales = IdentityHashMap<GlyphLayout, LayoutScale>()
    internal val truncatedLayouts = IdentityHashMap<GlyphLayout, Boolean>()
    internal var directInput: String? = null

    override fun clear() {
        super.clear()
        scales.clear(); truncatedLayouts.clear(); directInput = null
    }

    override fun addText(layout: GlyphLayout, x: Float, y: Float) {
        super.addText(layout, x, y)
        scales[layout] = font.data.run { LayoutScale(scaleX, scaleY, down, blankLineScale, capHeight,
            x, y + ascent, this@L10nBitmapFontCache.x, this@L10nBitmapFontCache.y) }
        val trace = (font.data as? L10nTracedFontData)?.l10nLayoutTrace
        truncatedLayouts[layout] = trace != null && layout.runs.any { trace.wasTruncated(it) }
    }

    override fun addText(str: CharSequence, x: Float, y: Float, start: Int, end: Int,
                         targetWidth: Float, halign: Int, wrap: Boolean, truncate: String?): GlyphLayout {
        val result = super.addText(str, x, y, start, end, targetWidth, halign, wrap, truncate)
        directInput = str.toString()
        return result
    }

    override fun draw(batch: Batch) {
        L10nDrawObserver.current?.observe(this, batch, 0, Int.MAX_VALUE)
        super.draw(batch)
    }

    override fun draw(batch: Batch, start: Int, end: Int) {
        L10nDrawObserver.current?.observe(this, batch, start, end)
        super.draw(batch, start, end)
    }
}

/** Active only around a v2 stage draw, on the GL thread. No validate/layout/getGlyph calls. */
class L10nDrawObserver private constructor(stage: Stage) {
    data class Frame(val size: List<Int>, val viewport: L10nRect, val elements: List<L10nDrawElement>) {
        fun dump(build: Int, request: L10nRequest, language: String): ByteArray = L10nCanonicalJson.bytes(linkedMapOf(
            "dump_version" to 1, "protocol_version" to 2, "build_version_code" to build,
            "screen" to request.screen, "language" to language, "state_sha256" to request.stateSha256,
            "image_size" to size, "viewport" to viewport.json(), "elements" to elements.map { it.json() }))
    }

    private data class Owner(val actor: Actor, val path: String, val logical: String, val decoration: Boolean)
    private val owners = IdentityHashMap<Actor, Owner>()
    private val labelLayouts = IdentityHashMap<GlyphLayout, Owner>()
    private val draws = HashMap<String, Int>()
    private val elements = ArrayList<L10nDrawElement>()
    private var explicitOwner: Actor? = null
    private val imageWidth = Gdx.graphics.backBufferWidth
    private val imageHeight = Gdx.graphics.backBufferHeight
    private val framebuffer = integers(GL20.GL_FRAMEBUFFER_BINDING, 1)[0]

    init { index(stage.root, "root") }

    private fun index(actor: Actor, path: String) {
        // Metadata only: no geometry, validation or layout is inferred from traversal.
        val shadow = actor.parent as? ShadowedLabel
        val logical = if (shadow == null) path else path.substringBeforeLast('/')
        val owner = Owner(actor, path, logical,
            shadow != null && shadow.children.first() === actor)
        owners[actor] = owner
        if (actor is Label) labelLayouts[actor.glyphLayout] = owner
        if (actor is Group) actor.children.forEachIndexed { index, child ->
            index(child, "$path/$index:${child.javaClass.simpleName.ifEmpty { "Actor" }}")
        }
    }

    private fun viewport(): L10nRect {
        val v = integers(GL20.GL_VIEWPORT, 4)
        return L10nRect(v[0].toDouble(), (imageHeight - v[1] - v[3]).toDouble(),
            (v[0] + v[2]).toDouble(), (imageHeight - v[1]).toDouble())
    }

    internal fun observe(cache: L10nBitmapFontCache, batch: Batch, start: Int, end: Int) {
        // Lazy font-icon generation may render to an offscreen FBO. It isn't part of the screenshot.
        if (integers(GL20.GL_FRAMEBUFFER_BINDING, 1)[0] != framebuffer) return
        val view = viewport()
        val screen = L10nRect(0.0, 0.0, imageWidth.toDouble(), imageHeight.toDouble())
        val visibleViewport = view.intersection(screen) ?: return
        val scissor = if (!Gdx.gl.glIsEnabled(GL20.GL_SCISSOR_TEST)) null else {
            val s = integers(GL20.GL_SCISSOR_BOX, 4)
            L10nRect(s[0].toDouble(), (imageHeight - s[1] - s[3]).toDouble(),
                (s[0] + s[2]).toDouble(), (imageHeight - s[1]).toDouble())
        }
        val transform = Matrix4(batch.projectionMatrix).mul(batch.transformMatrix)
        fun project(x: Float, y: Float): L10nPoint {
            val p = Vector3(x, y, 0f).prj(transform)
            return L10nPoint(view.left + (p.x.toDouble() + 1) * (view.right - view.left) / 2,
                view.top + (1 - p.y.toDouble()) * (view.bottom - view.top) / 2)
        }
        val origin = project(0f, 0f); val axisX = project(1f, 0f); val axisY = project(0f, 1f)
        val owner = explicitOwner?.let { owners[it] } ?: cache.layouts.firstOrNull()?.let { labelLayouts[it] }
        val glyphs = ArrayList<L10nDrawGlyph>()
        val lineOrigins = HashMap<Int, L10nRect>()
        val warnings = ArrayList<String>()
        val offsets = IntArray(cache.pageCount)
        var glyphIndex = 0; var totalLines = 0
        var scale: L10nBitmapFontCache.LayoutScale? = null
        for (layout in cache.layouts) {
            val layoutScale = cache.scales[layout] ?: error("unobserved_cache_layout")
            if (scale == null) scale = layoutScale
            else if (scale != layoutScale) warnings.add("mixed_layout_scales")
            check(layoutScale.blank == 1f && layoutScale.down != 0f) { "unsupported_blank_line_geometry" }
            val lines = ((layout.height - layoutScale.cap) / kotlin.math.abs(layoutScale.down)).roundToInt() + 1
            for (line in 0 until lines) {
                val origin = project(layoutScale.originX + cache.x - layoutScale.cacheX,
                    layoutScale.originY + cache.y - layoutScale.cacheY + line * layoutScale.down)
                lineOrigins[totalLines + line] = L10nRect(origin.x, origin.y, origin.x, origin.y)
            }
            for (run in layout.runs) {
                val line = totalLines + (run.y / layoutScale.down).roundToInt()
                for (glyph in run.glyphs) {
                    val offset = offsets[glyph.page]
                    offsets[glyph.page] += 20
                    val selected = glyphIndex++ in start until end
                    if (!selected) continue
                    val vertices = cache.getVertices(glyph.page)
                    check(offset + 20 <= cache.getVertexCount(glyph.page)) { "glyph_vertex_mapping_failed" }
                    val quad = (0..3).map { project(vertices[offset + it * 5], vertices[offset + it * 5 + 1]) }
                    val alpha = (NumberUtils.floatToIntColor(vertices[offset + 2]).ushr(24) and 255) / 255.0
                    val char = glyph.id.toChar()
                    val text = DiacriticSupport.getStringFor(char)
                    if (char in Fonts.allSymbols || char in FontRulesetIcons.charToRulesetImageActor)
                        warnings.add("icon_glyph:" + char.code.toString(16))
                    glyphs.add(L10nDrawGlyph(text, quad, alpha, line))
                }
            }
            totalLines += lines
        }
        if (glyphs.isEmpty()) return // Empty cache sends no glyph draw to Batch.
        val firstLine = if (start == 0) 0 else glyphs.first().line
        val normalizedGlyphs = glyphs.map { it.copy(line = it.line - firstLine) }
        val layoutScale = scale!!
        val input = when (val actor = owner?.actor) {
            is Label -> actor.text.toString()
            is SelectBox<*> -> actor.selected?.toString()
            is TextField -> if (actor.text.isEmpty()) actor.messageText else actor.text
            else -> cache.directInput
        }
        // These widgets draw in parent coordinates. Group.draw's temporary child
        // offsets and the actual batch matrix are both active at this exact point.
        val bounds = owner?.actor?.let { actor -> L10nRect.bounds(listOf(
            project(actor.x, actor.y), project(actor.x + actor.width, actor.y),
            project(actor.x + actor.width, actor.y + actor.height), project(actor.x, actor.y + actor.height))) }
        val source = owner?.let { L10nTextMetadata.get(it.actor, input) }
        val selectedKey = (owner?.actor as? TranslatedSelectBox)?.selected?.value
        val path = owner?.path ?: "unmapped"
        val drawIndex = draws.getOrDefault(path, 0)
        draws[path] = drawIndex + 1
        // Input remains full provenance; display_text is made only from submitted glyphs.
        check(cache.font.data is L10nTracedFontData) { "font_layout_not_observed" }
        val ellipsisApplied = cache.layouts.any { cache.truncatedLayouts[it] == true }
        if (owner == null) warnings.add("unmapped_font_draw")
        if (input?.contains('[') == true) warnings.add("markup_input")
        elements.add(L10nDrawElement("$path/draw-$drawIndex", owner?.logical ?: "$path/draw-$drawIndex",
            owner?.path, source?.key ?: selectedKey, source?.keys ?: emptyList(),
            input?.map { DiacriticSupport.getStringFor(it) }?.joinToString(""), bounds,
            normalizedGlyphs, visibleViewport, scissor, ellipsisApplied,
            listOf(layoutScale.x.toDouble(), layoutScale.y.toDouble()),
            listOf(hypot(axisX.x - origin.x, axisX.y - origin.y) * layoutScale.x,
                hypot(axisY.x - origin.x, axisY.y - origin.y) * layoutScale.y),
            owner?.decoration ?: false, warnings,
            if (start == 0 && end >= glyphIndex) totalLines else normalizedGlyphs.maxOf { it.line } + 1,
            lineOrigins.filterKeys { it >= firstLine }.mapKeys { it.key - firstLine }))
    }

    companion object {
        internal var current: L10nDrawObserver? = null
            private set

        private fun integers(parameter: Int, size: Int): IntArray {
            val buffer = BufferUtils.newIntBuffer(size)
            Gdx.gl.glGetIntegerv(parameter, buffer)
            return IntArray(size) { buffer.get(it) }
        }

        fun collect(stage: Stage, draw: () -> Unit): Frame {
            check(current == null) { "nested_dump_observation" }
            val observer = L10nDrawObserver(stage)
            current = observer
            try { draw() } finally { current = null }
            return Frame(listOf(observer.imageWidth, observer.imageHeight), observer.viewport(), observer.elements)
        }

        fun <T> withOwner(actor: Actor, draw: () -> T): T {
            val observer = current ?: return draw()
            val previous = observer.explicitOwner
            observer.explicitOwner = actor
            try { return draw() } finally { observer.explicitOwner = previous }
        }

    }
}
