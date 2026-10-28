package dev.dreamz.mmc

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import androidx.compose.material3.MaterialTheme
import dev.dreamz.mmc.ui.MmcApp
import dev.dreamz.mmc.ui.MmcViewModel
import dev.dreamz.mmc.ui.controllerFor

class MainActivity : ComponentActivity() {
    private val vm: MmcViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // The ViewModel retains the controller across recreation; a fresh
        // process restores the snapshot and resumes the backend session.
        val controller = vm.controller ?: controllerFor(this).also { c ->
            vm.controller = c
            vm.restoreInto(c)
        }
        setContent {
            MaterialTheme {
                MmcApp(controller)
            }
        }
    }

    override fun onPause() {
        vm.controller?.let { vm.persist(it) }
        super.onPause()
    }
}
