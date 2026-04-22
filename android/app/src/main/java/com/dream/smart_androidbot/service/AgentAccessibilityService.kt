package com.dream.smart_androidbot.service

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.AccessibilityServiceInfo
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Rect
import android.os.Handler
import android.os.Looper
import android.util.DisplayMetrics
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import com.dream.smart_androidbot.core.AccessibilityTreeBuilder
import com.dream.smart_androidbot.input.AgentKeyboardIME
import com.dream.smart_androidbot.model.ElementNode
import com.dream.smart_androidbot.model.PhoneState
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONObject
import kotlin.coroutines.resume
import java.io.ByteArrayOutputStream
import android.util.Base64
import android.util.Log

class AgentAccessibilityService : AccessibilityService() {

    companion object {
        private const val TAG = "AgentA11y"

        @Volatile
        private var instance: AgentAccessibilityService? = null

        fun getInstance(): AgentAccessibilityService? = instance
    }

    private val mainHandler = Handler(Looper.getMainLooper())

    // ── Lifecycle ─────────────────────────────────────────────────────────────

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        val info = serviceInfo ?: AccessibilityServiceInfo()
        info.flags = info.flags or
                AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS or
                AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS or
                AccessibilityServiceInfo.FLAG_REQUEST_TOUCH_EXPLORATION_MODE
        info.notificationTimeout = 0
        serviceInfo = info
    }

    override fun onInterrupt() {}

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {}

    override fun onDestroy() {
        super.onDestroy()
        if (instance === this) instance = null
    }

    // ── Screen bounds ─────────────────────────────────────────────────────────

    fun getScreenBounds(): Rect {
        val wm = getSystemService(WINDOW_SERVICE) as WindowManager
        val metrics = DisplayMetrics()
        @Suppress("DEPRECATION")
        wm.defaultDisplay.getRealMetrics(metrics)
        return Rect(0, 0, metrics.widthPixels, metrics.heightPixels)
    }

    // ── Phone state ───────────────────────────────────────────────────────────

    fun getPhoneState(): PhoneState {
        val root = rootInActiveWindow
        val focused = findFocusedNode(root)
        val keyboardVisible = isKeyboardVisible()
        val pkgName = root?.packageName?.toString()
        val appName = pkgName?.let { getAppName(it) }
        val isEditable = focused?.isEditable ?: false
        val activityName = getTopActivityName()
        root?.recycle()
        return PhoneState(
            focusedElement = focused,
            keyboardVisible = keyboardVisible,
            packageName = pkgName,
            appName = appName,
            isEditable = isEditable,
            activityName = activityName
        )
    }

    private fun findFocusedNode(root: AccessibilityNodeInfo?): AccessibilityNodeInfo? {
        return root?.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)
    }

    private fun isKeyboardVisible(): Boolean {
        for (window in windows) {
            if (window.type == AccessibilityWindowInfo.TYPE_INPUT_METHOD) return true
        }
        return false
    }

    private fun getAppName(packageName: String): String? {
        return try {
            val pm = packageManager
            val appInfo: ApplicationInfo = pm.getApplicationInfo(packageName, 0)
            pm.getApplicationLabel(appInfo).toString()
        } catch (e: PackageManager.NameNotFoundException) {
            null
        }
    }

    private fun getTopActivityName(): String? {
        for (window in windows) {
            if (window.type == AccessibilityWindowInfo.TYPE_APPLICATION) {
                return window.root?.className?.toString()
            }
        }
        return null
    }

    // ── Visible elements (flat indexed list) ─────────────────────────────────

    fun getVisibleElements(): List<ElementNode> {
        val root = rootInActiveWindow ?: return emptyList()
        val screen = getScreenBounds()
        val elements = mutableListOf<ElementNode>()
        var index = 0
        traverseForElements(root, screen, elements, 0) { index++ }
        root.recycle()
        return elements
    }

    private fun traverseForElements(
        node: AccessibilityNodeInfo,
        screen: Rect,
        elements: MutableList<ElementNode>,
        depth: Int,
        nextIndex: () -> Int
    ) {
        val bounds = Rect()
        node.getBoundsInScreen(bounds)

        // Skip fully off-screen nodes
        if (!Rect.intersects(bounds, screen)) {
            return
        }

        val text = node.text?.toString() ?: ""
        val desc = node.contentDescription?.toString() ?: ""
        val resourceId = node.viewIdResourceName ?: ""
        val className = node.className?.toString() ?: ""
        val isClickable = node.isClickable || node.isCheckable
        val isScrollable = node.isScrollable
        val isEditable = node.isEditable

        val isInteresting = isClickable || isScrollable || isEditable ||
                text.isNotEmpty() || desc.isNotEmpty()

        var element: ElementNode? = null
        if (isInteresting) {
            element = ElementNode(
                overlayIndex = nextIndex(),
                className = className,
                text = text,
                contentDescription = desc,
                resourceId = resourceId,
                isClickable = isClickable,
                isScrollable = isScrollable,
                isCheckable = node.isCheckable,
                isChecked = node.isChecked,
                isEditable = isEditable,
                isSelected = node.isSelected,
                isEnabled = node.isEnabled,
                bounds = bounds,
                depth = depth
            )
            elements.add(element)
        }

        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            traverseForElements(child, screen, elements, depth + 1, nextIndex)
            child.recycle()
        }
    }

    // ── Full accessibility tree (for structured state reporting) ──────────────

    fun getFullTreeJson(filter: Boolean = true): JSONObject {
        val root = rootInActiveWindow ?: return JSONObject()
        val screen = getScreenBounds()
        val result = AccessibilityTreeBuilder.buildFullAccessibilityTreeJson(root, screen)
        root.recycle()
        return result
    }

    // ── Screenshot ────────────────────────────────────────────────────────────

    suspend fun takeScreenshotBase64(hideOverlay: Boolean = false): String {
        Log.d(TAG, "takeScreenshot: requesting…")
        val result = withTimeoutOrNull(12_000L) {
            suspendCancellableCoroutine { cont ->
                takeScreenshot(
                    0,  // default display
                    mainExecutor,
                    object : TakeScreenshotCallback {
                        override fun onSuccess(screenshotResult: ScreenshotResult) {
                            try {
                                val hardwareBuffer = screenshotResult.getHardwareBuffer()
                                val colorSpace = screenshotResult.getColorSpace()
                                val hardwareBitmap = Bitmap.wrapHardwareBuffer(hardwareBuffer, colorSpace)
                                hardwareBuffer.close()
                                if (hardwareBitmap == null) {
                                    Log.w(TAG, "takeScreenshot: hardwareBitmap is null")
                                    cont.resume("")
                                    return
                                }
                                val softBitmap = hardwareBitmap.copy(Bitmap.Config.ARGB_8888, false)
                                hardwareBitmap.recycle()
                                val out = ByteArrayOutputStream()
                                softBitmap.compress(Bitmap.CompressFormat.JPEG, 75, out)
                                softBitmap.recycle()
                                val b64 = Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP)
                                Log.d(TAG, "takeScreenshot: success ${out.size()} bytes")
                                cont.resume(b64)
                            } catch (e: Exception) {
                                Log.e(TAG, "takeScreenshot: processing error ${e.message}")
                                cont.resume("")
                            }
                        }

                        override fun onFailure(errorCode: Int) {
                            Log.w(TAG, "takeScreenshot: onFailure errorCode=$errorCode")
                            cont.resume("")
                        }
                    }
                )
            }
        }
        if (result == null) {
            Log.w(TAG, "takeScreenshot: timed out (no callback in 12s)")
        }
        return result ?: ""
    }

    // ── Text input ────────────────────────────────────────────────────────────

    suspend fun inputText(text: String, clear: Boolean = false): Boolean {
        // Try IME approach first (works with focused editable fields)
        if (AgentKeyboardIME.inputText(text, clear)) return true

        // Fallback: set text via accessibility action on focused node
        val root = rootInActiveWindow ?: return false
        val focused = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)
        root.recycle()
        if (focused != null) {
            val args = android.os.Bundle()
            args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
            val result = focused.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
            focused.recycle()
            return result
        }
        return false
    }

    // ── Package list ──────────────────────────────────────────────────────────

    fun getInstalledPackages(): List<String> {
        return try {
            packageManager.getInstalledPackages(0).map { it.packageName }
        } catch (e: Exception) {
            emptyList()
        }
    }

    // ── Node lookup by index ──────────────────────────────────────────────────

    fun findNodeByIndex(index: Int): AccessibilityNodeInfo? {
        val root = rootInActiveWindow ?: return null
        val screen = getScreenBounds()
        var counter = 0
        val result = findNodeByIndexRecursive(root, screen, index) { counter++ }
        root.recycle()
        return result
    }

    private fun findNodeByIndexRecursive(
        node: AccessibilityNodeInfo,
        screen: Rect,
        targetIndex: Int,
        nextIndex: () -> Int
    ): AccessibilityNodeInfo? {
        val bounds = Rect()
        node.getBoundsInScreen(bounds)
        if (!Rect.intersects(bounds, screen)) return null

        val text = node.text?.toString() ?: ""
        val desc = node.contentDescription?.toString() ?: ""
        val isInteresting = node.isClickable || node.isCheckable || node.isScrollable ||
                node.isEditable || text.isNotEmpty() || desc.isNotEmpty()

        if (isInteresting) {
            val idx = nextIndex()
            if (idx == targetIndex) return node
        }

        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            val found = findNodeByIndexRecursive(child, screen, targetIndex, nextIndex)
            if (found != null) {
                if (found !== child) child.recycle()
                return found
            }
            child.recycle()
        }
        return null
    }
}
