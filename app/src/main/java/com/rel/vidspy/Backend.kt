package com.rel.vidspy

import com.chaquo.python.Python
import java.io.File

/** Thin wrapper over the Python module `vidspy_backend`. Blocking: call from a background thread. */
class Backend {
    private val module by lazy { Python.getInstance().getModule("vidspy_backend") }

    fun resolve(text: String): ResolvedPost =
        parseResolvedPost(module.callAttr("resolve", text).toString())

    fun download(item: MediaItem, destDir: File): File =
        File(module.callAttr("download", item.raw, destDir.absolutePath).toString())
}
