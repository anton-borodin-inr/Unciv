package com.unciv.l10n

import com.badlogic.gdx.math.Vector2
import com.badlogic.gdx.scenes.scene2d.Actor
import com.badlogic.gdx.scenes.scene2d.Group
import com.badlogic.gdx.scenes.scene2d.ui.Label
import com.badlogic.gdx.scenes.scene2d.ui.ScrollPane
import com.badlogic.gdx.scenes.scene2d.utils.Layout
import com.badlogic.gdx.utils.JsonValue
import com.unciv.models.translations.tr
import kotlin.math.abs

/** Pane names and source-key actor anchors are registered by the owning widgets. */
class L10nScrollState(private val root: Group, private val request: L10nRequest) {
    val panes: Map<String, ScrollPane> = actors(root).filterIsInstance<ScrollPane>()
        .filter { effectivelyVisible(it) && it.name != "l10n-camera" }
        .mapIndexed { index, pane -> (pane.name?.takeIf { it.startsWith("l10n-pane:") }?.removePrefix("l10n-pane:") ?: "pane-$index") to pane }
        .also { require(it.map { pair -> pair.first }.distinct().size == it.size) { "duplicate_pane_id" } }.toMap()

    fun apply() {
        val offsets = request.state.get("scroll").associateBy { it.name }
        val anchors = request.state.get("scroll_to").associateBy { it.name }
        require((offsets.keys + anchors.keys) == panes.keys) {
            "pane_set_mismatch_expected_" + panes.keys.joinToString(",")
        }
        for ((id, pane) in panes) {
            pane.setSmoothScrolling(false)
            pane.setOverscroll(false, false)
            pane.setFlingTime(0f)
            pane.setFadeScrollBars(false)
            pane.validate()
            val offset = offsets[id]
            if (offset != null) {
                val x = offset.getFloat("x")
                val y = offset.getFloat("y")
                require(x <= pane.maxX && y <= pane.maxY) { "offset_out_of_range_$id" }
                pane.scrollX = x; pane.scrollY = y
            } else applyAnchor(id, pane, anchors.getValue(id))
            pane.updateVisualScroll()
        }
        verify()
    }

    private fun applyAnchor(id: String, pane: ScrollPane, anchor: JsonValue) {
        if (anchor.has("edge")) {
            val fraction = when (anchor.getString("edge")) { "start" -> 0f; "center" -> 0.5f; else -> 1f }
            pane.scrollX = pane.maxX * fraction
            pane.scrollY = pane.maxY * fraction
            return
        }
        val actor = resolve(id, pane, anchor)
        val point = actor.localToAscendantCoordinates(pane.actor, Vector2())
        val align = anchor.getString("align")
        when (align) {
            "visible" -> pane.scrollTo(point.x - 2f, point.y - 2f, actor.width + 4f, actor.height + 4f)
            "center" -> pane.scrollTo(point.x, point.y, actor.width, actor.height, true, true)
            else -> {
                val x = if (align == "start") point.x else point.x + actor.width - pane.scrollWidth
                val y = pane.actor.height - point.y - if (align == "start") actor.height else pane.scrollHeight
                require(pane.maxX == 0f || x in 0f..pane.maxX) { "anchor_out_of_range_$id" }
                require(pane.maxY == 0f || y in 0f..pane.maxY) { "anchor_out_of_range_$id" }
                pane.scrollX = if (pane.maxX == 0f) 0f else x
                pane.scrollY = if (pane.maxY == 0f) 0f else y
            }
        }
        pane.updateVisualScroll()
        require(isVisible(pane, actor)) { "anchor_not_fully_visible_$id" }
    }

    private fun resolve(id: String, pane: ScrollPane, anchor: JsonValue): Actor {
        val target = anchor.first().let { if (it.name == "align") it.next else it }
        val candidates = if (target.name == "text") {
            val text = target.asString().tr()
            actors(pane.actor).filterIsInstance<Label>().filter { effectivelyVisible(it) && it.text.toString() == text }
        } else actors(pane.actor).filter {
            effectivelyVisible(it) && it.name == if (target.name in setOf("entry", "tab")) target.asString()
            else "l10n-anchor:${target.name}:${target.asString()}"
        }
        require(candidates.size == 1) { "unresolved_or_ambiguous_anchor_$id" }
        return candidates.single()
    }

    fun verify() {
        for ((id, pane) in panes) {
            val offset = request.state.get("scroll").get(id)
            if (offset != null) require(abs(pane.scrollX - offset.getFloat("x")) < 0.01f && abs(pane.scrollY - offset.getFloat("y")) < 0.01f) { "scroll_changed_$id" }
            val anchor = request.state.get("scroll_to").get(id) ?: continue
            if (anchor.has("edge")) {
                val f = when (anchor.getString("edge")) { "start" -> 0f; "center" -> 0.5f; else -> 1f }
                require(abs(pane.scrollX - pane.maxX * f) < 0.01f && abs(pane.scrollY - pane.maxY * f) < 0.01f) { "edge_changed_$id" }
            } else require(isVisible(pane, resolve(id, pane, anchor))) { "anchor_changed_$id" }
        }
    }

    private fun isVisible(pane: ScrollPane, actor: Actor): Boolean {
        val p = actor.localToAscendantCoordinates(pane.actor, Vector2())
        val fromTop = pane.actor.height - p.y - actor.height
        return p.x >= pane.scrollX - 0.5f && p.x + actor.width <= pane.scrollX + pane.scrollWidth + 0.5f &&
            fromTop >= pane.scrollY - 0.5f && fromTop + actor.height <= pane.scrollY + pane.scrollHeight + 0.5f
    }

    fun layoutSignature(): List<Float> = actors(root).flatMap {
        if (it is ScrollPane) listOf(it.x, it.y, it.width, it.height, it.maxX, it.maxY, it.scrollX, it.scrollY)
        else listOf(it.x, it.y, it.width, it.height)
    }

    companion object {
        fun actors(root: Actor): List<Actor> = buildList {
            add(root)
            if (root is Group) for (child in root.children.toList()) addAll(actors(child))
        }

        fun effectivelyVisible(actor: Actor): Boolean {
            var node: Actor? = actor
            while (node != null) { if (!node.isVisible) return false; node = node.parent }
            return true
        }

        fun layout(root: Actor) {
            if (root is Layout) root.validate()
            if (root is Group) for (child in root.children.toList()) layout(child)
        }
    }
}
