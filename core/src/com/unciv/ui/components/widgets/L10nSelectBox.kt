package com.unciv.ui.components.widgets

import com.badlogic.gdx.graphics.g2d.Batch
import com.badlogic.gdx.scenes.scene2d.ui.SelectBox
import com.badlogic.gdx.scenes.scene2d.ui.Skin
import com.unciv.l10n.L10nDrawObserver

/** Selected values use BitmapFont.draw's shared cache rather than a Label-owned cache. */
open class L10nSelectBox<T>(skin: Skin) : SelectBox<T>(skin) {
    override fun draw(batch: Batch, parentAlpha: Float) = L10nDrawObserver.withOwner(this) {
        super.draw(batch, parentAlpha)
    }
}
