package com.dream.smart_androidbot.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import com.dream.smart_androidbot.R
import com.dream.smart_androidbot.config.ConfigManager
import kotlinx.coroutines.*
import org.java_websocket.client.WebSocketClient
import org.java_websocket.handshake.ServerHandshake
import org.json.JSONObject
import java.net.URI
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Foreground service that maintains a reverse WebSocket connection to the backend.
 *
 * Reliability patterns (modeled after droidrun-portal):
 *  - `connectionLostTimeout` on the WebSocketClient → library-level ping/pong
 *    catches dead connections without waiting for next send.
 *  - Reconnect timer is tracked from the LAST FAILURE, not service start —
 *    so a 30-min reconnect budget resets every successful connect.
 *  - Terminal HTTP errors (401/403/400) stop retrying immediately.
 *  - Handler-based scheduling with `isReconnecting` guard prevents duplicate
 *    reconnect attempts from overlapping onError/onClose callbacks.
 *  - Old connections are explicitly closed before creating a new one
 *    (prevents zombie ws eating RAM/sockets).
 */
class ReverseConnectionService : Service() {

    companion object {
        private const val TAG = "ReverseConn"
        private const val NOTIFICATION_ID = 1001
        private const val CHANNEL_ID = "smart_agent_channel"
        private const val RECONNECT_DELAY_MS = 3_000L
        private const val RECONNECT_GIVE_UP_MS = 30 * 60 * 1000L  // 30 min since FIRST failure
        private const val CONNECTION_LOST_TIMEOUT_SEC = 30

        const val ACTION_START = "com.dream.smart_androidbot.ACTION_START"
        const val ACTION_STOP  = "com.dream.smart_androidbot.ACTION_STOP"

        @Volatile private var instance: ReverseConnectionService? = null
        fun isRunning(): Boolean = instance != null

        /** 401/403/400 — don't retry; configuration is broken. */
        internal fun isTerminalClose(reason: String?): Boolean {
            if (reason == null) return false
            return reason.contains("Unauthorized", ignoreCase = true) ||
                    reason.contains("Forbidden", ignoreCase = true) ||
                    reason.contains("Bad Request", ignoreCase = true) ||
                    reason.startsWith("401") ||
                    reason.startsWith("403") ||
                    reason.startsWith("400")
        }
    }

    @Volatile private var webSocketClient: AgentWebSocketClient? = null

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val handler = Handler(Looper.getMainLooper())
    private val isServiceRunning = AtomicBoolean(false)
    private val isReconnecting = AtomicBoolean(false)

    // Timer starts on FIRST failure; resets to 0 on successful connect.
    @Volatile private var reconnectStartedAtMs = 0L

    private var dispatcher: ActionDispatcher? = null
    private var isForeground = false
    private lateinit var config: ConfigManager

    // ── Lifecycle ─────────────────────────────────────────────────────────────

    override fun onCreate() {
        super.onCreate()
        instance = this
        config = ConfigManager.getInstance(this)
        dispatcher = ActionDispatcher(this)
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                stopSelf()
                return START_NOT_STICKY
            }
            else -> {
                ensureForeground()
                if (isServiceRunning.compareAndSet(false, true)) {
                    connectToHost()
                }
            }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        isServiceRunning.set(false)
        isReconnecting.set(false)
        handler.removeCallbacksAndMessages(null)
        scope.cancel()
        disconnect()
        if (isForeground) {
            @Suppress("DEPRECATION")
            stopForeground(true)
            isForeground = false
        }
        if (instance === this) instance = null
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    // ── Connect / reconnect ───────────────────────────────────────────────────

    private fun connectToHost() {
        if (!isServiceRunning.get()) return

        val url = config.serverUrl
        val token = config.token
        if (url.isBlank() || token.isBlank()) {
            Log.w(TAG, "Server URL or token not configured")
            updateNotification("Not configured")
            return
        }

        try {
            updateNotification("Connecting…")
            // Prevent zombie — always close previous before creating new
            disconnect()

            val uri = URI(url)
            val headers = mapOf(
                "Authorization" to "Bearer $token",
                "X-Device-ID"   to config.deviceId,
                "X-Device-Name" to config.deviceName,
            )
            Log.i(TAG, "Connecting to $url (device=${config.deviceId})")

            val client = AgentWebSocketClient(
                uri = uri,
                headers = headers,
                openedCallback = {
                    Log.i(TAG, "Connected")
                    reconnectStartedAtMs = 0L    // reset give-up timer on success
                    updateNotification("Connected to ${uri.host}")
                },
                messageCallback = ::handleMessage,
                closedCallback = { code, reason, _ ->
                    Log.w(TAG, "Disconnected code=$code reason=$reason")
                    logNetworkState("onClose")
                    if (isTerminalClose(reason)) {
                        Log.w(TAG, "Terminal error — not retrying")
                        updateNotification("Auth failed (${reason ?: "terminal"})")
                        isReconnecting.set(false)
                        handler.removeCallbacksAndMessages(null)
                    } else {
                        scheduleReconnect()
                    }
                },
                errorCallback = { ex ->
                    Log.e(TAG, "WS error: ${ex?.message}")
                    logNetworkState("onError")
                    scheduleReconnect()  // safety net — onClose usually follows
                },
            )

            // Library-level ping/pong — detects dead connections within 30s
            client.connectionLostTimeout = CONNECTION_LOST_TIMEOUT_SEC
            webSocketClient = client
            client.connect()
        } catch (e: Exception) {
            Log.e(TAG, "Failed to initiate connection: ${e.message}")
            scheduleReconnect()
        }
    }

    private fun scheduleReconnect() {
        if (!isServiceRunning.get()) return
        if (isReconnecting.getAndSet(true)) return  // already scheduled

        val now = SystemClock.elapsedRealtime()
        if (reconnectStartedAtMs <= 0L) {
            reconnectStartedAtMs = now
        }

        if (now - reconnectStartedAtMs >= RECONNECT_GIVE_UP_MS) {
            Log.w(TAG, "Giving up after ${(now - reconnectStartedAtMs) / 60_000}min of retries")
            isReconnecting.set(false)
            reconnectStartedAtMs = 0L
            updateNotification("Disconnected (retry exhausted)")
            return
        }

        updateNotification("Reconnecting in ${RECONNECT_DELAY_MS / 1000}s…")
        handler.postDelayed({
            if (isServiceRunning.get()) {
                isReconnecting.set(false)
                connectToHost()
            } else {
                isReconnecting.set(false)
            }
        }, RECONNECT_DELAY_MS)
    }

    private fun disconnect() {
        try {
            webSocketClient?.close()
        } catch (_: Exception) {}
        webSocketClient = null
    }

    // ── Message handling ──────────────────────────────────────────────────────

    private fun handleMessage(raw: String, sender: AgentWebSocketClient) {
        scope.launch {
            try {
                val json = JSONObject(raw)
                val id: Any? = json.opt("id")
                val method = json.optString("method", "")
                val params = json.optJSONObject("params") ?: JSONObject()

                Log.d(TAG, "← $method (id=$id)")
                val response = dispatcher?.dispatch(method, params)
                    ?: com.dream.smart_androidbot.api.ApiResponse.Error("Dispatcher not ready")
                sender.send(response.toJson(id).toString())
            } catch (e: Exception) {
                Log.e(TAG, "Message handling error: ${e.message}")
                try {
                    val id = runCatching { JSONObject(raw).opt("id") }.getOrNull()
                    val err = JSONObject()
                    if (id != null) err.put("id", id)
                    err.put("status", "error")
                    err.put("result", "Internal error: ${e.message}")
                    sender.send(err.toString())
                } catch (_: Exception) {}
            }
        }
    }

    // ── Network diagnostics ───────────────────────────────────────────────────

    private fun logNetworkState(prefix: String) {
        try {
            val cm = getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
            val net = cm?.activeNetwork
            val caps = cm?.getNetworkCapabilities(net)
            if (caps == null) {
                Log.d(TAG, "$prefix network=unknown")
                return
            }
            val t = buildList {
                if (caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) add("wifi")
                if (caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)) add("cellular")
                if (caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)) add("ethernet")
                if (caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) add("vpn")
            }
            val validated = caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)
            Log.d(TAG, "$prefix network=${t.joinToString(",")} validated=$validated")
        } catch (_: Exception) {}
    }

    // ── Notification ──────────────────────────────────────────────────────────

    private fun createNotificationChannel() {
        val channel = NotificationChannel(
            CHANNEL_ID, "Smart Agent Connection",
            NotificationManager.IMPORTANCE_LOW,
        ).apply { description = "Maintains reverse WebSocket connection to the agent server" }
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    private fun buildNotification(text: String): Notification =
        Notification.Builder(this, CHANNEL_ID)
            .setContentTitle("Smart Agent")
            .setContentText(text)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setOngoing(true)
            .build()

    private fun updateNotification(text: String) {
        if (!isForeground) return
        getSystemService(NotificationManager::class.java)
            .notify(NOTIFICATION_ID, buildNotification(text))
    }

    private fun ensureForeground() {
        if (isForeground) return
        try {
            val notif = buildNotification("Starting…")
            startForeground(
                NOTIFICATION_ID, notif,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC,
            )
            isForeground = true
        } catch (e: Exception) {
            Log.e(TAG, "Failed to start foreground: ${e.message}")
        }
    }
}

// ── WebSocketClient ───────────────────────────────────────────────────────────

internal class AgentWebSocketClient(
    uri: URI,
    headers: Map<String, String>,
    private val openedCallback: () -> Unit,
    private val messageCallback: (String, AgentWebSocketClient) -> Unit,
    private val closedCallback: (Int, String?, Boolean) -> Unit,
    private val errorCallback: (Exception?) -> Unit,
) : WebSocketClient(uri, org.java_websocket.drafts.Draft_6455(), headers, 5_000) {

    override fun onOpen(handshakedata: ServerHandshake?) {
        Log.i("AgentWS", "Connected (http=${handshakedata?.httpStatus})")
        openedCallback()
    }

    override fun onMessage(message: String?) {
        if (message != null) messageCallback(message, this)
    }

    override fun onClose(code: Int, reason: String?, remote: Boolean) {
        closedCallback(code, reason, remote)
    }

    override fun onError(ex: Exception?) {
        Log.e("AgentWS", "Error: ${ex?.javaClass?.simpleName}: ${ex?.message}")
        errorCallback(ex)
    }
}
