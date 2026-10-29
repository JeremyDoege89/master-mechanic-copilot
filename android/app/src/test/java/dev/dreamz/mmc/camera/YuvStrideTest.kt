package dev.dreamz.mmc.camera

import org.junit.Assert.assertArrayEquals
import org.junit.Test
import java.nio.ByteBuffer

/**
 * YuvStrideTest (finding #10): the Y-plane copy must respect row/pixel
 * strides. CameraX YUV_420_888 planes commonly pad rows (rowStride >
 * width); ignoring the stride shears every row below the first.
 */
class YuvStrideTest {

    @Test
    fun packedPlaneCopiesVerbatim() {
        val w = 4
        val h = 3
        val bytes = ByteArray(w * h) { it.toByte() }
        assertArrayEquals(bytes, copyYPlane(ByteBuffer.wrap(bytes), w, h, w, 1))
    }

    @Test
    fun rowStridePaddingIsSkipped() {
        val w = 4
        val h = 3
        val stride = 6
        val buf = ByteBuffer.allocate(stride * h)
        for (r in 0 until h) {
            for (c in 0 until stride) {
                buf.put(r * stride + c, (r * 10 + c).toByte())
            }
        }
        val got = copyYPlane(buf, w, h, stride, 1)
        val want = ByteArray(w * h) { i -> ((i / w) * 10 + (i % w)).toByte() }
        assertArrayEquals(want, got)
    }

    @Test
    fun pixelStrideIsRespected() {
        val w = 3
        val h = 2
        val stride = 6
        val buf = ByteBuffer.allocate(stride * h)
        for (r in 0 until h) {
            for (c in 0 until stride) {
                buf.put(r * stride + c, (100 + r * 10 + c).toByte())
            }
        }
        val got = copyYPlane(buf, w, h, stride, 2)
        val want = ByteArray(w * h) { i -> (100 + (i / w) * 10 + (i % w) * 2).toByte() }
        assertArrayEquals(want, got)
    }

    @Test
    fun consumedBufferPositionDoesNotMatter() {
        val w = 4
        val h = 2
        val bytes = ByteArray(w * h) { it.toByte() }
        val buf = ByteBuffer.wrap(bytes)
        buf.position(buf.limit()) // analyzer already drained it
        assertArrayEquals(bytes, copyYPlane(buf, w, h, w, 1))
    }
}
