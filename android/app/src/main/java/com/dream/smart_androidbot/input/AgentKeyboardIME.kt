package com.dream.smart_androidbot.input

import android.inputmethodservice.InputMethodService
import android.os.SystemClock
import android.view.KeyEvent
import android.view.View
import android.view.inputmethod.ExtractedTextRequest
import android.view.inputmethod.InputConnection
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel

class AgentKeyboardIME : InputMethodService() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    companion object {
        @Volatile
        private var instance: AgentKeyboardIME? = null

        fun getInstance(): AgentKeyboardIME? = instance

        fun inputText(text: String, clear: Boolean = false): Boolean {
            val ime = instance ?: return false
            val ic = ime.currentInputConnection ?: return false
            if (clear) {
                ic.performContextMenuAction(android.R.id.selectAll)
                ic.commitText("", 1)
            }
            ic.commitText(text, 1)
            return true
        }

        fun clearText(): Boolean {
            val ime = instance ?: return false
            val ic = ime.currentInputConnection ?: return false
            ic.performContextMenuAction(android.R.id.selectAll)
            ic.commitText("", 1)
            return true
        }

        fun sendKeyEventDirect(keyCode: Int): Boolean {
            val ime = instance ?: return false
            val ic = ime.currentInputConnection ?: return false
            val now = SystemClock.uptimeMillis()
            ic.sendKeyEvent(KeyEvent(now, now, KeyEvent.ACTION_DOWN, keyCode, 0))
            ic.sendKeyEvent(KeyEvent(now, now, KeyEvent.ACTION_UP, keyCode, 0))
            return true
        }

        /**
         * Returns current text content length for delete calculations.
         */
        fun getTextLength(): Int {
            val ime = instance ?: return 0
            val ic = ime.currentInputConnection ?: return 0
            val extracted = ic.getExtractedText(ExtractedTextRequest(), 0) ?: return 0
            return extracted.text?.length ?: 0
        }
    }

    override fun onCreate() {
        super.onCreate()
        instance = this
    }

    override fun onDestroy() {
        super.onDestroy()
        if (instance === this) instance = null
        scope.cancel()
    }

    // Return null view — this is a "headless" keyboard used only by automation
    override fun onCreateInputView(): View? = null
    override fun onCreateCandidatesView(): View? = null
}
