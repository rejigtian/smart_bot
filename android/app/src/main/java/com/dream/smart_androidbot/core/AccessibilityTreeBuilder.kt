package com.dream.smart_androidbot.core

import android.graphics.Rect
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONArray
import org.json.JSONObject

/**
 * Builds a JSON representation of the accessibility tree.
 * Field names match what the backend's perception.py / format_ui_state() expects:
 *   - boundsInScreen (not "bounds")
 *   - isVisibleToUser
 *   - isFocusable
 *   - children
 */
object AccessibilityTreeBuilder {

    fun buildFullAccessibilityTreeJson(
        node: AccessibilityNodeInfo?,
        screenBounds: Rect? = null
    ): JSONObject {
        if (node == null) return JSONObject()
        val obj = JSONObject()
        try {
            obj.put("className", node.className ?: "")
            obj.put("text", node.text?.toString() ?: "")
            obj.put("contentDescription", node.contentDescription?.toString() ?: "")
            obj.put("resourceId", node.viewIdResourceName ?: "")
            obj.put("isClickable", node.isClickable)
            obj.put("isScrollable", node.isScrollable)
            obj.put("isCheckable", node.isCheckable)
            obj.put("isChecked", node.isChecked)
            obj.put("isEditable", node.isEditable)
            obj.put("isSelected", node.isSelected)
            obj.put("isEnabled", node.isEnabled)
            obj.put("isFocusable", node.isFocusable)
            obj.put("isFocused", node.isFocused)
            obj.put("isVisibleToUser", node.isVisibleToUser)

            val boundsRect = Rect()
            node.getBoundsInScreen(boundsRect)
            val boundsObj = JSONObject()
            boundsObj.put("left", boundsRect.left)
            boundsObj.put("top", boundsRect.top)
            boundsObj.put("right", boundsRect.right)
            boundsObj.put("bottom", boundsRect.bottom)
            obj.put("boundsInScreen", boundsObj)  // matches perception.py field name

            if (screenBounds != null) {
                obj.put("visiblePercent", getVisiblePercentage(boundsRect, screenBounds))
            }

            val children = JSONArray()
            for (i in 0 until node.childCount) {
                val child = node.getChild(i) ?: continue
                children.put(buildFullAccessibilityTreeJson(child, screenBounds))
                child.recycle()
            }
            if (children.length() > 0) {
                obj.put("children", children)
            }
        } catch (e: Exception) {
            obj.put("error", e.message ?: "unknown")
        }
        return obj
    }

    fun getVisiblePercentage(rect: Rect, screenBounds: Rect): Float {
        val intersection = Rect(rect)
        if (!intersection.intersect(screenBounds)) return 0f
        val intersectionArea = intersection.width().toFloat() * intersection.height()
        val nodeArea = rect.width().toFloat() * rect.height()
        return if (nodeArea <= 0f) 0f else intersectionArea / nodeArea
    }
}
