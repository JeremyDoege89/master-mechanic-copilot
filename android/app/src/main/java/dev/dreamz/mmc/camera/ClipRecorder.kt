package dev.dreamz.mmc.camera

import android.content.Context
import android.util.Log
import androidx.camera.core.CameraSelector
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.video.FileOutputOptions
import androidx.camera.video.Quality
import androidx.camera.video.QualitySelector
import androidx.camera.video.Recorder
import androidx.camera.video.Recording
import androidx.camera.video.VideoCapture
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleOwner
import java.io.File
import java.util.concurrent.Executor

/**
 * M5 clip recorder: 5–10 s H.264 clips for SAM video segmentation.
 *
 * SAM accepts MP4/MOV (H.264/H.265/MPEG-4) only — no VP9/WebM — and bills
 * at least 50 frames per video request. [quality] defaults to HD, which
 * the backend downsamples server-side for tracking.
 */
class ClipRecorder(
    private val context: Context,
    private val lifecycle: LifecycleOwner,
    private val minSeconds: Int = 5,
    private val maxSeconds: Int = 10,
) {
    private var recording: Recording? = null

    private fun videoCapture(): VideoCapture<Recorder> {
        val recorder = Recorder.Builder()
            .setQualitySelector(QualitySelector.from(Quality.HD))
            .build()
        return VideoCapture.withOutput(recorder)
    }

    /**
     * Starts recording to [outputFile] (must end .mp4). Calls [onDone] with
     * the finished file, or [onError] on failure. Recording auto-stops at
     * [maxSeconds]; call [stop] earlier, never before [minSeconds].
     */
    fun start(
        outputFile: File,
        executor: Executor = ContextCompat.getMainExecutor(context),
        onDone: (File) -> Unit,
        onError: (String) -> Unit,
    ) {
        require(outputFile.extension.lowercase() == "mp4") { "SAM video must be MP4 H.264" }
        val future = ProcessCameraProvider.getInstance(context)
        future.addListener({
            try {
                val provider = future.get()
                val capture = videoCapture()
                provider.unbindAll()
                provider.bindToLifecycle(
                    lifecycle,
                    CameraSelector.DEFAULT_BACK_CAMERA,
                    capture,
                )
                val output = FileOutputOptions.Builder(outputFile).build()
                recording = capture.output
                    .prepareRecording(context, output)
                    .withAudioEnabled()
                    .start(executor) { event ->
                        if (event is androidx.camera.video.VideoRecordEvent.Finalize) {
                            if (event.hasError()) {
                                onError("recording failed: ${event.error}")
                            } else {
                                onDone(outputFile)
                            }
                            recording = null
                        }
                    }
            } catch (e: Exception) {
                Log.e("ClipRecorder", "clip start failed", e)
                onError("Camera unavailable: ${e.message}")
            }
        }, executor)
    }

    fun stop() {
        recording?.stop()
        recording = null
    }
}
