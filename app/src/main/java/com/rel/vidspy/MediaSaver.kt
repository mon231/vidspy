package com.rel.vidspy

import android.content.ContentValues
import android.content.Context
import android.media.MediaScannerConnection
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import java.io.File

/** Copies a downloaded file into the public gallery (Movies/vidspy or Pictures/vidspy). */
object MediaSaver {
    private const val FOLDER = "vidspy"

    private fun mimeFor(file: File, type: MediaType): String = when (file.extension.lowercase()) {
        "mp4" -> "video/mp4"
        "ts" -> "video/mp2t"
        "webm" -> "video/webm"
        "mov" -> "video/quicktime"
        "jpg", "jpeg" -> "image/jpeg"
        "png" -> "image/png"
        "webp" -> "image/webp"
        "gif" -> "image/gif"
        else -> if (type == MediaType.VIDEO) "video/mp4" else "image/jpeg"
    }

    fun save(context: Context, src: File, type: MediaType): Uri? {
        val mime = mimeFor(src, type)
        val isVideo = type == MediaType.VIDEO
        val publicDir = if (isVideo) Environment.DIRECTORY_MOVIES else Environment.DIRECTORY_PICTURES

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val resolver = context.contentResolver
            val values = ContentValues().apply {
                put(MediaStore.MediaColumns.DISPLAY_NAME, src.name)
                put(MediaStore.MediaColumns.MIME_TYPE, mime)
                put(MediaStore.MediaColumns.RELATIVE_PATH, "$publicDir/$FOLDER")
                put(MediaStore.MediaColumns.IS_PENDING, 1)
            }
            val collection = if (isVideo) MediaStore.Video.Media.EXTERNAL_CONTENT_URI
            else MediaStore.Images.Media.EXTERNAL_CONTENT_URI
            val uri = resolver.insert(collection, values) ?: return null
            resolver.openOutputStream(uri)?.use { out -> src.inputStream().use { it.copyTo(out) } }
            values.clear()
            values.put(MediaStore.MediaColumns.IS_PENDING, 0)
            resolver.update(uri, values, null, null)
            return uri
        }

        // API 24-28: needs WRITE_EXTERNAL_STORAGE (requested by the activity).
        val dir = File(Environment.getExternalStoragePublicDirectory(publicDir), FOLDER).apply { mkdirs() }
        val dest = File(dir, src.name)
        src.copyTo(dest, overwrite = true)
        MediaScannerConnection.scanFile(context, arrayOf(dest.path), arrayOf(mime), null)
        return Uri.fromFile(dest)
    }

    /** Copies a document (the error-traces zip) into Downloads/vidspy. */
    fun saveDocument(context: Context, src: File): Uri? {
        val mime = "application/zip"
        val publicDir = Environment.DIRECTORY_DOWNLOADS

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val resolver = context.contentResolver
            val values = ContentValues().apply {
                put(MediaStore.MediaColumns.DISPLAY_NAME, src.name)
                put(MediaStore.MediaColumns.MIME_TYPE, mime)
                put(MediaStore.MediaColumns.RELATIVE_PATH, "$publicDir/$FOLDER")
                put(MediaStore.MediaColumns.IS_PENDING, 1)
            }
            val uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values) ?: return null
            resolver.openOutputStream(uri)?.use { out -> src.inputStream().use { it.copyTo(out) } }
            values.clear()
            values.put(MediaStore.MediaColumns.IS_PENDING, 0)
            resolver.update(uri, values, null, null)
            return uri
        }

        val dir = File(Environment.getExternalStoragePublicDirectory(publicDir), FOLDER).apply { mkdirs() }
        val dest = File(dir, src.name)
        src.copyTo(dest, overwrite = true)
        MediaScannerConnection.scanFile(context, arrayOf(dest.path), arrayOf(mime), null)
        return Uri.fromFile(dest)
    }
}
