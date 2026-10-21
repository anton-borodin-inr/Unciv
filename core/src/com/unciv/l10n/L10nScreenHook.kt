package com.unciv.l10n

import com.badlogic.gdx.Gdx
import com.badlogic.gdx.graphics.GL20
import com.badlogic.gdx.math.Vector2
import com.badlogic.gdx.scenes.scene2d.Actor
import com.badlogic.gdx.scenes.scene2d.ui.ScrollPane
import com.badlogic.gdx.scenes.scene2d.utils.Layout
import com.badlogic.gdx.utils.JsonValue
import com.unciv.UncivGame
import com.unciv.logic.civilization.PlayerType
import com.unciv.logic.map.MapSize
import com.unciv.logic.map.MapType
import com.unciv.logic.map.toHexCoord
import com.unciv.models.metadata.GameSetupInfo
import com.unciv.models.metadata.Player
import com.unciv.models.ruleset.RulesetCache
import com.unciv.ui.components.widgets.ExpanderTab
import com.unciv.ui.images.ImageGetter
import com.unciv.ui.popups.Popup
import com.unciv.ui.popups.options.OptionsPopup
import com.unciv.ui.popups.options.OptionsPopupPages
import com.unciv.ui.screens.basescreen.BaseScreen
import com.unciv.ui.screens.cityscreen.CityScreen
import com.unciv.ui.screens.civilopediascreen.CivilopediaCategories
import com.unciv.ui.screens.civilopediascreen.CivilopediaScreen
import com.unciv.ui.screens.diplomacyscreen.DiplomacyScreen
import com.unciv.ui.screens.mainmenuscreen.MainMenuScreen
import com.unciv.ui.screens.newgamescreen.NewGameScreen
import com.unciv.ui.screens.overviewscreen.EmpireOverviewCategories
import com.unciv.ui.screens.overviewscreen.EmpireOverviewScreen
import com.unciv.ui.screens.pickerscreens.PickerScreen
import com.unciv.ui.screens.pickerscreens.PolicyPickerScreen
import com.unciv.ui.screens.pickerscreens.TechPickerScreen
import com.unciv.ui.screens.victoryscreen.VictoryScreen
import com.unciv.ui.screens.worldscreen.WorldScreen
import com.unciv.utils.Concurrency
import com.unciv.utils.Log
import com.unciv.utils.withGLContext

/** Android debug-only entry point. No settings, saves or gameplay choices are persisted. */
class L10nScreenHook(private val game: UncivGame) {
    var active = true
        private set
    private val queue = ArrayDeque<Triple<String, String, String?>>()
    private var loading = false
    private var leavePending = false
    private var request: L10nRequest? = null
    private var target: BaseScreen? = null
    private var scrollState: L10nScrollState? = null
    private var applied = false
    private var terminal = false
    private var started = 0L
    private val frames = L10nReadyFrames()

    fun enqueue(screen: String, token: String, decodedJson: String?) {
        leavePending = false
        queue.addLast(Triple(screen, token, decodedJson))
        Gdx.graphics?.requestRendering()
    }

    fun requestLeave() {
        leavePending = true
        for ((screen, token, _) in queue)
            emit("L10N_SCREEN_ERROR ${safe(screen)} request=${safe(token)} reason=normal_launch_requested")
        queue.clear()
        Gdx.graphics.requestRendering()
    }

    private fun leave() {
        // Dispose hook widgets while persistence is still blocked, then restore ordinary settings.
        game.l10nReset(object : BaseScreen() {})
        game.settings = game.files.getGeneralSettings()
        active = false
        game.l10nReset(MainMenuScreen())
        Gdx.graphics.isContinuousRendering = game.settings.continuousRendering
        game.l10nHook = null
    }

    fun permits(popup: Popup) = request?.screen == "options-gameplay" && popup is OptionsPopup

    /** Called on the GL thread instead of the ordinary renderer while capture mode is active. */
    fun render(): Boolean {
        if (!active || !game.l10nInitialized) return false
        if (!loading && leavePending) { leave(); return false }
        if (!loading && queue.isNotEmpty()) start(queue.removeFirst())
        if (loading) return false
        val screen = target ?: return false
        val current = request ?: return false
        if (game.screen !== screen) {
            fail(current.screen, current.token, "screen_replaced")
            return true
        }
        try {
            if (!applied) {
                if (hasPendingWork(screen)) {
                    screen.render(0f)
                    checkTimeout()
                    Gdx.graphics.requestRendering()
                    return true
                }
                finishState(screen, current)
                L10nScrollState.layout(screen.stage.root)
                // All transitions are disabled in hook mode. Remove perpetual decorative actions.
                for (actor in L10nScrollState.actors(screen.stage.root)) actor.clearActions()
                scrollState = L10nScrollState(screen.stage.root, current)
                emit("L10N_SCREEN_PANES ${current.screen} request=${current.token} panes=${scrollState!!.panes.keys.joinToString(",")}")
                scrollState!!.apply()
                applied = true
            }
            Gdx.gl.glClearColor(BaseScreen.clearColor.r, BaseScreen.clearColor.g, BaseScreen.clearColor.b, BaseScreen.clearColor.a)
            Gdx.gl.glClear(GL20.GL_COLOR_BUFFER_BIT)
            // Deliberately draw without act(): map, unit, cursor and transition clocks stay frozen.
            screen.stage.draw()
            val actors = L10nScrollState.actors(screen.stage.root)
            val quiet = !hasPendingWork(screen) && actors.none { it.hasActions() || ((it is com.badlogic.gdx.scenes.scene2d.ui.Widget && it.needsLayout()) || (it is com.badlogic.gdx.scenes.scene2d.ui.WidgetGroup && it.needsLayout())) }
            if (quiet && !terminal) {
                try { scrollState!!.verify() }
                catch (_: IllegalArgumentException) {
                    // A first draw can finalize nested pane bounds. Apply against that layout,
                    // then require two new quiet draws; unresolved/range errors still fail.
                    checkTimeout()
                    scrollState!!.apply()
                    frames.reset()
                    Gdx.graphics.requestRendering()
                    return true
                }
            }
            if (frames.rendered(quiet, scrollState!!.layoutSignature()) && !terminal) {
                scrollState!!.verify()
                Gdx.gl.glFinish()
                terminal = true
                emit("L10N_SCREEN_READY ${current.screen} request=${current.token} frames=${frames.quietFrames}")
            }
            if (!terminal) { checkTimeout(); Gdx.graphics.requestRendering() }
        } catch (ex: Exception) {
            fail(current.screen, current.token, ex.message ?: ex.javaClass.simpleName)
        }
        return true
    }

    private fun start(input: Triple<String, String, String?>) {
        loading = true; target = null; scrollState = null; applied = false; terminal = false
        frames.reset(); started = System.nanoTime()
        // Dispose the complete previous stack before reading fresh settings or a fresh save.
        game.l10nReset(object : BaseScreen() {})
        game.settings = game.files.getGeneralSettings().apply {
            showTutorials = false
            enableEasterEggs = false
            continuousRendering = false
            unitMovementButtonAnimation = false
            unitActionsTableAnimation = false
            showUnitMovements = false
        }
        Gdx.graphics.isContinuousRendering = true // request-driven work is not allowed to starve during setup
        try {
            require(input.third != null) { "invalid_base64_or_missing_extra" }
            request = L10nRequest.parse(input.first, input.second, input.third!!)
        } catch (ex: Exception) {
            fail(input.first, input.second, ex.message ?: "invalid_request")
            return
        }
        val current = request!!
        Concurrency.run("L10n screen load") {
            try {
                val world = current.save?.let { name ->
                    require(game.files.getSave(name).exists()) { "save_not_found" }
                    val saved = game.files.loadGameByName(name)
                    require(!saved.gameParameters.isOnlineMultiplayer) { "multiplayer_save_not_supported" }
                    // Fonts are incremental atlases: their packing and mipmaps otherwise depend
                    // on the previously visited screens, even when the saved game is reloaded.
                    withGLContext { ImageGetter.setNewRuleset(saved.ruleset) }
                    game.loadGame(saved, callFromLoadScreen = true)
                }
                withGLContext {
                    if (!active) return@withGLContext
                    if (world != null) {
                        world.shouldUpdate = true
                        world.render(0f)
                    }
                    val screen = build(current, world)
                    target = screen
                    loading = false
                    Gdx.input.inputProcessor = null
                    Gdx.graphics.requestRendering()
                }
            } catch (ex: Exception) {
                withGLContext { fail(current.screen, current.token, ex.message ?: ex.javaClass.simpleName) }
            }
        }
    }

    private fun build(r: L10nRequest, world: WorldScreen?): BaseScreen {
        val selected = r.selection
        if (r.screen == "options-gameplay") {
            val menu = MainMenuScreen()
            game.l10nReset(menu)
            OptionsPopup(menu, OptionsPopupPages.Gameplay).open(true)
            return menu
        }
        if (r.screen == "new-game") {
            val setup = newGameSetup(r.state.get("new_game"))
            // PickerPane builds its buttons before NewGameScreen refreshes its ruleset.
            // Give those inherited widgets fresh fonts too, not the previous screen atlas.
            ImageGetter.setNewRuleset(RulesetCache.getComplexRuleset(setup.gameParameters))
            val screen = NewGameScreen(setup)
            game.l10nReset(screen)
            return screen
        }
        require(world != null) { "missing_game" }
        val civ = world.viewingCiv
        val view = world.selectedGameView
        val screen: BaseScreen = when (r.screen) {
            "tech-picker" -> TechPickerScreen(civ).apply { l10nSelect(selected.getString("technology")) }
            "city-production" -> {
                val cityKey = selected.get("city")
                val city = game.gameInfo!!.civilizations.filter { it.civID == cityKey.getString("owner") }
                    .flatMap { it.cities }.filter { it.name == cityKey.getString("name") }
                require(city.size == 1 && city.single().civ == civ) { "unknown_or_ambiguous_city" }
                val cityView = view.getCityView(city.single())
                val name = selected.getString("construction")
                require(name in game.gameInfo!!.ruleset.units || name in game.gameInfo!!.ruleset.buildings) { "unknown_construction" }
                CityScreen(cityView, cityView.constructions.getConstruction(name))
            }
            "empire-stats" -> EmpireOverviewScreen(view.civView, EmpireOverviewCategories.Stats)
            "policy-picker" -> PolicyPickerScreen(civ, world.isPlayersTurn).apply { l10nSetExpanded(r.expanded) }
            "victory-status" -> VictoryScreen(world, 0)
            "civilopedia" -> {
                val category = CivilopediaCategories.entries.singleOrNull { it.name == selected.getString("category") }
                    ?: throw IllegalArgumentException("unknown_civilopedia_category")
                CivilopediaScreen(game.gameInfo!!.ruleset, category, selected.getString("article")).apply {
                    l10nVerifyEntry(selected.getString("article"))
                }
            }
            "world" -> world.apply {
                val unitKey = selected.get("unit")
                val point = unitKey.get("tile")
                val units = game.gameInfo!!.civilizations.filter { it.civID == unitKey.getString("owner") }
                    .flatMap { it.units.getCivUnits().toList() }.filter {
                        it.name == unitKey.getString("name") && it.currentTile.position == Vector2(point.getFloat(0), point.getFloat(1)).toHexCoord()
                    }
                require(units.size == 1 && units.single().civ == civ) { "unknown_or_ambiguous_unit" }
                val camera = r.state.get("camera")
                val zoom = camera.getFloat("zoom")
                require(zoom in mapHolder.minZoom..mapHolder.maxZoom) { "zoom_out_of_range" }
                mapHolder.zoom(zoom)
                val center = camera.get("center_tile")
                val centerTile = Vector2(center.getFloat(0), center.getFloat(1)).toHexCoord()
                require(mapHolder.tileGroups.values.any { it.tileView.position() == centerTile }) { "camera_tile_not_available" }
                mapHolder.setCenterPosition(centerTile, immediately = true, selectUnit = false)
                bottomUnitTable.selectUnit(view.getMapUnitView(units.single()))
                shouldUpdate = true
                render(0f)
                mapHolder.name = "l10n-camera"
            }
            "city-state-diplomacy" -> {
                val other = civ.getKnownCivs().filter { it.civID == selected.getString("city_state") && it.isCityState }.toList()
                require(other.size == 1) { "city_state_not_met" }
                DiplomacyScreen(view.civView, view.getForeignCivView(other.single()))
            }
            else -> throw IllegalArgumentException("unknown_screen")
        }
        if (screen !== world) game.l10nPushScreen(screen)
        if (screen is PickerScreen) {
            screen.scrollPane.name = "l10n-pane:" + when (r.screen) {
                "tech-picker" -> "tech-tree"
                "policy-picker" -> "policy-tree"
                else -> "picker"
            }
            screen.pickerPane.descriptionScroll.name = "l10n-pane:description"
        }
        return screen
    }

    private fun finishState(screen: BaseScreen, r: L10nRequest) {
        if (screen is CityScreen) {
            val expanders = L10nScrollState.actors(screen.stage.root).filterIsInstance<ExpanderTab>()
            require(r.expanded.all { name -> expanders.any { it.title == name } }) { "unknown_expanded_section" }
            for (expander in expanders) expander.isOpen = expander.title in r.expanded
        }
    }

    private fun hasPendingWork(screen: BaseScreen) = when (screen) {
        is NewGameScreen -> screen.l10nHasPendingWork
        is CityScreen -> screen.l10nHasPendingWork
        is MainMenuScreen -> screen.l10nBackgroundPending
        else -> false
    }

    private fun checkTimeout() {
        require(System.nanoTime() - started < 60_000_000_000L) { "readiness_timeout" }
    }

    private fun fail(screen: String, token: String, reason: String) {
        loading = false; target = null; terminal = true
        game.l10nReset(object : BaseScreen() {})
        Gdx.input.inputProcessor = null
        emit("L10N_SCREEN_ERROR ${safe(screen)} request=${safe(token)} reason=${safe(reason)}")
    }

    /** Capture events are a protocol, independent of the optional general debug-log switch. */
    private fun emit(message: String) = Log.backend.debug(com.unciv.utils.Tag("L10nScreenHook"), Thread.currentThread().name, message)

    private fun safe(value: String) = value.replace(Regex("[^A-Za-z0-9._,:-]"), "_").take(512).ifBlank { "missing" }

    private fun newGameSetup(json: JsonValue): GameSetupInfo {
        val rulesetName = json.getString("ruleset")
        require(rulesetName in RulesetCache) { "unknown_ruleset" }
        val rules = RulesetCache[rulesetName]!!
        require(json.getString("difficulty") in rules.difficulties && json.getString("speed") in rules.speeds && json.getString("era") in rules.eras) { "unknown_new_game_option" }
        require(json.getString("map_type") in MapType.allValues && MapSize.Predefined.entries.any { it.name == json.getString("map_size") }) { "unknown_map_option" }
        return GameSetupInfo().apply {
            gameParameters.apply {
                baseRuleset = rulesetName
                difficulty = json.getString("difficulty"); speed = json.getString("speed")
                startingEra = json.getString("era"); maxTurns = json.getInt("max_turns")
                numberOfCityStates = json.getInt("city_states")
                players = ArrayList(json.get("players").map {
                    val name = it.getString("civilization")
                    require(name == "Random" || name in rules.nations) { "unknown_player_civilization" }
                    Player(name, PlayerType.valueOf(it.getString("type")))
                })
            }
            mapParameters.apply {
                type = json.getString("map_type"); mapSize = MapSize(json.getString("map_size"))
                seed = json.getLong("seed")
            }
        }
    }
}

/** Counts completed quiet draws, resetting on pending work or a changed layout. */
class L10nReadyFrames {
    var quietFrames = 0
        private set
    private var signature: List<Float>? = null
    fun reset() { quietFrames = 0; signature = null }
    fun rendered(quiet: Boolean, current: List<Float>): Boolean {
        quietFrames = if (!quiet) 0 else if (signature == null || signature == current) quietFrames + 1 else 1
        signature = current
        return quietFrames >= 2
    }
}
