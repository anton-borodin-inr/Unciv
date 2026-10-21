package com.unciv.l10n

import java.io.File
import java.io.FileOutputStream
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.security.MessageDigest
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

fun l10nSha256(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(bytes)
    .joinToString("") { (it.toInt() and 255).toString(16).padStart(2, '0') }

/** Physical framebuffer coordinates, with a top-left origin. No geometry is rounded. */
data class L10nPoint(val x: Double, val y: Double)
data class L10nRect(val left: Double, val top: Double, val right: Double, val bottom: Double) {
    init { require(listOf(left, top, right, bottom).all { it.isFinite() } && right >= left && bottom >= top) }
    fun json() = listOf(left, top, right, bottom)
    fun intersection(other: L10nRect): L10nRect? {
        val l = max(left, other.left); val t = max(top, other.top)
        val r = min(right, other.right); val b = min(bottom, other.bottom)
        return if (r > l && b > t) L10nRect(l, t, r, b) else null
    }
    companion object {
        fun bounds(points: List<L10nPoint>): L10nRect {
            require(points.isNotEmpty())
            return L10nRect(points.minOf { it.x }, points.minOf { it.y }, points.maxOf { it.x }, points.maxOf { it.y })
        }
        fun union(rects: List<L10nRect>): L10nRect? = if (rects.isEmpty()) null else
            L10nRect(rects.minOf { it.left }, rects.minOf { it.top }, rects.maxOf { it.right }, rects.maxOf { it.bottom })
    }
}

/** Sutherland-Hodgman clipping measures quad area, including rotated/scaled glyphs. */
object L10nGeometry {
    fun area(points: List<L10nPoint>): Double = if (points.size < 3) 0.0 else abs(points.indices.sumOf {
        val a = points[it]; val b = points[(it + 1) % points.size]
        a.x * b.y - b.x * a.y
    }) / 2

    fun clip(points: List<L10nPoint>, rect: L10nRect?): List<L10nPoint> {
        if (rect == null) return emptyList()
        var output = points
        for (edge in 0..3) {
            val input = output
            output = emptyList()
            if (input.isEmpty()) break
            val result = ArrayList<L10nPoint>()
            fun inside(p: L10nPoint) = when (edge) {
                0 -> p.x >= rect.left; 1 -> p.x <= rect.right
                2 -> p.y >= rect.top; else -> p.y <= rect.bottom
            }
            fun crossing(a: L10nPoint, b: L10nPoint): L10nPoint = when (edge) {
                0, 1 -> {
                    val x = if (edge == 0) rect.left else rect.right
                    L10nPoint(x, a.y + (b.y - a.y) * (x - a.x) / (b.x - a.x))
                }
                else -> {
                    val y = if (edge == 2) rect.top else rect.bottom
                    L10nPoint(a.x + (b.x - a.x) * (y - a.y) / (b.y - a.y), y)
                }
            }
            var previous = input.last()
            for (point in input) {
                if (inside(point) != inside(previous)) result.add(crossing(previous, point))
                if (inside(point)) result.add(point)
                previous = point
            }
            output = result
        }
        return output
    }
}

data class L10nDrawGlyph(val text: String, val quad: List<L10nPoint>, val alpha: Double, val line: Int) {
    init { require(quad.size == 4 && alpha.isFinite() && alpha in 0.0..1.0 && line >= 0) }
}

data class L10nDrawElement(
    val id: String, val logicalId: String, val actorPath: String?, val sourceKey: String?,
    val sourceKeys: List<String>, val inputText: String?, val ownerBounds: L10nRect?,
    val glyphs: List<L10nDrawGlyph>, val viewport: L10nRect, val scissor: L10nRect?,
    val ellipsisApplied: Boolean, val fontScale: List<Double>, val pixelScale: List<Double>,
    val decoration: Boolean = false, val warnings: List<String> = emptyList(), val lineCount: Int? = null,
    val emptyLineBounds: Map<Int, L10nRect> = emptyMap()
) {
    fun json(): Map<String, Any?> {
        require(glyphs.isNotEmpty())
        require(fontScale.size == 2 && pixelScale.size == 2 && (fontScale + pixelScale).all { it.isFinite() && it > 0 })
        val clip = if (scissor == null) viewport else viewport.intersection(scissor)
        var fullArea = 0.0; var visibleArea = 0.0
        var viewportLoss = false; var scissorLoss = false
        val extraWarnings = ArrayList(warnings)
        val glyphJson = glyphs.map { glyph ->
            val bounds = L10nRect.bounds(glyph.quad)
            val area = L10nGeometry.area(glyph.quad)
            val inViewport = L10nGeometry.clip(glyph.quad, viewport)
            val visible = L10nGeometry.clip(glyph.quad, clip)
            val clippedArea = L10nGeometry.area(visible).coerceIn(0.0, area)
            fullArea += area; visibleArea += clippedArea
            viewportLoss = viewportLoss || L10nGeometry.area(inViewport) < area
            scissorLoss = scissorLoss || (scissor != null && clippedArea < L10nGeometry.area(inViewport))
            val visibility = when {
                glyph.alpha == 0.0 || visible.isEmpty() || (area > 0.0 && clippedArea == 0.0) -> "hidden"
                clippedArea < area -> "partial"
                else -> "full"
            }
            linkedMapOf<String, Any?>("text" to glyph.text, "bounds" to bounds.json(),
                "visible_bounds" to if (visibility == "hidden") null else L10nRect.bounds(visible).json(),
                "visibility" to visibility)
        }
        val full = L10nRect.union(glyphs.map { L10nRect.bounds(it.quad) })!!
        val visibleRects = glyphJson.mapNotNull { it["visible_bounds"] as? List<*> }.map {
            L10nRect(it[0] as Double, it[1] as Double, it[2] as Double, it[3] as Double)
        }
        val visibility = when {
            glyphJson.all { it["visibility"] == "full" } -> "full"
            glyphJson.all { it["visibility"] == "hidden" } -> "hidden"
            else -> "partial"
        }
        if (actorPath == null || ownerBounds == null) extraWarnings.add("unknown_owner")
        if (sourceKey == null && sourceKeys.isEmpty()) extraWarnings.add("source_unavailable")
        if (glyphs.map { it.alpha }.distinct().size > 1) extraWarnings.add("mixed_glyph_alpha")
        if (glyphJson.any { it["visibility"] == "partial" }) extraWarnings.add("partial_glyph_requires_human_review")
        if (clip != null) {
            if (full.left < clip.left) extraWarnings.add("clipped_left")
            if (full.top < clip.top) extraWarnings.add("clipped_top")
            if (full.right > clip.right) extraWarnings.add("clipped_right")
            if (full.bottom > clip.bottom) extraWarnings.add("clipped_bottom")
        }
        val lastLine = maxOf(glyphs.maxOf { it.line }, (lineCount ?: 1) - 1)
        val lines = (0..lastLine).map { line ->
            val indices = glyphs.indices.filter { glyphs[it].line == line }
            // Empty baseline lines retain their position in display_text. They have no submitted quads.
            val bounds = L10nRect.union(indices.map { L10nRect.bounds(glyphs[it].quad) })
                ?: emptyLineBounds[line] ?: L10nRect(full.left, full.top, full.left, full.top)
            linkedMapOf("text" to indices.joinToString("") { glyphs[it].text }, "bounds" to bounds.json(),
                "glyphs" to indices.map { glyphJson[it] })
        }
        return linkedMapOf(
            "id" to id, "logical_id" to logicalId, "actor_path" to actorPath,
            "source_key" to sourceKey, "source_keys" to sourceKeys, "input_text" to inputText,
            "display_text" to lines.joinToString("\n") { it["text"] as String },
            "bounds" to ownerBounds?.json(), "glyph_bounds" to full.json(),
            "visible_bounds" to L10nRect.union(visibleRects)?.json(), "drawn" to (visibility != "hidden"),
            "effective_alpha" to glyphs.maxOf { it.alpha }, "visibility" to visibility,
            "clip_bounds" to clip?.json(), "clipped_fraction" to if (fullArea == 0.0) 0.0 else (1 - visibleArea / fullArea).coerceIn(0.0, 1.0),
            "clip_cause" to when { viewportLoss && scissorLoss -> "mixed"; scissorLoss -> "ancestor_scissor"; viewportLoss -> "screen"; else -> "none" },
            "ellipsis_applied" to ellipsisApplied,
            "overflow_x" to (ownerBounds != null && (full.left < ownerBounds.left || full.right > ownerBounds.right)),
            "overflow_y" to (ownerBounds != null && (full.top < ownerBounds.top || full.bottom > ownerBounds.bottom)),
            "font_scale" to fontScale, "pixel_scale" to pixelScale, "decoration" to decoration,
            "warnings" to extraWarnings.distinct(), "lines" to lines)
    }
}

/** Fixed insertion order, JSON quoting, exact finite number strings, UTF-8 and one trailing LF. */
object L10nCanonicalJson {
    fun bytes(value: Any?): ByteArray {
        val encoded = Charsets.UTF_8.newEncoder().onMalformedInput(java.nio.charset.CodingErrorAction.REPORT)
            .encode(java.nio.CharBuffer.wrap(encode(value) + "\n"))
        return ByteArray(encoded.remaining()).also { encoded.get(it) }
    }
    private fun encode(value: Any?): String = when (value) {
        null -> "null"
        is String -> buildString {
            append('"')
            for (ch in value) when (ch) {
                '"' -> append("\\\""); '\\' -> append("\\\\")
                '\n' -> append("\\n"); '\r' -> append("\\r"); '\t' -> append("\\t")
                else -> if (ch < ' ') append("\\u" + ch.code.toString(16).padStart(4, '0')) else append(ch)
            }
            append('"')
        }
        is Boolean, is Int, is Long -> value.toString()
        is Double -> { require(value.isFinite()); if (value == 0.0) "0" else value.toString() }
        is Float -> { require(value.isFinite()); encode(value.toDouble()) }
        is Map<*, *> -> value.entries.joinToString(",", "{", "}") {
            require(it.key is String); encode(it.key) + ":" + encode(it.value)
        }
        is Iterable<*> -> value.joinToString(",", "[", "]") { encode(it) }
        else -> error("unsupported_dump_value")
    }
}

/** The only allowed output is files/l10n-hook/<token>.json. Never replace an existing file. */
class L10nDumpPublisher(private val filesDirectory: File) {
    data class Published(val path: String, val sha256: String)
    fun publish(token: String, bytes: ByteArray): Published {
        require(token.matches(Regex("[A-Za-z0-9._-]{1,64}"))) { "invalid_request_id" }
        val directory = File(filesDirectory, "l10n-hook")
        check(directory.isDirectory || directory.mkdir()) { "dump_directory_failed" }
        val target = File(directory, "$token.json")
        val temporary = File(directory, "$token.json.tmp")
        check(!target.exists() && temporary.createNewFile()) { "dump_output_exists" }
        FileOutputStream(temporary).use { it.write(bytes); it.fd.sync() }
        check(!target.exists()) { "dump_output_exists" }
        // Same-directory atomic publication; unsupported atomic rename is an error, not a copy fallback.
        Files.move(temporary.toPath(), target.toPath(), StandardCopyOption.ATOMIC_MOVE)
        return Published("files/l10n-hook/$token.json", l10nSha256(bytes))
    }
}
