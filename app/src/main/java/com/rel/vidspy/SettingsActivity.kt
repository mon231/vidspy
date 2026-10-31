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
import androidx.core.content.ContextCompat
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

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
        val traces = try {
            withContext(Dispatchers.IO) { backend.tracesInfo() }
        } catch (e: Exception) {  // cosmetic: the list itself is what matters here
            null
        }
        render(accounts, traces)
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

    private fun render(accounts: List<Account>, traces: TraceFiles? = null) {
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
        tracesTray(traces)
    }

    /** Internal diagnostics: every error while watching/downloading wrote a report here; download them all at once. */
    private fun tracesTray(traces: TraceFiles? = null) {
        list.addView(View(this).apply {
            setBackgroundColor(getColor(R.color.line))
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(1)).apply {
                topMargin = dp(20)
            }
        })
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(0, dp(14), 0, 0)
        }
        row.addView(TextView(this).apply {
            text = "Error traces\n${traces?.count ?: "?"} stored on this device · saves a zip with what went wrong"
            textSize = 13f
            setTextColor(ContextCompat.getColor(this@SettingsActivity, R.color.muted))
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        })
        row.addView(Button(this).apply {
            text = "Download"
            setOnClickListener { exportTraces() }
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, dp(44)).apply { marginStart = dp(12) }
        })
        list.addView(row)
    }

    private fun exportTraces() = scope.launch {
        val traces = try {
            withContext(Dispatchers.IO) { backend.tracesInfo() }
        } catch (e: Exception) {
            toast(errorText(e)); return@launch
        }
        if (traces.count == 0) {
            toast("No traces yet: they are written only when watching or downloading fails")
            return@launch
        }
        try {
            val zip = withContext(Dispatchers.IO) {
                val packDir = File(cacheDir, "tracepack").apply { deleteRecursively(); mkdirs() }
                backend.tracePack(packDir)
            } ?: return@launch toast("No traces stored yet")
            val name = zip.name
            val uri = withContext(Dispatchers.IO) { MediaSaver.saveDocument(this@SettingsActivity, zip) }
            toast(if (uri != null) "Saved $name (${traces.count} trace(s), Downloads › vidspy)"
            else "Could not write to Downloads")
        } catch (e: Exception) {
            toast(errorText(e))
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
