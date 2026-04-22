package com.dream.smart_androidbot.model

import android.graphics.Rect

data class ElementNode(
    val overlayIndex: Int,
    val className: String,
    val text: String,
    val contentDescription: String,
    val resourceId: String,
    val isClickable: Boolean,
    val isScrollable: Boolean,
    val isCheckable: Boolean,
    val isChecked: Boolean,
    val isEditable: Boolean,
    val isSelected: Boolean,
    val isEnabled: Boolean,
    val bounds: Rect,
    val depth: Int,
    val parent: ElementNode? = null,
    val children: MutableList<ElementNode> = mutableListOf()
) {
    fun createId(): String {
        val parts = mutableListOf<String>()
        if (resourceId.isNotEmpty()) parts.add(resourceId.substringAfterLast("/"))
        if (text.isNotEmpty()) parts.add(text.take(20))
        if (contentDescription.isNotEmpty()) parts.add(contentDescription.take(20))
        if (parts.isEmpty()) parts.add(className.substringAfterLast("."))
        return parts.joinToString("|")
    }

    fun getCenterX(): Int = (bounds.left + bounds.right) / 2
    fun getCenterY(): Int = (bounds.top + bounds.bottom) / 2
}
