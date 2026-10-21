package com.unciv.l10n

import com.badlogic.gdx.utils.JsonReader
import com.badlogic.gdx.utils.JsonValue

/** Versioned capture protocol. Android decodes base64 before calling this parser. */
data class L10nRequest(val screen: String, val token: String, val state: JsonValue) {
    val save: String? get() = state.get("save").let { if (it.isNull) null else it.asString() }
    val selection: JsonValue get() = state.get("selection")
    val expanded: Set<String> get() = state.get("expanded_sections").map { it.asString() }.toSet()

    companion object {
        val screens = setOf("options-gameplay", "new-game", "tech-picker", "city-production",
            "empire-stats", "policy-picker", "victory-status", "civilopedia", "world", "city-state-diplomacy")
        private val menuScreens = setOf("options-gameplay", "new-game")

        fun parse(screen: String, token: String, json: String): L10nRequest {
            require(screen in screens) { "unknown_screen" }
            require(token.matches(Regex("[A-Za-z0-9._-]{1,64}"))) { "invalid_request_id" }
            require(json.toByteArray(Charsets.UTF_8).size <= 32768) { "state_too_large" }
            val state = try { JsonReader().parse(json) } catch (_: Exception) {
                throw IllegalArgumentException("invalid_json")
            }
            state.fields("state", setOf("schema_version", "save", "selection", "tab", "expanded_sections",
                "scroll", "scroll_to", "camera", "new_game"))
            require(state.get("schema_version").isLong && state.getInt("schema_version") == 1) { "unsupported_schema" }
            val save = state.get("save")
            if (screen in menuScreens) require(save.isNull) { "save_not_applicable" }
            else {
                require(save.isString && save.asString().isNotBlank()) { "missing_save" }
                require(save.asString().length <= 128 && save.asString() !in setOf(".", "..") &&
                    save.asString().none { it == '/' || it == '\\' || it.isISOControl() }) { "invalid_save_name" }
            }
            val allowedSelection = when (screen) {
                "tech-picker" -> setOf("technology")
                "city-production" -> setOf("city", "construction")
                "civilopedia" -> setOf("category", "article")
                "world" -> setOf("unit")
                "city-state-diplomacy" -> setOf("city_state")
                else -> emptySet()
            }
            val selection = state.get("selection")
            selection.fields("selection", allowedSelection)
            for (field in selection) {
                when (field.name) {
                    "city" -> {
                        field.fields("city", setOf("owner", "name"))
                        field.nonEmptyString("owner"); field.nonEmptyString("name")
                    }
                    "unit" -> {
                        field.fields("unit", setOf("owner", "name", "tile"))
                        field.nonEmptyString("owner"); field.nonEmptyString("name"); tile(field.get("tile"))
                    }
                    else -> require(field.isString && field.asString().isNotBlank()) { "invalid_selection" }
                }
            }
            val expectedTab = when (screen) {
                "options-gameplay" -> "gameplay"
                "empire-stats" -> "statistics"
                "victory-status" -> "our-status"
                else -> null
            }
            val tab = state.get("tab")
            require(if (expectedTab == null) tab.isNull else tab.isString && tab.asString() == expectedTab) { "invalid_tab" }
            val expanded = state.get("expanded_sections")
            require(expanded.isArray && expanded.all { it.isString && it.asString().isNotBlank() } &&
                expanded.map { it.asString() }.distinct().size == expanded.size) { "invalid_expanded_sections" }
            require(screen in setOf("city-production", "policy-picker") || expanded.size == 0) { "sections_not_applicable" }
            val scroll = state.get("scroll")
            val anchors = state.get("scroll_to")
            scroll.objectFields("scroll"); anchors.objectFields("scroll_to")
            require(scroll.map { it.name }.intersect(anchors.map { it.name }.toSet()).isEmpty()) { "conflicting_scroll" }
            for (offset in scroll) {
                offset.fields("offset", setOf("x", "y"))
                offset.number("x", 0.0); offset.number("y", 0.0)
            }
            for (anchor in anchors) {
                anchor.objectFields("anchor")
                val targets = anchor.map { it.name }.toSet() - "align"
                require(targets.size == 1 && targets.single() in setOf("edge", "technology", "construction", "branch", "entry", "category", "tab", "text")) { "invalid_anchor" }
                anchor.nonEmptyString(targets.single())
                require(anchor.get("align")?.isString == true && anchor.getString("align") in setOf("visible", "start", "center", "end")) { "invalid_anchor_alignment" }
                if ("edge" in targets) require(anchor.getString("edge") in setOf("start", "center", "end")) { "invalid_edge" }
            }
            val camera = state.get("camera")
            if (screen == "world") {
                camera.fields("camera", setOf("center_tile", "zoom"))
                tile(camera.get("center_tile")); camera.number("zoom", 0.0001)
            } else require(camera.isNull) { "camera_not_applicable" }
            val setup = state.get("new_game")
            if (screen == "new-game") validateNewGame(setup)
            else require(setup.isNull) { "new_game_not_applicable" }
            return L10nRequest(screen, token, state)
        }

        private fun validateNewGame(setup: JsonValue) {
            setup.fields("new_game", setOf("ruleset", "difficulty", "speed", "era", "max_turns", "city_states", "map_type", "map_size", "seed", "players"))
            for (key in listOf("ruleset", "difficulty", "speed", "era", "map_type", "map_size")) setup.nonEmptyString(key)
            for (key in listOf("max_turns", "city_states", "seed")) require(setup.get(key).isLong) { "invalid_$key" }
            require(setup.getInt("max_turns") > 0 && setup.getInt("city_states") >= 0) { "invalid_new_game_counts" }
            val players = setup.get("players")
            require(players.isArray && players.size in 1..64) { "invalid_players" }
            for (player in players) {
                player.fields("player", setOf("type", "civilization"))
                player.nonEmptyString("civilization")
                require(player.get("type")?.isString == true && player.getString("type") in setOf("Human", "AI")) { "invalid_player_type" }
            }
            require(players.any { it.getString("type") == "Human" }) { "missing_human_player" }
        }

        private fun tile(value: JsonValue?) {
            require(value != null && value.isArray && value.size == 2 && value.all { it.isLong }) { "invalid_tile" }
        }
    }
}

internal fun JsonValue.objectFields(context: String) {
    require(isObject) { "invalid_$context" }
    require(map { it.name }.distinct().size == size) { "duplicate_${context}_field" }
}

internal fun JsonValue.fields(context: String, expected: Set<String>) {
    objectFields(context)
    require(map { it.name }.toSet() == expected) { "invalid_${context}_fields" }
}

internal fun JsonValue.nonEmptyString(key: String): String {
    val v = get(key)
    require(v != null && v.isString && v.asString().isNotBlank()) { "invalid_$key" }
    return v.asString()
}

internal fun JsonValue.number(key: String, minimum: Double): Float {
    val v = get(key)
    require(v != null && v.isNumber && v.asDouble().isFinite() && v.asDouble() >= minimum && v.asDouble() <= Float.MAX_VALUE) { "invalid_$key" }
    return v.asFloat()
}
