package com.rel.vidspy

import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Connect / disconnect platform accounts. All logic and texts live in the Python backend. */
class SettingsActivity : AppCompatActivity() {
    private val scope = CoroutineScope(Dispatchers.Main + SupervisorJob())
    private lateinit var backend: Backend
    private lateinit var list: LinearLayout

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = "Linked accounts"
        if (!Python.isStarted()) Python.start(AndroidPlatform(this))
        backend = Backend(filesDir)

        list = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(16), dp(16), dp(16))
        }
        setContentView(ScrollView(this).apply { addView(list) })
        refresh()
    }

    private fun refresh() = scope.launch {
        val accounts = withContext(Dispatchers.IO) { backend.accounts() }
        render(accounts)
    }

    /** White platform glyph on a rounded badge in the platform's brand color. */
    private fun badge(acc: Account, size: Int): View {
        val res = resources.getIdentifier("ic_platform_${acc.platform}", "drawable", packageName)
        return ImageView(this).apply {
            setImageResource(res)
            val pad = dp(size) / 5
            setPadding(pad, pad, pad, pad)
            background = GradientDrawable().apply {
                cornerRadius = dp(size) / 4f
                setColor(Color.parseColor(acc.color))
                setStroke(dp(1), 0x33888888)
            }
            layoutParams = LinearLayout.LayoutParams(dp(size), dp(size)).apply { marginEnd = dp(12) }
            contentDescription = acc.label
        }
    }

    private fun render(accounts: List<Account>) {
        list.removeAllViews()
        list.addView(TextView(this).apply {
            text = "Optional. Connect an account to open posts that need a login. " +
                "Everything stays on this device."
        })
        accounts.forEach { acc ->
            val status = if (acc.connected) "Connected (${acc.count} cookies)" else "Not connected"
            val row = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                setPadding(0, dp(14), 0, 0)
            }
            row.addView(badge(acc, 40))
            row.addView(TextView(this).apply {
                text = "${acc.label}\n$status"
                layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
            })
            row.addView(Button(this).apply {
                text = if (acc.connected) "Replace" else "Connect"
                setOnClickListener { showConnectDialog(acc) }
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT, dp(48)).apply { marginStart = dp(12) }
            })
            if (acc.connected) row.addView(Button(this).apply {
                text = "Remove"
                setOnClickListener { disconnect(acc) }
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT, dp(48)).apply { marginStart = dp(12) }
            })
            list.addView(row)
        }
    }

    private fun openUrl(url: String) {
        try {
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
        } catch (e: Exception) {
            toast("No browser found")
        }
    }

    private fun showConnectDialog(acc: Account) {
        val input = EditText(this).apply {
            hint = "Paste the Cookie header value here"
            minLines = 5
            gravity = Gravity.TOP
            isSingleLine = false
        }
        val content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(8), dp(20), 0)
            addView(LinearLayout(context).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                addView(badge(acc, 44))
                addView(TextView(context).apply { text = acc.label; textSize = 20f })
            })
            addView(TextView(context).apply {
                text = acc.steps.mapIndexed { i, s -> "${i + 1}. $s" }.joinToString("\n\n")
                setPadding(0, dp(12), 0, dp(8))
            })
            addView(Button(context).apply {
                text = "Open ${acc.label} login"
                setOnClickListener { openUrl(acc.loginUrl) }
            })
            addView(input)
            addView(TextView(context).apply {
                text = acc.privacy
                textSize = 12f
                setPadding(0, dp(8), 0, 0)
            })
        }
        val dialog = AlertDialog.Builder(this)
            .setView(ScrollView(this).apply { addView(content) })
            .setPositiveButton("Save", null)
            .setNeutralButton("Paste clipboard", null)
            .setNegativeButton("Cancel", null)
            .show()
        // override so these buttons don't auto-dismiss on error / paste
        dialog.getButton(AlertDialog.BUTTON_NEUTRAL).setOnClickListener {
            val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            val text = cm.primaryClip?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.coerceToText(this)
            if (!text.isNullOrBlank()) input.setText(text)
        }
        dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
            scope.launch {
                try {
                    withContext(Dispatchers.IO) { backend.connect(acc.platform, input.text.toString()) }
                    dialog.dismiss()
                    toast("${acc.label} connected")
                    refresh()
                } catch (e: Exception) {
                    toast(errorText(e))
                }
            }
        }
    }

    private fun disconnect(acc: Account) = scope.launch {
        withContext(Dispatchers.IO) { backend.disconnect(acc.platform) }
        toast("${acc.label} removed")
        refresh()
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_LONG).show()

    private fun errorText(e: Exception) = (e.message ?: "Unknown error").substringAfter("VidspyError: ")

    override fun onDestroy() {
        super.onDestroy()
        scope.cancel()
    }
}
