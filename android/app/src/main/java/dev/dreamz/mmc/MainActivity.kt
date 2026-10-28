package dev.dreamz.mmc

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.material3.MaterialTheme
import dev.dreamz.mmc.ui.MmcApp
import dev.dreamz.mmc.ui.controllerFor

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val controller = controllerFor(this)
        setContent {
            MaterialTheme {
                MmcApp(controller)
            }
        }
    }
}
