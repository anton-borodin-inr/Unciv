package com.unciv.l10n

import com.badlogic.gdx.graphics.g2d.GlyphLayout.GlyphRun
import com.unciv.UncivGame
import java.lang.ref.WeakReference

interface L10nTracedFontData {
    val l10nLayoutTrace: L10nGlyphLayoutTrace
}

/** Observe the engine's actual truncate invocation, including an unchanged literal "...".
 *
 * libGDX 1.14.2 calls BitmapFontData.getGlyphs for its temporary truncate run, then
 * appends it to the most recent live line run. Markup fragment runs have already
 * been appended and returned (empty) to the pool. We retain weak references only.
 * This neither requests a glyph nor changes a run, cache, layout or font scale.
 */
class L10nGlyphLayoutTrace(private val enabled: () -> Boolean = {
    UncivGame.isCurrentInitialized() && UncivGame.Current.l10nHook?.observesText == true
}) {
    private val sources = ArrayList<WeakReference<GlyphRun>>()
    private var truncated: GlyphRun? = null

    fun glyphs(run: GlyphRun, fill: () -> Unit) {
        if (!enabled()) { fill(); return }
        val caller = Throwable().stackTrace.firstOrNull {
            it.className == "com.badlogic.gdx.graphics.g2d.GlyphLayout"
        }?.methodName
        check(caller == "setText" || caller == "truncate") { "glyph_layout_call_not_observed" }
        val truncating = caller == "truncate"
        if (truncating) {
            val owner = sources.asReversed().firstNotNullOfOrNull {
                it.get()?.takeIf { source -> source !== run && source.glyphs.notEmpty() }
            } ?: error("truncate_owner_not_observed")
            fill()
            truncated = owner
        } else {
            truncated = null
            fill()
            // A glyph may lazily render an icon containing other labels. The outer
            // completed run, rather than a nested FBO run, owns the following truncate.
            truncated = null
            sources.removeAll { it.get() == null || it.get() === run || it.get()!!.glyphs.isEmpty }
            // Keep the last completed line and its newest markup fragment. A fragment
            // may be returned to the pool after this method returns, so one predecessor
            // is necessary; retaining every previously laid-out Label is unnecessary.
            val previous = sources.lastOrNull()
            sources.clear()
            if (previous != null) sources.add(previous)
            sources.add(WeakReference(run))
        }
    }

    fun wasTruncated(run: GlyphRun) = run === truncated
}
