package com.rel.vidspy

import android.content.Context
import androidx.annotation.OptIn
import androidx.media3.common.C
import androidx.media3.common.MediaItem as ExoItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.DefaultHttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.hls.HlsMediaSource
import androidx.media3.exoplayer.source.MediaSource
import androidx.media3.exoplayer.source.MergingMediaSource
import androidx.media3.exoplayer.source.ProgressiveMediaSource
import androidx.media3.ui.PlayerView

/**
 * Streams a video straight from its CDN: only a sliding window around the playback position is
 * buffered, so huge files and live HLS streams work. A separate audio stream is merged on the fly.
 */
@OptIn(UnstableApi::class)
class VideoPlayer(
    private val context: Context,
    private val view: PlayerView,
    private val events: Events,
) {
    interface Events {
        fun onBuffering(buffering: Boolean)
        fun onEnded()
        fun onError(message: String, detail: String)
    }

    private var player: ExoPlayer? = null
    var speed = 1f
        set(value) {
            field = value
            player?.setPlaybackSpeed(value)
        }

    val isPlaying get() = player?.let { it.playWhenReady && it.playbackState != Player.STATE_ENDED } == true
    val isLive get() = player?.isCurrentMediaItemLive == true
    val duration get() = player?.duration?.takeIf { it != C.TIME_UNSET }?.toInt() ?: 0
    val position get() = (player?.currentPosition?.toInt() ?: 0).let { if (duration > 0) minOf(it, duration) else it }
    val active get() = player != null

    fun play(item: MediaItem) {
        release()
        val exo = ExoPlayer.Builder(context).build()
        exo.addListener(object : Player.Listener {
            override fun onPlaybackStateChanged(state: Int) {
                events.onBuffering(state == Player.STATE_BUFFERING || state == Player.STATE_IDLE)
                if (state == Player.STATE_ENDED) events.onEnded()
            }

            override fun onPlayerError(error: PlaybackException) {
                // the code name plus the cause chain is what the trace needs to name the transport problem
                val chain = buildString {
                    var cause: Throwable? = error.cause
                    var first = true
                    while (cause != null) {
                        if (!first) append(" <- ")
                        append(cause.javaClass.simpleName)
                        cause.message?.let { append(": ").append(it) }
                        cause = cause.cause
                        first = false
                    }
                }
                events.onError(error.message ?: "Cannot play this video", "${error.errorCodeName}: $chain")
            }
        })
        val video = source(item.url, item.headers, item.ext == "m3u8")
        val audio = item.audio
        exo.setMediaSource(if (audio == null) video else
            MergingMediaSource(video, source(audio.url, audio.headers, audio.ext == "m3u8")))
        exo.setPlaybackSpeed(speed)
        exo.playWhenReady = true
        exo.prepare()
        view.player = exo
        player = exo
    }

    fun toggle() {
        val exo = player ?: return
        if (exo.playbackState == Player.STATE_ENDED) {
            exo.seekTo(0)
            exo.playWhenReady = true
        } else {
            exo.playWhenReady = !exo.playWhenReady
        }
    }

    fun seekTo(ms: Int) = player?.seekTo(ms.toLong())

    fun pause() = player?.pause()

    fun release() {
        view.player = null
        player?.release()
        player = null
    }

    private fun source(url: String, headers: Map<String, String>, hls: Boolean): MediaSource {
        val http = DefaultHttpDataSource.Factory()
            .setAllowCrossProtocolRedirects(true)
            .setDefaultRequestProperties(headers)
        headers["User-Agent"]?.let { http.setUserAgent(it) }
        val item = ExoItem.fromUri(url)
        return if (hls) HlsMediaSource.Factory(http).createMediaSource(item)
        else ProgressiveMediaSource.Factory(http).createMediaSource(item)
    }
}
