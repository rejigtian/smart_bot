package com.dream.smart_androidbot.config

import android.content.Context
import android.content.SharedPreferences
import java.util.UUID

/**
 * Simplified config: stores server URL + auth token for the reverse WebSocket connection.
 * No cloud tasks, no triggers, no keepalive state.
 */
class ConfigManager private constructor(context: Context) {

    private val prefs: SharedPreferences =
        context.getSharedPreferences("smart_agent_config", Context.MODE_PRIVATE)

    companion object {
        private const val KEY_SERVER_URL = "server_url"
        private const val KEY_TOKEN = "token"
        private const val KEY_DEVICE_ID = "device_id"
        private const val KEY_DEVICE_NAME = "device_name"
        private const val KEY_KEEP_ALIVE_ENABLED = "keep_alive_enabled"

        const val DEFAULT_SERVER_URL = "ws://10.0.2.2:8000/v1/providers/join"

        @Volatile
        private var instance: ConfigManager? = null

        fun getInstance(context: Context): ConfigManager {
            return instance ?: synchronized(this) {
                instance ?: ConfigManager(context.applicationContext).also { instance = it }
            }
        }
    }

    var serverUrl: String
        get() = prefs.getString(KEY_SERVER_URL, DEFAULT_SERVER_URL) ?: DEFAULT_SERVER_URL
        set(value) = prefs.edit().putString(KEY_SERVER_URL, value).apply()

    var token: String
        get() = prefs.getString(KEY_TOKEN, "") ?: ""
        set(value) = prefs.edit().putString(KEY_TOKEN, value).apply()

    /** Stable per-device identifier, auto-generated on first run. */
    var deviceId: String
        get() {
            val stored = prefs.getString(KEY_DEVICE_ID, "") ?: ""
            if (stored.isNotEmpty()) return stored
            val newId = UUID.randomUUID().toString()
            prefs.edit().putString(KEY_DEVICE_ID, newId).apply()
            return newId
        }
        set(value) = prefs.edit().putString(KEY_DEVICE_ID, value).apply()

    var deviceName: String
        get() = prefs.getString(KEY_DEVICE_NAME, android.os.Build.MODEL) ?: android.os.Build.MODEL
        set(value) = prefs.edit().putString(KEY_DEVICE_NAME, value).apply()

    /** When true: keep-alive service starts on boot and monitors services automatically. */
    var keepAliveEnabled: Boolean
        get() = prefs.getBoolean(KEY_KEEP_ALIVE_ENABLED, false)
        set(value) = prefs.edit().putBoolean(KEY_KEEP_ALIVE_ENABLED, value).apply()
}
