package com.rel.vidspy

import org.json.JSONObject

enum class MediaType { VIDEO, IMAGE }

/** A media stream (url + the headers its CDN needs). */
data class Stream(val url: String, val ext: String, val headers: Map<String, String>)

/**
 * One video or image of a post. [raw] is the backend JSON, passed back to Python for download.
 * [audio] is set when the sound is a separate stream that the player merges while streaming.
 */
data class MediaItem(
    val type: MediaType,
    val url: String,
    val ext: String,
    val title: String,
    val headers: Map<String, String>,
    val raw: String,
    val note: String? = null,
    val audio: Stream? = null,
)

data class ResolvedPost(val platform: String, val items: List<MediaItem>)

private fun JSONObject.headerMap(): Map<String, String> {
    val h = optJSONObject("headers") ?: return emptyMap()
    return h.keys().asSequence().associateWith { h.getString(it) }
}

fun parseResolvedPost(json: String): ResolvedPost {
    val root = JSONObject(json)
    val arr = root.getJSONArray("items")
    val items = (0 until arr.length()).map { i ->
        val o = arr.getJSONObject(i)
        MediaItem(
            type = if (o.getString("type") == "image") MediaType.IMAGE else MediaType.VIDEO,
            url = o.getString("url"),
            ext = o.optString("ext"),
            title = o.optString("title", "vidspy"),
            headers = o.headerMap(),
            raw = o.toString(),
            note = if (o.isNull("note")) null else o.optString("note").ifEmpty { null },
            audio = o.optJSONObject("audio")?.let { Stream(it.getString("url"), it.optString("ext"), it.headerMap()) },
        )
    }
    return ResolvedPost(root.getString("platform"), items)
}
