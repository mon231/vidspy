package com.rel.vidspy

import org.json.JSONObject

enum class MediaType { VIDEO, IMAGE }

/** One video or image of a post. [raw] is the backend JSON, passed back to Python for download. */
data class MediaItem(
    val type: MediaType,
    val url: String,
    val title: String,
    val headers: Map<String, String>,
    val raw: String,
    val note: String? = null,
)

data class ResolvedPost(val platform: String, val items: List<MediaItem>)

fun parseResolvedPost(json: String): ResolvedPost {
    val root = JSONObject(json)
    val arr = root.getJSONArray("items")
    val items = (0 until arr.length()).map { i ->
        val o = arr.getJSONObject(i)
        val h = o.optJSONObject("headers")
        val headers = h?.keys()?.asSequence()?.associateWith { h.getString(it) } ?: emptyMap()
        MediaItem(
            type = if (o.getString("type") == "image") MediaType.IMAGE else MediaType.VIDEO,
            url = o.getString("url"),
            title = o.optString("title", "vidspy"),
            headers = headers,
            raw = o.toString(),
            note = o.optString("note").ifEmpty { null },
        )
    }
    return ResolvedPost(root.getString("platform"), items)
}
