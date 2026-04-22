package com.dream.smart_androidbot.core

import android.graphics.Rect
import com.dream.smart_androidbot.model.ElementNode
import com.dream.smart_androidbot.model.PhoneState
import com.dream.smart_androidbot.service.AgentAccessibilityService
import org.json.JSONObject

object StateRepository {

    private fun service(): AgentAccessibilityService? =
        AgentAccessibilityService.getInstance()

    fun getVisibleElements(): List<ElementNode> =
        service()?.getVisibleElements() ?: emptyList()

    fun getFullTree(filter: Boolean = true): JSONObject =
        service()?.getFullTreeJson(filter) ?: JSONObject()

    fun getPhoneState(): PhoneState? =
        service()?.getPhoneState()

    suspend fun takeScreenshot(hideOverlay: Boolean = false): String =
        service()?.takeScreenshotBase64(hideOverlay) ?: ""

    suspend fun inputText(text: String, clear: Boolean = false): Boolean =
        service()?.inputText(text, clear) ?: false

    fun getScreenBounds(): Rect? =
        service()?.getScreenBounds()
}
