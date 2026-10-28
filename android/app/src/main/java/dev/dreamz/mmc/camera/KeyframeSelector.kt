package dev.dreamz.mmc.camera

/**
 * Pure keyframe-selection policy mirroring backend/mmc/vision/keyframes.py.
 *
 * CameraX delivers YUV frames at ~15 fps; callers pass the Y plane
 * downsampled to at most 160 px on the long side. No Android imports here
 * so the JVM unit test covers the exact shipping logic.
 */
class KeyframeSelector(
    val minIntervalMs: Long = 3000L,
    val sharpnessThreshold: Double = 100.0,
    val changeThreshold: Double = 8.0,
    val lowLightMean: Double = 40.0,
) {
    private var lastKeyframeMs: Long? = null
    private var prevFrame: IntArray? = null

    /** Mean luminance 0-255; true means the torch should be on. */
    fun isLowLight(gray: IntArray): Boolean {
        require(gray.isNotEmpty())
        return gray.average() < lowLightMean
    }

    /** Sharpness: variance of the 4-neighbour Laplacian, borders skipped. */
    fun laplacianVariance(gray: IntArray, width: Int, height: Int): Double {
        require(width >= 3 && height >= 3) { "frame too small for Laplacian" }
        require(gray.size == width * height) {
            "expected ${width * height} pixels, got ${gray.size}"
        }
        val laps = DoubleArray((width - 2) * (height - 2))
        var k = 0
        for (y in 1 until height - 1) {
            val row = y * width
            for (x in 1 until width - 1) {
                val i = row + x
                laps[k++] = (4 * gray[i] - gray[i - 1] - gray[i + 1] -
                    gray[i - width] - gray[i + width]).toDouble()
            }
        }
        val mean = laps.average()
        return laps.sumOf { (it - mean) * (it - mean) } / laps.size
    }

    fun isSharp(gray: IntArray, width: Int, height: Int): Boolean =
        laplacianVariance(gray, width, height) >= sharpnessThreshold

    /** Scene-change score: mean absolute pixel difference. */
    fun meanAbsDiff(a: IntArray, b: IntArray): Double {
        require(a.isNotEmpty() && a.size == b.size)
        var sum = 0.0
        for (i in a.indices) sum += kotlin.math.abs(a[i] - b[i])
        return sum / a.size
    }

    fun hasChanged(prev: IntArray, curr: IntArray): Boolean =
        meanAbsDiff(prev, curr) >= changeThreshold

    /**
     * True when this frame should be kept as a keyframe.
     * A tap keeps any sharp frame immediately; otherwise the frame must be
     * sharp, changed since the reference, and outside the 3 s window.
     */
    fun offer(
        gray: IntArray,
        width: Int,
        height: Int,
        nowMs: Long,
        tapped: Boolean = false,
    ): Boolean {
        if (tapped && isSharp(gray, width, height)) {
            accept(gray, nowMs)
            return true
        }
        if (!isSharp(gray, width, height)) return false
        val prev = prevFrame
        if (prev != null && prev.size == gray.size && !hasChanged(prev, gray)) return false
        val last = lastKeyframeMs
        if (last != null && nowMs - last < minIntervalMs) return false
        accept(gray, nowMs)
        return true
    }

    private fun accept(frame: IntArray, nowMs: Long) {
        prevFrame = frame.copyOf()
        lastKeyframeMs = nowMs
    }
}
