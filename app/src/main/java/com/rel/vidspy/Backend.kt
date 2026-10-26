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

/** Thin wrapper over the Python package `vidspy_backend`. Blocking: call from a background thread. */
class Backend(filesDir: File) {
    private val module by lazy { Python.getInstance().getModule("vidspy_backend") }

    init {
        // app-private storage; excluded from backups (allowBackup=false)
        module.callAttr("configure", File(filesDir, "accounts").absolutePath)
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
}
