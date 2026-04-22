package com.dream.smart_androidbot.model

import android.view.accessibility.AccessibilityNodeInfo

data class PhoneState(
    val focusedElement: AccessibilityNodeInfo?,
    val keyboardVisible: Boolean,
    val packageName: String?,
    val appName: String?,
    val isEditable: Boolean,
    val activityName: String?
)
