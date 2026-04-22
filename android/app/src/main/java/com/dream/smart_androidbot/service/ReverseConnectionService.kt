package com.dream.smart_androidbot.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.os.IBinder
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
 * Foreground service that maintains a reverse WebSocket connection to the backend server.
 *
 * The device (Android) is the WS *client*; the server is the WS *server*.
 * On each JSON-RPC request, the device dispatches the action and sends the result back
 * over the same connection.
 *
 * Protocol:
 *   Incoming: { "id": "<uuid>", "method": "tap", "params": { "x": 500, "y": 500 } }
 *   Outgoing: { "id": "<uuid>", "status": "success", "result": "Tapped (500, 500)" }
 */
class ReverseConnectionService : Service() {

    companion object {
        private const val TAG = "ReverseConn"
        private const val NOTIFICATION_ID = 1001
        private const val CHANNEL_ID = "smart_agent_channel"
        private const val RECONNECT_DELAY_MS = 5_000L
        private const val MAX_RECONNECT_DURATION_MS = 30 * 60 * 1000L  // 30 minutes

        const val ACTION_START = "com.dream.smart_androidbot.ACTION_START"
        const val ACTION_STOP  = "com.dream.smart_androidbot.ACTION_STOP"

        @Volatile private var instance: ReverseConnectionService? = null
        fun isRunning(): Boolean = instance != null
    }

    // current active client — set when connected, cleared when closed
    @Volatile private var activeClient: AgentWebSocketClient? = null

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val running = AtomicBoolean(false)
    private var dispatcher: ActionDispatcher? = null
    private lateinit var config: ConfigManager

    // ── Service lifecycle ─────────────────────────────────────────────────────

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
                startForeground(NOTIFICATION_ID, buildNotification("Connecting…"))
                if (running.compareAndSet(false, true)) {
                    scope.launch { connectLoop() }
                }
            }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        running.set(false)
        scope.cancel()
        try { activeClient?.close() } catch (_: Exception) {}
        activeClient = null
        if (instance === this) instance = null
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    // ── WebSocket reconnect loop ──────────────────────────────────────────────

    private suspend fun connectLoop() {
        val startTime = System.currentTimeMillis()
        while (running.get()) {
            val elapsed = System.currentTimeMillis() - startTime
            if (elapsed > MAX_RECONNECT_DURATION_MS) {
                Log.w(TAG, "Giving up after 30 minutes of reconnect attempts")
                break
            }

            val url = config.serverUrl
            val token = config.token
            if (url.isBlank() || token.isBlank()) {
                Log.w(TAG, "Server URL or token not configured — waiting 10s")
                delay(10_000)
                continue
            }

            try {
                Log.i(TAG, "Connecting to $url")
                updateNotification("Connecting…")

                val client = AgentWebSocketClient(
                    serverUrl   = url,
                    token       = token,
                    config      = config,
                    onMessage   = ::handleMessage,
                    onOpened    = { updateNotification("Connected to $url") },
                    onClosed    = { code, reason ->
                        Log.i(TAG, "Connection closed: code=$code reason=$reason")
                        activeClient = null
                        if (running.get()) updateNotification("Reconnecting…")
                    }
                )

                activeClient = client
                // connect() is non-blocking; awaitClose() suspends until onClose fires
                client.connect()
                client.awaitClose()

            } catch (e: CancellationException) {
                throw e  // propagate coroutine cancellation
            } catch (e: Exception) {
                Log.e(TAG, "Connection error: ${e.message}")
                activeClient = null
            }

            if (running.get()) {
                Log.i(TAG, "Reconnecting in ${RECONNECT_DELAY_MS}ms…")
                delay(RECONNECT_DELAY_MS)
            }
        }
        stopSelf()
    }

    // ── Message handling ──────────────────────────────────────────────────────

    private fun handleMessage(raw: String, sender: AgentWebSocketClient) {
        scope.launch {
            try {
                val json = JSONObject(raw)
                // id is a UUID string from the backend — preserve as-is
                val id: Any? = json.opt("id")
                val method = json.optString("method", "")
                val params = json.optJSONObject("params") ?: JSONObject()

                Log.d(TAG, "← $method (id=$id)")

                val response = dispatcher?.dispatch(method, params)
                    ?: com.dream.smart_androidbot.api.ApiResponse.Error("Dispatcher not ready")

                val responseJson = response.toJson(id)
                val msg = responseJson.toString()
                Log.d(TAG, "→ ${msg.take(200)}")

                // Reply on the same connection that sent the request
                sender.send(msg)

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

    // ── Notification ──────────────────────────────────────────────────────────

    private fun createNotificationChannel() {
        val channel = NotificationChannel(
            CHANNEL_ID,
            "Smart Agent Connection",
            NotificationManager.IMPORTANCE_LOW
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
        getSystemService(NotificationManager::class.java)
            .notify(NOTIFICATION_ID, buildNotification(text))
    }
}

// ── WebSocketClient implementation ────────────────────────────────────────────

private class AgentWebSocketClient(
    serverUrl: String,
    token: String,
    config: ConfigManager,
    private val onMessage: (String, AgentWebSocketClient) -> Unit,
    private val onOpened: () -> Unit,
    private val onClosed: (Int, String?) -> Unit,
) : WebSocketClient(
    URI(serverUrl),
    org.java_websocket.drafts.Draft_6455(),
    mapOf(
        "Authorization" to "Bearer $token",
        "X-Device-ID"   to config.deviceId,
        "X-Device-Name" to config.deviceName,
    ),
    5_000  // connection timeout ms
) {
    // Deferred that completes when the connection closes (or fails to open)
    private val closedDeferred = CompletableDeferred<Unit>()

    /** Suspend until this connection closes. */
    suspend fun awaitClose() = closedDeferred.await()

    override fun onOpen(handshake: ServerHandshake?) {
        Log.i("AgentWS", "Connected — HTTP ${handshake?.httpStatus}")
        onOpened()
    }

    override fun onMessage(message: String?) {
        if (message != null) onMessage(message, this)
    }

    override fun onClose(code: Int, reason: String?, remote: Boolean) {
        onClosed(code, reason)
        closedDeferred.complete(Unit)
    }

    override fun onError(ex: Exception?) {
        Log.e("AgentWS", "WS error: ${ex?.message}")
        // onClose will also fire after onError, completing closedDeferred there
    }
}
