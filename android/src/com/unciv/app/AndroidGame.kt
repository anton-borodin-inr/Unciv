package com.unciv.app

import android.app.Activity
import android.content.Intent
import android.graphics.Rect
import android.net.Uri
import android.os.Build
import android.os.Debug
import android.view.View
import android.view.ViewTreeObserver
import com.badlogic.gdx.Gdx
import com.badlogic.gdx.backends.android.AndroidGraphics
import com.badlogic.gdx.math.Rectangle
import com.unciv.UncivGame
import com.unciv.logic.IdChecker
import com.unciv.logic.event.EventBus
import com.unciv.ui.screens.basescreen.BaseScreen
import com.unciv.ui.screens.basescreen.UncivStage
import com.unciv.utils.Concurrency
import com.unciv.utils.isUUID
import java.util.Locale

class AndroidGame(private val activity: Activity) : UncivGame() {

    private var lastOrientation = activity.resources.configuration.orientation

    /** Parse extras on both cold start and onNewIntent. Release APKs ignore this debug protocol. */
    fun setL10nIntent(intent: Intent): Boolean {
        val present = listOf("l10n_screen", "l10n_request", "l10n_state_b64").any { intent.hasExtra(it) }
        if (activity.applicationInfo.flags and android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE == 0) return false
        if (!present) {
            if (l10nHook != null) Concurrency.runOnGLThread { l10nHook?.requestLeave() }
            return false
        }
        val screen = intent.getStringExtra("l10n_screen") ?: "missing"
        val token = intent.getStringExtra("l10n_request") ?: "missing"
        val decoded = try {
            require(intent.getStringExtra("l10n_request") != null)
            val encoded = intent.getStringExtra("l10n_state_b64") ?: error("missing")
            require(encoded.length <= 43692 && encoded.matches(Regex("[A-Za-z0-9+/]*={0,2}")))
            val bytes = android.util.Base64.decode(encoded, android.util.Base64.DEFAULT)
            require(android.util.Base64.encodeToString(bytes, android.util.Base64.NO_WRAP) == encoded)
            Charsets.UTF_8.newDecoder().onMalformedInput(java.nio.charset.CodingErrorAction.REPORT)
                .decode(java.nio.ByteBuffer.wrap(bytes)).toString()
        } catch (_: Exception) { null }
        val submit = {
            val hook = l10nHook ?: com.unciv.l10n.L10nScreenHook(this).also { l10nHook = it }
            hook.enqueue(screen, token, decoded)
        }
        if (isInitialized) Concurrency.runOnGLThread { submit() } else submit()
        return true
    }

    fun addScreenObscuredListener() {
        val contentView = (Gdx.graphics as AndroidGraphics).view
        contentView.viewTreeObserver.addOnGlobalLayoutListener(object : ViewTreeObserver.OnGlobalLayoutListener {

            /** [onGlobalLayout] gets triggered not only when the [View.getWindowVisibleDisplayFrame]
             * changes, but also on other things. So we need to check if that was actually
             * the thing that changed. */
            private var lastFrame: Rect? = null
            private var lastVisibleStage: Rectangle? = null

            override fun onGlobalLayout() {

                if (!isInitialized || screen == null)
                    return

                val currentFrame = Rect()
                contentView.getWindowVisibleDisplayFrame(currentFrame)

                val stage = (screen as BaseScreen).stage
                val horizontalRatio = stage.width / contentView.width
                val verticalRatio = stage.height / contentView.height

                // Android coordinate system has the origin in the top left,
                // while GDX uses bottom left.

                val visibleStage = Rectangle(
                    currentFrame.left * horizontalRatio,
                    (contentView.height - currentFrame.bottom)  * verticalRatio,
                    currentFrame.width() * horizontalRatio,
                    currentFrame.height() * verticalRatio
                )

                if (lastFrame == currentFrame && lastVisibleStage == visibleStage)
                    return
                lastFrame = currentFrame
                lastVisibleStage = visibleStage

                val currentOrientation = activity.resources.configuration.orientation
                if (lastOrientation != currentOrientation) {
                    lastOrientation = currentOrientation
                    return
                }

                Concurrency.runOnGLThread {
                    EventBus.send(UncivStage.VisibleAreaChanged(visibleStage))
                }
            }
        })
    }

    /** This is needed in onCreate _and_ onNewIntent to open links and notifications
     *  correctly even if the app was not running */
    fun setDeepLinkedGame(intent: Intent) {
        if (intent.action != Intent.ACTION_VIEW) {
            deepLinkedMultiplayerGame = null
        }
        val uri: Uri? = intent.data
        val idParam = uri?.getQueryParameter("id") //legacy game url
        deepLinkedMultiplayerGame = 
            if (idParam != null && idParam.isUUID()) idParam
            else if (IdChecker.isGameDeepLink(uri.toString())) IdChecker.checkAndReturnUuiId(uri.toString())
            else null
    }

    fun isInitializedProxy() = super.isInitialized

    override fun getGcCount(): Int = 
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) Debug.getRuntimeStat("art.gc.gc-count").toInt() else 0

    override fun getDefaultLocale(): Locale =
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) super.getDefaultLocale()
        else activity.resources.configuration.locales.get(0)
}
