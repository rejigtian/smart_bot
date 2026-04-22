package com.dream.smart_androidbot.keepalive

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.provider.Settings
import android.util.Log
import com.dream.smart_androidbot.R
import com.dream.smart_androidbot.config.ConfigManager
import com.dream.smart_androidbot.service.AgentAccessibilityService
import com.dream.smart_androidbot.service.ReverseConnectionService

/**
 * Foreground service that keeps the agent alive during long-running tasks.
 *
 * Responsibilities:
 *  1. Hold a SCREEN_BRIGHT_WAKE_LOCK — keeps screen on so device doesn't lock during agent tasks
 *  2. Poll every 30s — detect AccessibilityService death and alert the user
 *  3. Auto-restart ReverseConnectionService if it was started but is no longer running
 */
class KeepAliveService : Service() {

    companion object {
        private const val TAG = "KeepAlive"
        private const val CHANNEL_ID = "keep_alive_channel"
        private const val NOTIFICATION_ID = 2001
        private const val ALERT_NOTIFICATION_ID = 2002
        private const val POLL_INTERVAL_MS = 30_000L

        const val ACTION_START = "com.dream.smart_androidbot.ACTION_KEEP_ALIVE_START"
        const val ACTION_STOP  = "com.dream.smart_androidbot.ACTION_KEEP_ALIVE_STOP"

        @Volatile private var instance: KeepAliveService? = null
        fun isRunning(): Boolean = instance != null
    }

    private val handler = Handler(Looper.getMainLooper())
    private var wakeLock: PowerManager.WakeLock? = null
    private var lastA11yAlive = true

    private val pollRunnable = object : Runnable {
        override fun run() {
            checkHealth()
            handler.postDelayed(this, POLL_INTERVAL_MS)
        }
    }

    // ── Lifecycle ─────────────────────────────────────────────────────────────

    override fun onCreate() {
        super.onCreate()
        instance = this
        createNotificationChannel()
        acquireWakeLock()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            ConfigManager.getInstance(this).keepAliveEnabled = false
            stopSelf()
            return START_NOT_STICKY
        }
        startForeground(NOTIFICATION_ID, buildStatusNotification())
        handler.post(pollRunnable)
        return START_STICKY
    }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        releaseWakeLock()
        instance = null
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    // ── Health check ──────────────────────────────────────────────────────────

    private fun checkHealth() {
        checkAccessibilityService()
        checkConnectionService()
    }

    private fun checkAccessibilityService() {
        val alive = AgentAccessibilityService.getInstance() != null
        when {
            !alive && lastA11yAlive -> {
                Log.w(TAG, "AccessibilityService died — alerting user")
                postA11yDeadNotification()
            }
            alive && !lastA11yAlive -> {
                Log.i(TAG, "AccessibilityService recovered")
                getSystemService(NotificationManager::class.java).cancel(ALERT_NOTIFICATION_ID)
                updateStatusNotification("Monitoring — all services active")
            }
        }
        lastA11yAlive = alive
    }

    private fun checkConnectionService() {
        val config = ConfigManager.getInstance(this)
        // If a token is configured but ReverseConnectionService is not running, restart it
        if (config.token.isNotBlank() && !ReverseConnectionService.isRunning()) {
            Log.i(TAG, "ReverseConnectionService not running — restarting")
            try {
                startForegroundService(
                    Intent(this, ReverseConnectionService::class.java).apply {
                        action = ReverseConnectionService.ACTION_START
                    }
                )
            } catch (e: Exception) {
                Log.w(TAG, "Failed to restart ReverseConnectionService: ${e.message}")
            }
        }
    }

    // ── Wake lock ─────────────────────────────────────────────────────────────

    private fun acquireWakeLock() {
        try {
            val pm = getSystemService(POWER_SERVICE) as PowerManager
            @Suppress("DEPRECATION")
            wakeLock = pm.newWakeLock(
                PowerManager.SCREEN_BRIGHT_WAKE_LOCK or PowerManager.ACQUIRE_CAUSES_WAKEUP,
                "$packageName:keep_alive"
            ).apply {
                setReferenceCounted(false)
                acquire()
            }
            Log.d(TAG, "WakeLock acquired")
        } catch (e: Exception) {
            Log.w(TAG, "Failed to acquire wake lock: ${e.message}")
        }
    }

    private fun releaseWakeLock() {
        try {
            if (wakeLock?.isHeld == true) wakeLock?.release()
        } catch (e: Exception) {
            Log.w(TAG, "Failed to release wake lock: ${e.message}")
        }
    }

    // ── Notifications ─────────────────────────────────────────────────────────

    private fun createNotificationChannel() {
        val channel = NotificationChannel(
            CHANNEL_ID,
            "Agent Keep-Alive",
            NotificationManager.IMPORTANCE_LOW
        ).apply { description = "Keeps agent services active during task execution" }
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    private fun buildStatusNotification(text: String = "Monitoring — all services active"): Notification {
        val stopIntent = Intent(this, KeepAliveService::class.java).apply {
            action = ACTION_STOP
        }
        val stopPi = PendingIntent.getService(
            this, 0, stopIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        return Notification.Builder(this, CHANNEL_ID)
            .setContentTitle("Agent Keep-Alive")
            .setContentText(text)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setOngoing(true)
            .addAction(
                Notification.Action.Builder(
                    null, "Stop",
                    stopPi
                ).build()
            )
            .build()
    }

    private fun updateStatusNotification(text: String) {
        getSystemService(NotificationManager::class.java)
            .notify(NOTIFICATION_ID, buildStatusNotification(text))
    }

    private fun postA11yDeadNotification() {
        updateStatusNotification("⚠ Accessibility Service stopped")
        val openSettings = PendingIntent.getActivity(
            this, 0,
            Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS).apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK
            },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val alert = Notification.Builder(this, CHANNEL_ID)
            .setContentTitle("Accessibility Service Stopped")
            .setContentText("Agent cannot operate. Tap to re-enable in Settings.")
            .setSmallIcon(android.R.drawable.stat_notify_error)
            .setContentIntent(openSettings)
            .setAutoCancel(false)
            .build()
        getSystemService(NotificationManager::class.java).notify(ALERT_NOTIFICATION_ID, alert)
    }
}
