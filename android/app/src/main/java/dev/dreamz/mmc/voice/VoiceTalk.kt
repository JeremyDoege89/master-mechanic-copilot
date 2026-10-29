package dev.dreamz.mmc.voice

import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString.Companion.toByteString
import java.util.concurrent.TimeUnit

/** Mic seam at the voice rate (mirrors the engine-sound AudioSource). */
interface VoiceMic {
    fun open(): Boolean
    fun read(buf: ShortArray, off: Int, len: Int): Int
    fun close()
}

class RealVoiceMic : VoiceMic {
    private var record: AudioRecord? = null

    override fun open(): Boolean {
        val minBuf = AudioRecord.getMinBufferSize(
            VoiceAudioSpec.SAMPLE_RATE_HZ,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        if (minBuf <= 0) return false
        return try {
            val rec = AudioRecord(
                MediaRecorder.AudioSource.MIC,
                VoiceAudioSpec.SAMPLE_RATE_HZ,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                maxOf(minBuf * 2, VoiceAudioSpec.SAMPLE_RATE_HZ * 2),
            )
            if (rec.state != AudioRecord.STATE_INITIALIZED) {
                rec.release()
                return false
            }
            rec.startRecording()
            record = rec
            true
        } catch (e: Exception) {
            false
        }
    }

    override fun read(buf: ShortArray, off: Int, len: Int): Int =
        record?.read(buf, off, len) ?: AudioRecord.ERROR

    override fun close() {
        try {
            record?.stop()
        } catch (e: Exception) {
            // Already stopped: fall through to release.
        }
        try {
            record?.release()
        } catch (e: Exception) {
            // Best effort.
        }
        record = null
    }
}

/**
 * One PUSH_TO_TALK voice turn: mic PCM streams to `/sessions/{id}/asr` and
 * upstream captions come back as live partials plus a closing final.
 *
 * Wiring: [start] opens the socket (the backend adds the Meta key
 * upstream); on open it sends `start` and streams ~100 ms PCM frames until
 * [stopTalk] sends `stop`. Inbound frames go through [RealtimeAsrClient]:
 * partials replace the caption, the final parses to the pending intent via
 * `VoiceUiState.onFinal`. The socket stays open for the final after
 * [stopTalk]; [close] ends it. Callbacks fire on OkHttp threads — the
 * controller posts them to main.
 */
class VoiceTalkSession(
    baseUrl: String,
    sessionId: String,
    private val events: Events,
    private val http: OkHttpClient = defaultHttp(),
    private val mic: VoiceMic = RealVoiceMic(),
) {
    interface Events {
        fun onPartial(caption: String)
        fun onFinal(caption: String)
        fun onError(message: String)
        fun onClosed()
    }

    private val urls = RealtimeAsrClient(baseUrl, sessionId)

    @Volatile private var socket: WebSocket? = null
    @Volatile private var capturing = false
    private var captureThread: Thread? = null
    private val closed = java.util.concurrent.atomic.AtomicBoolean(false)

    fun start() {
        if (!mic.open()) {
            events.onError("Microphone unavailable.")
            return
        }
        val req = Request.Builder().url(urls.socketUrl()).build()
        socket = http.newWebSocket(req, Listener())
    }

    /** Mic off + `stop` upstream; the socket stays open for the final. */
    fun stopTalk() {
        capturing = false
        try {
            captureThread?.join(2000)
        } catch (e: InterruptedException) {
            // Take whatever was captured; the stop frame still goes out.
        }
        captureThread = null
        try {
            mic.close()
        } catch (e: Exception) {
            // Best effort.
        }
        try {
            socket?.send(urls.controlFrame("stop"))
        } catch (e: Exception) {
            // Socket already down: onFailure/onClosed reports it.
        }
    }

    fun close() {
        try {
            stopTalk()
        } catch (e: Exception) {
            // Best effort.
        }
        try {
            socket?.close(1000, "done")
        } catch (e: Exception) {
            // Best effort.
        }
        socket = null
        fireClosed()
    }

    private fun fireClosed() {
        if (closed.compareAndSet(false, true)) events.onClosed()
    }

    private inner class Listener : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            webSocket.send(urls.controlFrame("start"))
            capturing = true
            captureThread = Thread({
                val frame = ShortArray(VoiceAudioSpec.frameSamples())
                val bytes = ByteArray(frame.size * 2)
                while (capturing) {
                    val n = try {
                        mic.read(frame, 0, frame.size)
                    } catch (e: Exception) {
                        break
                    }
                    if (n > 0) {
                        for (i in 0 until n) {
                            val v = frame[i].toInt()
                            bytes[i * 2] = (v and 0xFF).toByte()
                            bytes[i * 2 + 1] = ((v shr 8) and 0xFF).toByte()
                        }
                        try {
                            webSocket.send(bytes.copyOf(n * 2).toByteString())
                        } catch (e: Exception) {
                            break
                        }
                    } else if (n == 0) {
                        try {
                            Thread.sleep(20)
                        } catch (e: InterruptedException) {
                            break
                        }
                    } else {
                        break
                    }
                }
            }, "voice-capture").also { it.isDaemon = true; it.start() }
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            if (urls.isPartial(text)) events.onPartial(urls.captionText(text))
            else if (urls.isFinal(text)) events.onFinal(urls.captionText(text))
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            capturing = false
            events.onError(t.message ?: "voice socket failed")
            fireClosed()
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            capturing = false
            fireClosed()
        }
    }

    companion object {
        /** No read timeout (a held turn is idle between frames) + keep-alive. */
        fun defaultHttp(): OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(0, TimeUnit.MILLISECONDS)
            .pingInterval(20, TimeUnit.SECONDS)
            .build()
    }
}
