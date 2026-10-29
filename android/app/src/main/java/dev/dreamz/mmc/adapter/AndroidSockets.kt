package dev.dreamz.mmc.adapter

import android.Manifest
import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothSocket
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.util.Log
import java.io.IOException
import java.net.InetSocketAddress
import java.net.Socket
import java.util.UUID
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException

private const val TAG = "MMC"

/** One bonded (already paired) Classic device, for the picker. */
data class BondedBtDevice(val name: String, val mac: String)

/**
 * Thin Android glue for the on-vehicle transports. Kept deliberately free of
 * logic: attempt order, framing and error text live in [BtSetup] /
 * [StreamByteSocket] and are JVM-tested. These functions need a real radio,
 * so they are exercised on the vehicle, not in unit tests.
 *
 * Safety: only RFCOMM SPP byte exchange. No ECU writes are possible here —
 * every OBD line still passes through Elm327Driver + Allowlist.
 */
object AndroidSockets {
    private val SPP: UUID = UUID.fromString(BtSetup.SPP_UUID)

    /** Bonded Classic devices (name + MAC). Empty when Bluetooth is off. */
    @SuppressLint("MissingPermission") // Caller holds BLUETOOTH_CONNECT (API 31+) or legacy grant.
    fun bondedClassicDevices(context: Context): List<BondedBtDevice> {
        val manager = context.getSystemService(BluetoothManager::class.java) ?: return emptyList()
        val adapter: BluetoothAdapter = manager.adapter ?: return emptyList()
        if (!adapter.isEnabled) return emptyList()
        return try {
            adapter.bondedDevices
                .sortedBy { it.name ?: it.address }
                .map { BondedBtDevice(name = it.name ?: "(unnamed)", mac = it.address) }
        } catch (e: SecurityException) {
            throw e
        }
    }

    /** True when BLUETOOTH_SCAN may be used (always true on legacy APIs). */
    fun hasScanPermission(context: Context): Boolean {
        if (Build.VERSION.SDK_INT < 31) return true
        return context.checkSelfPermission(Manifest.permission.BLUETOOTH_SCAN) ==
            PackageManager.PERMISSION_GRANTED
    }

    /**
     * Connect to [mac] over SPP: best-effort cancelDiscovery first (only when
     * BLUETOOTH_SCAN permits it — a bonded connect needs only
     * BLUETOOTH_CONNECT, and discovery calls must never block it), then
     * secure → insecure → reflection channel-1. Blocks up to [timeoutMs]
     * total; throws IOException with the attempt trail on failure.
     */
    @SuppressLint("MissingPermission") // Caller holds BLUETOOTH_CONNECT (API 31+) or legacy grant.
    fun openClassic(context: Context, mac: String, timeoutMs: Int = 15_000): ByteSocket {
        val manager = context.getSystemService(BluetoothManager::class.java)
            ?: throw IOException("Bluetooth unavailable on this phone")
        val adapter: BluetoothAdapter = manager.adapter
            ?: throw IOException("Bluetooth unavailable on this phone")
        if (!adapter.isEnabled) throw IOException("Bluetooth is off — turn it on and pair the adapter first")
        if (BtSetup.mayTouchDiscovery(Build.VERSION.SDK_INT, hasScanPermission(context))) {
            try {
                if (adapter.isDiscovering) {
                    Log.i(TAG, "cancelling discovery before connect")
                    adapter.cancelDiscovery()
                }
            } catch (e: SecurityException) {
                // Best effort only: discovery must never block the connect.
                Log.w(TAG, "cancelDiscovery blocked (${e.message}); connecting anyway")
            }
        } else {
            Log.i(TAG, "skip cancelDiscovery (no BLUETOOTH_SCAN); bonded connect needs only BLUETOOTH_CONNECT")
        }
        val device: BluetoothDevice = try {
            adapter.getRemoteDevice(mac)
        } catch (e: IllegalArgumentException) {
            throw IOException("Bad adapter address: $mac — re-pick the device")
        }
        Log.i(TAG, "BT connecting to $mac (secure→insecure→ch1)")
        return BtSetup.connectFirstOf(
            BtSetup.ATTEMPT_ORDER.map { kind ->
                kind to {
                    val sock: BluetoothSocket = when (kind) {
                        BtSetup.BtAttempt.SECURE ->
                            device.createRfcommSocketToServiceRecord(SPP)
                        BtSetup.BtAttempt.INSECURE ->
                            device.createInsecureRfcommSocketToServiceRecord(SPP)
                        BtSetup.BtAttempt.REFLECT_CHANNEL_1 ->
                            device.javaClass
                                .getMethod("createRfcommSocket", Int::class.javaPrimitiveType)
                                .invoke(device, 1) as BluetoothSocket
                    }
                    try {
                        connectWithTimeout(sock, timeoutMs)
                    } catch (e: Exception) {
                        try {
                            sock.close()
                        } catch (_: Exception) {
                        }
                        throw e
                    }
                    StreamByteSocket(sock.inputStream, sock.outputStream, onClose = {
                        try {
                            sock.close()
                        } catch (_: Exception) {
                        }
                    })
                }
            },
        )
    }

    /** BluetoothSocket.connect() has no timeout; run it on a worker and cap it. */
    private fun connectWithTimeout(sock: BluetoothSocket, timeoutMs: Int) {
        val exec = java.util.concurrent.Executors.newSingleThreadExecutor()
        try {
            val future = exec.submit {
                try {
                    sock.connect()
                } catch (e: IOException) {
                    throw e
                }
            }
            try {
                future.get(timeoutMs.toLong(), TimeUnit.MILLISECONDS)
            } catch (e: TimeoutException) {
                try {
                    sock.close()
                } catch (_: Exception) {
                }
                future.cancel(true)
                throw IOException("connect timed out after ${timeoutMs}ms")
            } catch (e: java.util.concurrent.ExecutionException) {
                throw (e.cause as? IOException) ?: IOException("connect failed: ${e.cause?.message}")
            }
        } finally {
            exec.shutdownNow()
        }
    }

    /** TCP socket to a Wi-Fi ELM327 (default 192.168.0.10:35000). */
    fun openTcp(host: String, port: Int, timeoutMs: Int = 8_000): ByteSocket {
        Log.i(TAG, "TCP connecting to $host:$port")
        val sock = Socket()
        try {
            sock.tcpNoDelay = true
            sock.connect(InetSocketAddress(host, port), timeoutMs)
            sock.soTimeout = 0 // framing uses poll loop; blocking reads unbounded
        } catch (e: IOException) {
            try {
                sock.close()
            } catch (_: Exception) {
            }
            throw e
        }
        return StreamByteSocket(sock.getInputStream(), sock.getOutputStream(), onClose = {
            try {
                sock.close()
            } catch (_: Exception) {
            }
        })
    }
}
