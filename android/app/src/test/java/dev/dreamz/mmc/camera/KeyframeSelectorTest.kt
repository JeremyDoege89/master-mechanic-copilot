package dev.dreamz.mmc.camera

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * KeyframeSelectorTest (M5): sharp/change/tap/torch-gate logic, no network.
 * Mirrors backend/tests/test_vision_parse.py keyframe cases.
 */
class KeyframeSelectorTest {

    private fun flat(value: Int, w: Int = 8, h: Int = 8): IntArray =
        IntArray(w * h) { value }

    /** Checkerboard-ish frame: high Laplacian variance (sharp). */
    private fun sharp(w: Int = 8, h: Int = 8): IntArray =
        IntArray(w * h) { i -> if ((i + i / w) % 2 == 0) 30 else 220 }

    @Test
    fun flatFrameIsNotSharp() {
        val s = KeyframeSelector()
        assertFalse(s.isSharp(flat(120), 8, 8))
    }

    @Test
    fun texturedFrameIsSharp() {
        val s = KeyframeSelector()
        assertTrue(s.isSharp(sharp(), 8, 8))
    }

    @Test
    fun firstSharpFrameIsKept() {
        val s = KeyframeSelector()
        assertTrue(s.offer(sharp(), 8, 8, nowMs = 0L))
    }

    @Test
    fun blurryFrameIsDropped() {
        val s = KeyframeSelector()
        assertFalse(s.offer(flat(120), 8, 8, nowMs = 0L))
    }

    @Test
    fun unchangedSceneIsDropped() {
        val s = KeyframeSelector()
        assertTrue(s.offer(sharp(), 8, 8, nowMs = 0L))
        assertFalse(s.offer(sharp(), 8, 8, nowMs = 4000L))
    }

    @Test
    fun changedSceneAfterWindowIsKept() {
        val s = KeyframeSelector()
        assertTrue(s.offer(sharp(), 8, 8, nowMs = 0L))
        val other = IntArray(64) { i -> if ((i / 8) % 2 == 0) 30 else 220 }
        assertTrue(s.isSharp(other, 8, 8))
        assertTrue(s.offer(other, 8, 8, nowMs = 4000L))
    }

    @Test
    fun rateLimitHoldsWithinWindow() {
        val s = KeyframeSelector()
        assertTrue(s.offer(sharp(), 8, 8, nowMs = 0L))
        val other = IntArray(64) { i -> if ((i / 8) % 2 == 0) 30 else 220 }
        assertFalse(s.offer(other, 8, 8, nowMs = 1000L))
    }

    @Test
    fun tapKeepsSharpFrameImmediately() {
        val s = KeyframeSelector()
        assertTrue(s.offer(sharp(), 8, 8, nowMs = 0L))
        val other = IntArray(64) { i -> if ((i / 8) % 2 == 0) 30 else 220 }
        assertTrue(s.offer(other, 8, 8, nowMs = 500L, tapped = true))
    }

    @Test
    fun tapDoesNotKeepBlurryFrame() {
        val s = KeyframeSelector()
        assertFalse(s.offer(flat(120), 8, 8, nowMs = 0L, tapped = true))
    }

    @Test
    fun lowLightGate() {
        val s = KeyframeSelector()
        assertTrue(s.isLowLight(flat(20)))
        assertFalse(s.isLowLight(flat(120)))
    }
}
