package com.rel.vidspy

import android.Manifest
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.BitmapFactory
import android.media.MediaPlayer
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.view.Menu
import android.view.MenuItem
import android.view.View
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.widget.Button
import android.widget.EditText
import android.widget.ImageView
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import android.widget.VideoView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
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

class MainActivity : AppCompatActivity() {
    private val job = SupervisorJob()
    private val scope = CoroutineScope(Dispatchers.Main + job)
    private lateinit var backend: Backend
    private lateinit var cacheRoot: File

    private lateinit var etUrl: EditText
    private lateinit var tvInfo: TextView
    private lateinit var tvCounter: TextView
    private lateinit var progress: ProgressBar
    private lateinit var videoView: VideoView
    private lateinit var imageView: ImageView
    private lateinit var navLayout: View
    private lateinit var controlLayout: View
    private lateinit var downloadLayout: View
    private lateinit var btnMute: Button

    private var post: ResolvedPost? = null
    private var index = 0
    private var mediaPlayer: MediaPlayer? = null
    private var isMuted = false
    private var showJob: Job? = null
    private var pendingSave: (() -> Unit)? = null

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
        videoView = findViewById(R.id.videoView)
        imageView = findViewById(R.id.imageView)
        navLayout = findViewById(R.id.navLayout)
        controlLayout = findViewById(R.id.controlLayout)
        downloadLayout = findViewById(R.id.downloadLayout)
        btnMute = findViewById(R.id.btnMute)

        findViewById<Button>(R.id.button).setOnClickListener { fetch() }
        findViewById<Button>(R.id.btnPaste).setOnClickListener { paste() }
        findViewById<Button>(R.id.btnPrev).setOnClickListener { step(-1) }
        findViewById<Button>(R.id.btnNext).setOnClickListener { step(1) }
        findViewById<Button>(R.id.btnDownload).setOnClickListener { downloadCurrent() }
        findViewById<Button>(R.id.btnDownloadAll).setOnClickListener { downloadAll() }
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
        if (intent?.action == Intent.ACTION_SEND) {
            intent.getStringExtra(Intent.EXTRA_TEXT)?.let {
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
                downloadLayout.visibility = View.VISIBLE
                show()
            } catch (e: Exception) {
                toast(errorText(e), long = true)
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
        videoView.visibility = View.GONE
        controlLayout.visibility = View.GONE
        navLayout.visibility = if (p.items.size > 1) View.VISIBLE else View.GONE
        tvCounter.text = "${index + 1} / ${p.items.size}"
        tvInfo.text = "${p.platform} · ${item.title}" + (item.note?.let { "\n$it" } ?: "")
        setBusy(true)

        showJob = scope.launch {
            try {
                if (item.type == MediaType.VIDEO) playVideo(item) else showImage(item)
            } catch (e: Exception) {
                toast(errorText(e), long = true)
                setBusy(false)
            }
        }
    }

    /** Direct watch: stream the CDN url. If the player rejects it, fall back to a cached download. */
    private fun playVideo(item: MediaItem, fromCache: File? = null) {
        videoView.visibility = View.VISIBLE
        controlLayout.visibility = View.VISIBLE
        videoView.setOnPreparedListener { mp ->
            mediaPlayer = mp
            applyMute()
            setBusy(false)
            videoView.start()
        }
        videoView.setOnErrorListener { _, _, _ ->
            if (fromCache == null) {
                showJob = scope.launch {
                    try {
                        val file = withContext(Dispatchers.IO) { backend.download(item, cacheRoot) }
                        playVideo(item, file)
                    } catch (e: Exception) {
                        toast(errorText(e), long = true)
                        setBusy(false)
                    }
                }
            } else {
                toast("Cannot play this video")
                setBusy(false)
            }
            true
        }
        if (fromCache != null) videoView.setVideoPath(fromCache.path)
        else videoView.setVideoURI(Uri.parse(item.url), item.headers)
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
        videoView.stopPlayback()
        mediaPlayer = null
    }

    private fun setupControls() {
        findViewById<Button>(R.id.btnPlay).setOnClickListener { if (!videoView.isPlaying) videoView.start() }
        findViewById<Button>(R.id.btnPause).setOnClickListener { if (videoView.isPlaying) videoView.pause() }
        findViewById<Button>(R.id.btnStop).setOnClickListener { videoView.pause(); videoView.seekTo(0) }
        btnMute.setOnClickListener {
            isMuted = !isMuted
            applyMute()
        }
    }

    private fun applyMute() {
        val v = if (isMuted) 0f else 1f
        mediaPlayer?.setVolume(v, v)
        btnMute.text = if (isMuted) "Unmute" else "Mute"
    }

    private fun downloadCurrent() {
        val p = post ?: return
        saveItems(listOf(p.items[index]))
    }

    private fun downloadAll() {
        val p = post ?: return
        saveItems(p.items)
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
                toast(errorText(e), long = true)
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

    private fun toast(msg: String, long: Boolean = false) =
        Toast.makeText(this, msg, if (long) Toast.LENGTH_LONG else Toast.LENGTH_SHORT).show()

    /** Python errors look like "vidspy_backend.api.VidspyError: message"; show just the message. */
    private fun errorText(e: Exception) = (e.message ?: "Unknown error").substringAfter("VidspyError: ")

    override fun onDestroy() {
        super.onDestroy()
        scope.cancel()
        stopVideo()
        cacheRoot.deleteRecursively()
    }
}
