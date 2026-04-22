package com.dream.smart_androidbot.ui

import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.inputmethod.InputMethodManager
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.dream.smart_androidbot.R
import com.dream.smart_androidbot.config.ConfigManager
import com.dream.smart_androidbot.databinding.ActivityMainBinding
import com.dream.smart_androidbot.keepalive.KeepAliveService
import com.dream.smart_androidbot.service.AgentAccessibilityService
import com.dream.smart_androidbot.service.ReverseConnectionService

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var config: ConfigManager
    private val statusHandler = Handler(Looper.getMainLooper())
    private val statusRunnable = Runnable { refreshStatus() }

    // Runtime permission request for POST_NOTIFICATIONS (Android 13+)
    private val notificationPermLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { /* result doesn't block usage; foreground service still works */ }

    // ── Lifecycle ─────────────────────────────────────────────────────────────

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)
        config = ConfigManager.getInstance(this)

        // Request POST_NOTIFICATIONS on Android 13+
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED
            ) {
                notificationPermLauncher.launch(android.Manifest.permission.POST_NOTIFICATIONS)
            }
        }

        // Populate config fields
        binding.editServerUrl.setText(config.serverUrl)
        binding.editToken.setText(config.token)
        binding.editDeviceName.setText(config.deviceName)
        binding.textDeviceId.text = "ID: ${config.deviceId}"

        // Keep-alive toggle
        binding.switchKeepAlive.isChecked = config.keepAliveEnabled
        binding.switchKeepAlive.setOnCheckedChangeListener { _, checked ->
            config.keepAliveEnabled = checked
            val intent = Intent(this, KeepAliveService::class.java).apply {
                action = if (checked) KeepAliveService.ACTION_START else KeepAliveService.ACTION_STOP
            }
            if (checked) startForegroundService(intent) else startService(intent)
            Toast.makeText(
                this,
                if (checked) "Keep-Alive enabled" else "Keep-Alive disabled",
                Toast.LENGTH_SHORT
            ).show()
        }

        // Status card button actions
        binding.btnEnableAccessibility.setOnClickListener { openAccessibilitySettings() }
        binding.btnEnableIme.setOnClickListener { openImeSettings() }

        // Config buttons
        binding.btnSave.setOnClickListener {
            saveConfig()
            Toast.makeText(this, "Saved", Toast.LENGTH_SHORT).show()
        }

        binding.btnConnect.setOnClickListener {
            saveConfig()
            if (config.token.isBlank()) {
                Toast.makeText(this, "Token is required", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            if (!isAccessibilityEnabled()) {
                Toast.makeText(this, "Please enable Accessibility Service first", Toast.LENGTH_LONG).show()
                openAccessibilitySettings()
                return@setOnClickListener
            }
            startForegroundService(
                Intent(this, ReverseConnectionService::class.java).apply {
                    action = ReverseConnectionService.ACTION_START
                }
            )
            Toast.makeText(this, "Connecting…", Toast.LENGTH_SHORT).show()
        }

        binding.btnDisconnect.setOnClickListener {
            startService(
                Intent(this, ReverseConnectionService::class.java).apply {
                    action = ReverseConnectionService.ACTION_STOP
                }
            )
            Toast.makeText(this, "Disconnected", Toast.LENGTH_SHORT).show()
        }
    }

    override fun onResume() {
        super.onResume()
        refreshStatus()
        scheduleStatusRefresh()
    }

    override fun onPause() {
        super.onPause()
        statusHandler.removeCallbacks(statusRunnable)
    }

    // ── Status refresh ────────────────────────────────────────────────────────

    private fun scheduleStatusRefresh() {
        statusHandler.postDelayed(statusRunnable, 1_500)
    }

    private fun refreshStatus() {
        updateAccessibilityCard()
        updateImeCard()
        updateConnectionCard()
        // Sync switch to service runtime state (in case service stopped itself)
        binding.switchKeepAlive.setOnCheckedChangeListener(null)
        binding.switchKeepAlive.isChecked = KeepAliveService.isRunning()
        binding.switchKeepAlive.setOnCheckedChangeListener { _, checked ->
            config.keepAliveEnabled = checked
            val intent = Intent(this, KeepAliveService::class.java).apply {
                action = if (checked) KeepAliveService.ACTION_START else KeepAliveService.ACTION_STOP
            }
            if (checked) startForegroundService(intent) else startService(intent)
        }
        scheduleStatusRefresh()
    }

    private fun updateAccessibilityCard() {
        val enabled = isAccessibilityEnabled()
        setDotColor(binding.dotAccessibility, enabled)
        binding.textAccessibilityStatus.text =
            if (enabled) "Active — gestures and screen reading ready"
            else "Not enabled — tap Enable to open settings"
        binding.btnEnableAccessibility.text = if (enabled) "Enabled ✓" else "Enable"
        binding.btnEnableAccessibility.isEnabled = !enabled
    }

    private fun updateImeCard() {
        val enabled = isImeEnabled()
        val selected = isImeSelected()
        val ok = enabled && selected
        setDotColor(binding.dotIme, ok, warn = enabled && !selected)
        binding.textImeStatus.text = when {
            ok          -> "Active — reliable text input ready"
            enabled     -> "Enabled but not selected — tap Switch to activate"
            else        -> "Not enabled — tap Setup to open Input Method settings"
        }
        binding.btnEnableIme.text = when {
            ok      -> "Selected ✓"
            enabled -> "Switch"
            else    -> "Setup"
        }
        binding.btnEnableIme.isEnabled = !ok
    }

    private fun updateConnectionCard() {
        val running = ReverseConnectionService.isRunning()
        setDotColor(binding.dotConnection, running)
        binding.textConnectionStatus.text =
            if (running) "Service running — connecting to ${config.serverUrl}"
            else "Not connected"
    }

    // ── Dot color helper ──────────────────────────────────────────────────────

    private fun setDotColor(dot: android.view.View, ok: Boolean, warn: Boolean = false) {
        val color = when {
            ok   -> ContextCompat.getColor(this, R.color.dot_green)
            warn -> ContextCompat.getColor(this, R.color.dot_orange)
            else -> ContextCompat.getColor(this, R.color.dot_red)
        }
        (dot.background as? android.graphics.drawable.GradientDrawable)?.setColor(color)
    }

    // ── Permission checks ─────────────────────────────────────────────────────

    private fun isAccessibilityEnabled(): Boolean =
        AgentAccessibilityService.getInstance() != null

    private fun isImeEnabled(): Boolean = try {
        val imm = getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager
        imm.enabledInputMethodList.any {
            it.packageName == packageName && it.serviceName.contains("AgentKeyboardIME")
        }
    } catch (_: Exception) { false }

    private fun isImeSelected(): Boolean {
        val selected = Settings.Secure.getString(
            contentResolver, Settings.Secure.DEFAULT_INPUT_METHOD
        ) ?: return false
        return selected.contains(packageName)
    }

    // ── Navigation to system settings ─────────────────────────────────────────

    private fun openAccessibilitySettings() {
        try {
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
        } catch (_: Exception) {
            Toast.makeText(this, "Cannot open Accessibility Settings", Toast.LENGTH_SHORT).show()
        }
    }

    private fun openImeSettings() {
        try {
            if (isImeEnabled()) {
                // Already enabled — show input method picker to let user switch
                val imm = getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager
                @Suppress("DEPRECATION")
                imm.showInputMethodPicker()
            } else {
                startActivity(Intent(Settings.ACTION_INPUT_METHOD_SETTINGS))
            }
        } catch (_: Exception) {
            Toast.makeText(this, "Cannot open Input Method Settings", Toast.LENGTH_SHORT).show()
        }
    }

    // ── Config helpers ────────────────────────────────────────────────────────

    private fun saveConfig() {
        config.serverUrl = binding.editServerUrl.text.toString().trim()
        config.token = binding.editToken.text.toString().trim()
        config.deviceName = binding.editDeviceName.text.toString().trim()
    }
}
