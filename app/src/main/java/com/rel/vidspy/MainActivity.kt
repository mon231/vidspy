package com.rel.vidspy

import android.Manifest
import android.app.Dialog
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.BitmapFactory
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.Editable
import android.text.TextWatcher
import android.util.Log
import android.util.TypedValue
import android.view.Gravity
import android.view.Menu
import android.view.MenuItem
import android.view.View
import android.view.ViewGroup
import android.view.Window
import android.view.WindowManager
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.HorizontalScrollView
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.PopupMenu
import android.widget.ProgressBar
import android.widget.SeekBar
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.media3.ui.PlayerView
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

private const val PAUSE_ICON = "||"
private const val PLAY_ICON = ">"

class MainActivity : AppCompatActivity() {
    private val job = SupervisorJob()
    private val scope = CoroutineScope(Dispatchers.Main + job)
    private lateinit var backend: Backend
    private lateinit var cacheRoot: File

    private lateinit var etUrl: EditText
    private var fullscreenDialog: Dialog? = null
    private var screenRoot: FrameLayout? = null
    private lateinit var tvInfo: TextView
    private lateinit var tvCounter: TextView
    private lateinit var progress: ProgressBar
    private lateinit var playerView: PlayerView
    private lateinit var player: VideoPlayer
    private lateinit var imageView: ImageView
    private lateinit var btnFullscreen: ImageView
    private lateinit var navLayout: View
    private lateinit var controlLayout: View
    private lateinit var btnDownload: View
    private lateinit var tvEmpty: View
    private lateinit var btnPlay: Button
    private lateinit var btnSpeed: Button
    private lateinit var seekBar: SeekBar
    private lateinit var tvTime: TextView
    private lateinit var suggestionsScroll: HorizontalScrollView
    private lateinit var suggestionsRow: LinearLayout

    private var post: ResolvedPost? = null
    private var index = 0
    private var showJob: Job? = null
    private var dragging = false
    private var fullscreen = false

    private data class SavedSlot(
        val parent: ViewGroup,
        val index: Int,
        val params: ViewGroup.LayoutParams
    )

    private val fullscreenSaved = mutableMapOf<View, SavedSlot>()
    private val handler = Handler(Looper.getMainLooper())
    private val ticker = object : Runnable {
        override fun run() {
            updateProgress()
            handler.postDelayed(this, 250)
        }
    }
    private var pendingSave: (() -> Unit)? = null
    private var lastTraceToast: String? = null  // the "trace stored" hint fires once per trace

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        if (!Python.isStarted()) Python.start(AndroidPlatform(this))
        backend = Backend(filesDir)
        cacheRoot = File(cacheDir, "vidspy").apply { deleteRecursively(); mkdirs() }

        etUrl = findViewById(R.id.etX)
        tvInfo = findViewById(R.id.tvInfo)
        tvCounter = findViewById(R.id.tvCounter)
        progress = findViewById(R.id.progress)
        playerView = findViewById(R.id.playerView)
        player = VideoPlayer(this, playerView, object : VideoPlayer.Events {
            override fun onBuffering(buffering: Boolean) = setBusy(buffering)

            override fun onEnded() {
                btnPlay.text = PLAY_ICON
            }

            override fun onError(message: String, detail: String) {
                setBusy(false)
                appTrace("playback", message, detail, post?.items?.getOrNull(index))
                toast(message, long = true)
            }
        })
        imageView = findViewById(R.id.imageView)
        navLayout = findViewById(R.id.navLayout)
        controlLayout = findViewById(R.id.controlLayout)
        btnDownload = findViewById(R.id.btnDownload)
        btnFullscreen = findViewById(R.id.btnFullscreen)
        btnFullscreen.setOnClickListener { toggleFullscreen() }
        tvEmpty = findViewById(R.id.tvEmpty)
        btnPlay = findViewById(R.id.btnPlay)
        btnSpeed = findViewById(R.id.btnSpeed)
        seekBar = findViewById(R.id.seekBar)
        tvTime = findViewById(R.id.tvTime)
        suggestionsScroll = findViewById(R.id.suggestionsScroll)
        suggestionsRow = findViewById(R.id.suggestionsRow)

        findViewById<Button>(R.id.button).setOnClickListener { fetch() }
        findViewById<Button>(R.id.btnPaste).setOnClickListener { paste() }
        findViewById<Button>(R.id.btnPrev).setOnClickListener { step(-1) }
        findViewById<Button>(R.id.btnNext).setOnClickListener { step(1) }
        btnDownload.setOnClickListener { downloadCurrent() }
        // Suggestions row follows the box: shown only while it is blank. Registered here, before the
        // chips load, so the toggle works even when the backend list fails to arrive.
        etUrl.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) = Unit
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) = Unit
            override fun afterTextChanged(s: Editable?) = updateSuggestionsVisibility()
        })
        updateSuggestionsVisibility()
        // Cosmetic, so failures stay silent and the row simply stays empty.
        scope.launch {
            try {
                val samples = withContext(Dispatchers.IO) { backend.suggestions() }
                buildSuggestions(samples)
            } catch (_: Exception) {
            }
        }
        etUrl.setOnEditorActionListener { _, actionId, _ ->
            if (actionId == EditorInfo.IME_ACTION_GO) { fetch(); true } else false
        }
        setupControls()
        handleShareIntent(intent)
    }

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menuInflater.inflate(R.menu.main, menu)
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        if (item.itemId == R.id.action_accounts) {
            startActivity(Intent(this, SettingsActivity::class.java))
            return true
        }
        return super.onOptionsItemSelected(item)
    }

    override fun onNewIntent(intent: Intent?) {
        super.onNewIntent(intent)
        handleShareIntent(intent)
    }

    private fun handleShareIntent(intent: Intent?) {
        when (intent?.action) {
            Intent.ACTION_SEND -> intent.getStringExtra(Intent.EXTRA_TEXT)?.let {
                etUrl.setText(it)
                fetch()
            }
            Intent.ACTION_VIEW -> intent.data?.toString()?.let {   // platform links registered in the manifest
                etUrl.setText(it)
                fetch()
            }
        }
    }

    private fun paste() {
        val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val text = cm.primaryClip?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.coerceToText(this)
        if (!text.isNullOrBlank()) etUrl.setText(text)
    }

    private fun fetch() {
        val text = etUrl.text.toString()
        if (text.isBlank()) return toast("Please enter a link")
        (getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager)
            .hideSoftInputFromWindow(etUrl.windowToken, 0)

        setBusy(true)
        scope.launch {
            try {
                val resolved = withContext(Dispatchers.IO) { backend.resolve(text) }
                post = resolved
                index = 0
                tvEmpty.visibility = View.GONE
                btnDownload.visibility = View.VISIBLE
                show()
            } catch (e: Exception) {
                Log.e("vidspy", "operation failed", e)
                toastError(e)
                setBusy(false)
            }
        }
    }

    private fun show() {
        val p = post ?: return
        val item = p.items[index]
        showJob?.cancel()
        stopVideo()
        imageView.visibility = View.GONE
        playerView.visibility = View.GONE
        controlLayout.visibility = View.GONE
        navLayout.visibility = if (p.items.size > 1) View.VISIBLE else View.GONE
        tvCounter.text = "${index + 1} / ${p.items.size}"
        tvInfo.text = "${p.platform} · ${item.title}" + (item.note?.let { "\n$it" } ?: "")
        setBusy(true)

        if (item.type == MediaType.VIDEO) {
            playerView.visibility = View.VISIBLE
            controlLayout.visibility = View.VISIBLE
            btnFullscreen.visibility = View.VISIBLE
            btnPlay.text = PAUSE_ICON
            player.play(item)
            return
        }
        showJob = scope.launch {
            try {
                showImage(item)
            } catch (e: Exception) {
                Log.e("vidspy", "operation failed", e)
                if (e is IllegalStateException) {  // the image download (traced in Python) worked; decoding failed here
                    appTrace("playback", errorText(e), item = item)
                    toast(errorText(e), long = true)
                } else {
                    toastError(e)
                }
                setBusy(false)
            }
        }
    }

    private suspend fun showImage(item: MediaItem) {
        val bitmap = withContext(Dispatchers.IO) {
            val file = backend.download(item, cacheRoot)
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeFile(file.path, bounds)
            var sample = 1
            while (bounds.outWidth / sample > 2048 || bounds.outHeight / sample > 2048) sample *= 2
            BitmapFactory.decodeFile(file.path, BitmapFactory.Options().apply { inSampleSize = sample })
        } ?: throw IllegalStateException("Cannot decode image")
        imageView.setImageBitmap(bitmap)
        imageView.visibility = View.VISIBLE
        setBusy(false)
    }

    private fun step(delta: Int) {
        val p = post ?: return
        index = (index + delta + p.items.size) % p.items.size
        show()
    }

    private fun stopVideo() {
        player.release()
        seekBar.progress = 0
        tvTime.text = "0:00 / 0:00"
    }

    /** Fullscreen: move the video and its controls into a full-screen dialog. */
    private fun toggleFullscreen() {
        if (fullscreen) exitFullscreen() else enterFullscreen()
    }

    private fun enterFullscreen() {
        if (fullscreen) return
        fullscreen = true
        val root = FrameLayout(this).apply { setBackgroundColor(Color.BLACK) }
        screenRoot = root
        val dialog = Dialog(this).apply {
            requestWindowFeature(Window.FEATURE_NO_TITLE)
            setContentView(root)
            window?.apply {
                setLayout(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
                addFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN)
                decorView.systemUiVisibility = View.SYSTEM_UI_FLAG_FULLSCREEN or
                    View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or
                    View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
            }
            setOnDismissListener { _ -> if (fullscreen) restoreViews() }
        }
        fullscreenDialog = dialog
        moveView(playerView, root, FrameLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        moveView(controlLayout, root, FrameLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.BOTTOM))
        moveView(btnFullscreen, root, FrameLayout.LayoutParams(
            dp(44), dp(44), Gravity.TOP or Gravity.START).apply { setMargins(dp(10), dp(10), dp(10), dp(10)) })
        btnFullscreen.setImageResource(R.drawable.ic_fullscreen_exit)
        dialog.show()
    }

    private fun exitFullscreen() {
        if (!fullscreen) return
        fullscreen = false
        restoreViews()
        fullscreenDialog?.dismiss()
        btnFullscreen.setImageResource(R.drawable.ic_fullscreen)
    }

    private fun moveView(view: View, target: ViewGroup, params: ViewGroup.LayoutParams) {
        val parent = view.parent as? ViewGroup ?: throw IllegalStateException("View not attached")
        val index = parent.indexOfChild(view)
        fullscreenSaved[view] = SavedSlot(parent, index, view.layoutParams)
        parent.removeView(view)
        target.addView(view, params)
    }

    private fun restoreViews() {
        fullscreenSaved.forEach { (view, slot) ->
            val host = view.parent as? ViewGroup
            host?.removeView(view)
            slot.parent.addView(view, slot.index, slot.params)
        }
        fullscreenSaved.clear()
    }

    override fun onBackPressed() {
        if (fullscreen) {
            exitFullscreen()
        } else {
            super.onBackPressed()
        }
    }

    private fun setupControls() {
        btnPlay.setOnClickListener {
            player.toggle()
            btnPlay.text = if (player.isPlaying) PAUSE_ICON else PLAY_ICON
        }
        btnSpeed.setOnClickListener { showSpeedMenu() }
        seekBar.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(bar: SeekBar, progress: Int, fromUser: Boolean) {
                if (fromUser) tvTime.text = "${clock(progress)} / ${clock(player.duration)}"
            }

            override fun onStartTrackingTouch(bar: SeekBar) {
                dragging = true
            }

            override fun onStopTrackingTouch(bar: SeekBar) {
                dragging = false
                player.seekTo(bar.progress)
            }
        })
        handler.post(ticker)
    }

    private fun updateProgress() {
        if (dragging || !player.active) return
        btnPlay.text = if (player.isPlaying) PAUSE_ICON else PLAY_ICON
        if (player.isLive) {
            seekBar.isEnabled = false
            tvTime.text = "LIVE"
            return
        }
        val duration = player.duration
        if (duration <= 0) return
        seekBar.isEnabled = true
        seekBar.max = duration
        seekBar.progress = player.position
        tvTime.text = "${clock(player.position)} / ${clock(duration)}"
    }

    private fun clock(ms: Int): String {
        val total = (ms / 1000).coerceAtLeast(0)
        return "%d:%02d".format(total / 60, total % 60)
    }

    private fun showSpeedMenu() {
        val menu = PopupMenu(this, btnSpeed)
        val speeds = floatArrayOf(0.5f, 0.75f, 1f, 1.25f, 1.5f, 2f)
        speeds.forEachIndexed { i, s -> menu.menu.add(0, i, i, speedLabel(s)) }
        menu.setOnMenuItemClickListener {
            player.speed = speeds[it.itemId]
            btnSpeed.text = speedLabel(player.speed)
            true
        }
        menu.show()
    }

    private fun speedLabel(s: Float) = "${if (s == s.toInt().toFloat()) s.toInt().toString() else s.toString()}x"

    private fun downloadCurrent() {
        val p = post ?: return
        saveItems(listOf(p.items[index]))
    }

    private fun saveItems(items: List<MediaItem>) {
        if (!hasStoragePermission()) {
            pendingSave = { saveItems(items) }
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.WRITE_EXTERNAL_STORAGE), 1)
            return
        }
        setBusy(true)
        scope.launch {
            try {
                withContext(Dispatchers.IO) {
                    items.forEach { item ->
                        val file = backend.download(item, cacheRoot)
                        MediaSaver.save(this@MainActivity, file, item.type)
                        file.delete()
                    }
                }
                toast("Saved ${items.size} file(s) to Movies/Pictures › vidspy")
            } catch (e: Exception) {
                Log.e("vidspy", "operation failed", e)
                toastError(e)
            }
            setBusy(false)
        }
    }

    private fun hasStoragePermission() =
        Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q ||
            ContextCompat.checkSelfPermission(this, Manifest.permission.WRITE_EXTERNAL_STORAGE) ==
            PackageManager.PERMISSION_GRANTED

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        val action = pendingSave
        pendingSave = null
        if (grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) action?.invoke()
        else toast("Storage permission is needed to save files")
    }

    private fun setBusy(busy: Boolean) {
        progress.visibility = if (busy) View.VISIBLE else View.INVISIBLE
    }

    private fun toast(msg: String, long: Boolean = false) {
        Log.w("vidspy", "toast: $msg")
        Toast.makeText(this, msg, if (long) Toast.LENGTH_LONG else Toast.LENGTH_SHORT).show()
    }

    /** Errors that surface here (player, image decoding) are recorded by the backend's trace system. */
    private fun appTrace(stage: String, message: String, detail: String = "", item: MediaItem? = null) {
        val text = etUrl.text.toString()
        val platform = post?.platform ?: ""
        scope.launch {
            try {
                withContext(Dispatchers.IO) { backend.traceReport(stage, message, item, detail, text, platform) }
            } catch (_: Exception) {
            }
        }
    }

    /** Python already wrote a trace for this failure; hint once per trace where the button lives. */
    private fun toastError(e: Exception) {
        toast(errorText(e), long = true)
        scope.launch {
            val name = try {
                withContext(Dispatchers.IO) { backend.lastTraceName() }
            } catch (_: Exception) {
                return@launch
            }
            if (name.isNotBlank() && name != lastTraceToast) {
                lastTraceToast = name
                toast("Diagnostic trace stored · Accounts → Error traces to download")
            }
        }
    }

    /** Python errors look like "vidspy_backend.api.VidspyError: message"; show just the message. */
    private fun errorText(e: Exception) = (e.message ?: "Unknown error").substringAfter("VidspyError: ")

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    /** Rounded example-link chips; tapping one fills the box but the user still presses Watch. */
    private fun buildSuggestions(suggestions: List<Suggestion>) {
        val ripple = TypedValue().apply {
            theme.resolveAttribute(android.R.attr.selectableItemBackground, this, true)
        }.resourceId
        suggestionsRow.removeAllViews()
        suggestions.forEach { s ->
            val color = s.color?.let { Color.parseColor(it) }
                ?: ContextCompat.getColor(this@MainActivity, R.color.ink)
            val chip = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                background = ContextCompat.getDrawable(this@MainActivity, R.drawable.bg_input)
                if (ripple != 0) foreground = ContextCompat.getDrawable(this@MainActivity, ripple)
                setPadding(dp(10), dp(8), dp(10), dp(8))
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT,
                ).apply { marginEnd = dp(6) }
            }
            // Icon only when the platform has a drawable; hls / direct have none.
            val iconRes = resources.getIdentifier("ic_platform_${s.platform}", "drawable", packageName)
            if (iconRes != 0) chip.addView(ImageView(this@MainActivity).apply {
                setImageResource(iconRes)
                setColorFilter(color)
                layoutParams = LinearLayout.LayoutParams(dp(18), dp(18)).apply { marginEnd = dp(6) }
            })
            chip.addView(TextView(this@MainActivity).apply {
                text = s.label
                textSize = 12f
                isSingleLine = true
                setTextColor(ContextCompat.getColor(this@MainActivity, R.color.ink))
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT,
                ).apply { marginEnd = dp(6) }
            })
            chip.setOnClickListener {
                etUrl.setText(s.sample)
                etUrl.setSelection(etUrl.text.length)
            }
            suggestionsRow.addView(chip)
        }
    }

    /** The row is visible exactly while the box is blank, whether chips arrived or not. */
    private fun updateSuggestionsVisibility() {
        suggestionsScroll.visibility = if (etUrl.text.isNullOrBlank()) View.VISIBLE else View.GONE
    }

    override fun onStop() {
        super.onStop()
        player.pause()
        btnPlay.text = PLAY_ICON
    }

    override fun onDestroy() {
        super.onDestroy()
        handler.removeCallbacks(ticker)
        scope.cancel()
        player.release()
        cacheRoot.deleteRecursively()
    }
}
