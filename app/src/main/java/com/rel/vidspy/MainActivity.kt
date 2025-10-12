package com.rel.vidspy

import android.content.Context
import android.os.Bundle
import android.view.inputmethod.InputMethodManager
import android.widget.Button
import android.widget.EditText
import android.widget.Toast
import android.widget.VideoView
import androidx.appcompat.app.AppCompatActivity
import com.chaquo.python.PyException
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import kotlinx.coroutines.*
import java.io.File

class MainActivity : AppCompatActivity() {
    private lateinit var videoView: VideoView
    private lateinit var btnPlay: Button
    private lateinit var btnPause: Button
    private lateinit var btnStop: Button
    private lateinit var btnMute: Button
    private var currentVideoFile: File? = null
    private var isMuted = false
    private var originalVolume = 1.0f

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        if (! Python.isStarted()) {
            Python.start(AndroidPlatform(this))
        }

        val py = Python.getInstance()
        val module = py.getModule("vidspylogic")

        // Initialize views
        videoView = findViewById(R.id.videoView)
        btnPlay = findViewById(R.id.btnPlay)
        btnPause = findViewById(R.id.btnPause)
        btnStop = findViewById(R.id.btnStop)
        btnMute = findViewById(R.id.btnMute)

        // Set up control button listeners
        setupControlButtons()

        findViewById<Button>(R.id.button).setOnClickListener {
            try {
                val url = findViewById<EditText>(R.id.etX).text.toString()
                if (url.isBlank()) {
                    Toast.makeText(this, "Please enter a URL", Toast.LENGTH_SHORT).show()
                    return@setOnClickListener
                }

                // Show loading message
                Toast.makeText(this, "Downloading video...", Toast.LENGTH_SHORT).show()

                // Hide keyboard
                currentFocus?.let {
                    (getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager)
                        .hideSoftInputFromWindow(it.windowToken, 0)
                }

                // Download video in background thread
                CoroutineScope(Dispatchers.IO).launch {
                    try {
                        val videoPath = module.callAttr("download_video", url).toString()

                        // Switch to main thread to update UI
                        withContext(Dispatchers.Main) {
                            displayVideo(videoPath)
                        }
                    } catch (e: PyException) {
                        withContext(Dispatchers.Main) {
                            Toast.makeText(this@MainActivity, "Error: ${e.message}", Toast.LENGTH_LONG).show()
                        }
                    }
                }

            } catch (e: Exception) {
                Toast.makeText(this, "Error: ${e.message}", Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun setupControlButtons() {
        btnPlay.setOnClickListener {
            if (videoView.isPlaying) {
                Toast.makeText(this, "Video is already playing", Toast.LENGTH_SHORT).show()
            } else {
                videoView.start()
                Toast.makeText(this, "Playing video", Toast.LENGTH_SHORT).show()
            }
        }

        btnPause.setOnClickListener {
            if (videoView.isPlaying) {
                videoView.pause()
                Toast.makeText(this, "Video paused", Toast.LENGTH_SHORT).show()
            } else {
                Toast.makeText(this, "Video is not playing", Toast.LENGTH_SHORT).show()
            }
        }

        btnStop.setOnClickListener {
            videoView.stopPlayback()
            Toast.makeText(this, "Video stopped", Toast.LENGTH_SHORT).show()
        }

        btnMute.setOnClickListener {
            if (isMuted) {
                // Unmute
                videoView.setOnPreparedListener { mediaPlayer ->
                    mediaPlayer.setVolume(originalVolume, originalVolume)
                }
                btnMute.text = "Mute"
                isMuted = false
                Toast.makeText(this, "Sound unmuted", Toast.LENGTH_SHORT).show()
            } else {
                // Mute
                videoView.setOnPreparedListener { mediaPlayer ->
                    mediaPlayer.setVolume(0.0f, 0.0f)
                }
                btnMute.text = "Unmute"
                isMuted = true
                Toast.makeText(this, "Sound muted", Toast.LENGTH_SHORT).show()
            }
        }
    }

    private fun displayVideo(videoPath: String) {
        try {
            val videoFile = File(videoPath)
            if (videoFile.exists()) {
                // Clean up previous video file
                currentVideoFile?.delete()
                currentVideoFile = videoFile

                // Set up video view
                videoView.setVideoPath(videoPath)
                videoView.setOnPreparedListener { mediaPlayer ->
                    mediaPlayer.isLooping = false
                    // Store original volume (default is 1.0f)
                    originalVolume = 1.0f
                    Toast.makeText(this, "Video ready to play", Toast.LENGTH_SHORT).show()
                }

                videoView.setOnErrorListener { _, _, _ ->
                    Toast.makeText(this, "Error playing video", Toast.LENGTH_SHORT).show()
                    true
                }

                videoView.setOnCompletionListener {
                    Toast.makeText(this, "Video playback completed", Toast.LENGTH_SHORT).show()
                }

                // Start playing automatically
                videoView.start()
            } else {
                Toast.makeText(this, "Video file not found", Toast.LENGTH_SHORT).show()
            }
        } catch (e: Exception) {
            Toast.makeText(this, "Error displaying video: ${e.message}", Toast.LENGTH_LONG).show()
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        // Clean up video file when activity is destroyed
        currentVideoFile?.delete()
    }
}

