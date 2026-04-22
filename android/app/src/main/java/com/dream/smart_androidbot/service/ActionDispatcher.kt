package com.dream.smart_androidbot.service

import android.content.Context
import com.dream.smart_androidbot.api.ApiHandler
import com.dream.smart_androidbot.api.ApiResponse
import org.json.JSONObject

/**
 * Routes JSON-RPC method strings to ApiHandler calls.
 *
 * Method names and parameter shapes match what ws_device.py (the backend) sends.
 *
 *   tap              { x, y }                           — absolute device pixels
 *   swipe            { startX, startY, endX, endY, duration? }  — absolute pixels
 *   keyboard/input   { base64_text, clear? }
 *   keyboard/key     { key_code }                       — Android KeyEvent.KEYCODE_*
 *   screenshot       { hideOverlay? }
 *   state            { filter? }
 *   state/full-tree
 *   app              { package, activity?, stopBeforeLaunch? }
 *   app/stop         { package }
 *   packages
 *   globalAction     { action }                         — AccessibilityService.GLOBAL_ACTION_* int
 *   time             {}                                 — returns current epoch ms
 *   ping
 */
class ActionDispatcher(context: Context) {

    private val handler = ApiHandler(context)

    suspend fun dispatch(method: String, params: JSONObject): ApiResponse {
        return when (method) {
            "ping" -> handler.ping()

            // Tap at absolute pixel coordinates
            "tap" -> handler.performTapAbs(
                params.optInt("x", 0),
                params.optInt("y", 0)
            )

            // Swipe with absolute pixel coordinates
            "swipe" -> handler.performSwipeAbs(
                params.optInt("startX", 0),
                params.optInt("startY", 0),
                params.optInt("endX", 0),
                params.optInt("endY", 0),
                params.optLong("duration", 500)
            )

            // Text input via base64-encoded UTF-8 text
            "keyboard/input" -> {
                val b64 = params.optString("base64_text", "")
                val text = try {
                    String(android.util.Base64.decode(b64, android.util.Base64.DEFAULT), Charsets.UTF_8)
                } catch (e: Exception) {
                    b64  // fallback: treat as plain text
                }
                handler.inputText(text, params.optBoolean("clear", false))
            }

            // Key press by Android KeyEvent key code integer
            "keyboard/key" -> handler.pressKeyCode(
                params.optInt("key_code", 0)
            )

            // Launch app (optionally stop before launch)
            "app" -> {
                if (params.optBoolean("stopBeforeLaunch", false)) {
                    handler.stopApp(params.optString("package"))
                }
                handler.launchApp(
                    params.getString("package"),
                    params.optString("activity", "")
                )
            }

            "app/stop" -> handler.stopApp(params.getString("package"))

            // Download APK from URL and launch system installer
            "install" -> {
                val url = params.optString("url", "")
                if (url.isBlank()) ApiResponse.Error("install: url param required")
                else handler.installApk(url)
            }

            "packages" -> handler.getPackages()

            "state" -> handler.getState()

            "state/full-tree" -> handler.getFullTree()

            "screenshot" -> handler.screenshot()

            // Global action by AccessibilityService.GLOBAL_ACTION_* integer
            "globalAction" -> handler.performGlobalActionCode(params.optInt("action", 0))

            // Current device time (epoch ms + ISO string)
            "time" -> ApiResponse.RawObject(
                JSONObject()
                    .put("timestamp", System.currentTimeMillis())
                    .put("datetime", java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss", java.util.Locale.US)
                        .apply { timeZone = java.util.TimeZone.getDefault() }
                        .format(java.util.Date()))
            )

            else -> ApiResponse.Error("Unknown method: $method")
        }
    }
}
