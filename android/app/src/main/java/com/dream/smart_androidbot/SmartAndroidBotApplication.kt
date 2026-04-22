package com.dream.smart_androidbot

import android.app.Application
import com.dream.smart_androidbot.config.ConfigManager

class SmartAndroidBotApplication : Application() {

    override fun onCreate() {
        super.onCreate()
        // Ensure ConfigManager is initialized with application context
        ConfigManager.getInstance(this)
    }
}
