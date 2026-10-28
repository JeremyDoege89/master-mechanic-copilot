package dev.dreamz.mmc.camera

import android.content.Context
import android.util.Log
import androidx.camera.core.AspectRatio
import androidx.camera.core.Camera
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleOwner
import java.nio.ByteBuffer
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/**
 * M5 live camera: CameraX preview + ImageAnalysis at 15 fps feeding
 * [KeyframeSelector]. Sharp, changed frames (at most one per 3 s, plus
 * on-tap) are downscaled to ~1024 px and delivered via [onKeyframe] for
 * upload. Low light switches the torch on automatically.
 */
private const val TAG = "CameraScreen"
private const val ANALYSIS_FPS = 15
private const val KEYFRAME_LONG_SIDE = 1024
private const val SELECTOR_LONG_SIDE = 160

@Composable
fun CameraScreen(
    onKeyframe: (jpeg: ByteArray, width: Int, height: Int) -> Unit,
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current
    val lifecycle = LocalLifecycleOwner.current
    val selector = remember { KeyframeSelector() }
    val tapped = remember { AtomicBoolean(false) }
    var status by remember { mutableStateOf("Starting camera…") }
    val analysisExecutor = remember { Executors.newSingleThreadExecutor() }

    Box(modifier.fillMaxSize().clickable { tapped.set(true) }) {
        AndroidView(
            factory = { ctx ->
                PreviewView(ctx).also { previewView ->
                    startCamera(
                        ctx, lifecycle, previewView, selector, analysisExecutor,
                        onStatus = { status = it },
                        onKeyframe = onKeyframe,
                        consumeTap = { tapped.getAndSet(false) },
                    )
                }
            },
            modifier = Modifier.fillMaxSize(),
        )
        if (status.isNotEmpty()) {
            Text(status, modifier = Modifier.align(Alignment.TopCenter))
        }
    }

    DisposableEffect(Unit) {
        onDispose { analysisExecutor.shutdown() }
    }
}

private fun startCamera(
    context: Context,
    lifecycle: LifecycleOwner,
    previewView: PreviewView,
    selector: KeyframeSelector,
    executor: ExecutorService,
    onStatus: (String) -> Unit,
    onKeyframe: (ByteArray, Int, Int) -> Unit,
    consumeTap: () -> Boolean,
) {
    val future = ProcessCameraProvider.getInstance(context)
    future.addListener({
        try {
            val provider = future.get()
            val preview = Preview.Builder()
                .setTargetAspectRatio(AspectRatio.RATIO_4_3)
                .build()
                .also { it.setSurfaceProvider(previewView.surfaceProvider) }
            val analysis = ImageAnalysis.Builder()
                .setTargetAspectRatio(AspectRatio.RATIO_4_3)
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                .build()
            var camera: Camera? = null
            var lastNs = 0L
            val frameIntervalNs = 1_000_000_000L / ANALYSIS_FPS
            analysis.setAnalyzer(executor) { image: ImageProxy ->
                try {
                    val nowNs = System.nanoTime()
                    if (nowNs - lastNs < frameIntervalNs) return@setAnalyzer
                    lastNs = nowNs
                    handleFrame(image, selector, camera, onStatus, onKeyframe, consumeTap())
                } catch (e: Exception) {
                    Log.w(TAG, "frame handling failed", e)
                } finally {
                    image.close()
                }
            }
            provider.unbindAll()
            camera = provider.bindToLifecycle(
                lifecycle,
                CameraSelector.DEFAULT_BACK_CAMERA,
                preview,
                analysis,
            )
            onStatus("")
        } catch (e: Exception) {
            Log.e(TAG, "camera start failed", e)
            onStatus("Camera unavailable: ${e.message}")
        }
    }, ContextCompat.getMainExecutor(context))
}

private fun handleFrame(
    image: ImageProxy,
    selector: KeyframeSelector,
    camera: Camera?,
    onStatus: (String) -> Unit,
    onKeyframe: (ByteArray, Int, Int) -> Unit,
    tapped: Boolean,
) {
    val yPlane = image.planes[0].buffer.toByteArray()
    val small = downsampleGray(yPlane, image.width, image.height, SELECTOR_LONG_SIDE)
    if (camera != null) {
        try {
            camera.cameraControl.enableTorch(selector.isLowLight(small.pixels))
        } catch (e: Exception) {
            Log.w(TAG, "torch control failed", e)
        }
    }
    if (selector.offer(small.pixels, small.width, small.height, System.currentTimeMillis(), tapped)) {
        val key = downsampleGray(yPlane, image.width, image.height, KEYFRAME_LONG_SIDE)
        onKeyframe(key.pixels.toJpegBytes(key.width, key.height), key.width, key.height)
        onStatus("")
    }
}

internal data class Gray(val pixels: IntArray, val width: Int, val height: Int)

/** Nearest-neighbour downsample of the Y plane to at most [longSide] px. */
internal fun downsampleGray(y: ByteArray, width: Int, height: Int, longSide: Int): Gray {
    val scale = maxOf(width, height).toDouble() / longSide
    if (scale <= 1.0) {
        return Gray(IntArray(y.size) { y[it].toInt() and 0xFF }, width, height)
    }
    val w = (width / scale).toInt().coerceAtLeast(3)
    val h = (height / scale).toInt().coerceAtLeast(3)
    val out = IntArray(w * h)
    for (yy in 0 until h) {
        for (xx in 0 until w) {
            out[yy * w + xx] = y[(yy * scale).toInt() * width + (xx * scale).toInt()].toInt() and 0xFF
        }
    }
    return Gray(out, w, h)
}

private fun ByteBuffer.toByteArray(): ByteArray {
    val bytes = ByteArray(remaining())
    get(bytes)
    return bytes
}

/**
 * Grayscale encodings for the keyframe bytes. Production replaces this with
 * Bitmap.compress(JPEG); the PGM fallback keeps JVM tests hermetic.
 */
internal fun IntArray.toJpegBytes(width: Int, height: Int): ByteArray {
    val header = "P5\n$width $height\n255\n".toByteArray()
    val body = ByteArray(size) { this[it].coerceIn(0, 255).toByte() }
    return header + body
}
