package com.rel.vidspy

import com.chaquo.python.Python
import org.json.JSONObject
import java.io.File

/** Linked-account status as reported by the backend (all texts come from Python, not duplicated here). */
data class Account(
    val platform: String,
    val label: String,
    val connected: Boolean,
    val count: Int,
    val color: String,
    val loginUrl: String,
    val steps: List<String>,
    val privacy: String,
)

private fun JSONObject.toAccount() = Account(
    platform = getString("platform"),
    label = getString("label"),
    connected = getBoolean("connected"),
    count = getInt("count"),
    color = getString("color"),
    loginUrl = getString("login_url"),
    steps = getJSONArray("steps").let { a -> (0 until a.length()).map { a.getString(it) } },
    privacy = getString("privacy"),
)

/** A working example link for the paste box; [color] has no value for the pseudo platforms (hls, direct). */
data class Suggestion(
    val platform: String,
    val label: String,
    val sample: String,
    val color: String? = null,
)private fun JSONObject.toSuggestion() = Suggestion(
    platform = getString("platform"),
    label = getString("label"),
    sample = getString("sample"),
    color = if (has("color")) getString("color") else null,
)

/** Error traces stored by the backend (written only when watching or downloading fails). */
data class TraceFiles(val dir: String, val count: Int, val files: List<String>)

/** Thin wrapper over the Python package `vidspy_backend`. Blocking: call from a background thread. */
class Backend(filesDir: File) {
    private val module by lazy { Python.getInstance().getModule("vidspy_backend") }

    init {
        module.callAttr("configure", filesDir.absolutePath)
    }

    fun resolve(text: String): ResolvedPost =
        parseResolvedPost(module.callAttr("resolve", text).toString())

    fun download(item: MediaItem, destDir: File): File =
        File(module.callAttr("download", item.raw, destDir.absolutePath).toString())

    fun accounts(): List<Account> {
        val arr = org.json.JSONArray(module.callAttr("accounts_list").toString())
        return (0 until arr.length()).map { arr.getJSONObject(it).toAccount() }
    }

    fun connect(platform: String, text: String): Account =
        JSONObject(module.callAttr("account_connect", platform, text).toString()).toAccount()

    fun disconnect(platform: String): Account =
        JSONObject(module.callAttr("account_disconnect", platform).toString()).toAccount()

    /** Example links, one per platform, in the backend's order. */
    fun suggestions(): List<Suggestion> {
        val arr = org.json.JSONArray(module.callAttr("suggestions").toString())
        return (0 until arr.length()).map { arr.getJSONObject(it).toSuggestion() }
    }

    // -- error traces (written by the backend itself on resolve/download failures) --

    /** Stored traces: {dir, count, files}. */
    fun tracesInfo(): TraceFiles = JSONObject(module.callAttr("traces_info").toString()).let {
        TraceFiles(it.getString("dir"), it.getInt("count"), files = (0 until it.getJSONArray("files").length())
            .map { i -> it.getJSONArray("files").getString(i) })
    }

    /** Reports an error that happened outside Python (a player / decoder failure). */
    fun traceReport(stage: String, message: String, item: MediaItem? = null,
                    detail: String = "", text: String = "", platform: String = ""): String =
        module.callAttr("trace_report", stage, message, item?.raw, detail, text, platform).toString()

    fun lastTraceName(): String = module.callAttr("last_trace_name").toString()

    /** Zips every stored trace; None when nothing has failed yet. */
    fun tracePack(destDir: File): File? {
        val path = module.callAttr("trace_pack", destDir.absolutePath).toString()
        return path.takeUnless { it.isEmpty() || it == "None" }?.let(::File)
    }
}
