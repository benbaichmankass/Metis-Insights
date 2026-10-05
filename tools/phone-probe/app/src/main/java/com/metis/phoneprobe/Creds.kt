package com.metis.phoneprobe

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * The one Breakout login the operator types into a NATIVE field of this app (never into a web page by us).
 * Stored only in Keystore-backed EncryptedSharedPreferences (AES-256-GCM values, AES-256-SIV keys), private to the
 * app, excluded from backup. It is never logged, never put in the report, and the only place it is ever sent is the
 * login form of a breakoutprop.com page (see probe.js, __probeLogin: it refuses any other host).
 */
object Creds {
    private fun prefs(c: Context): SharedPreferences {
        val key = MasterKey.Builder(c).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build()
        return EncryptedSharedPreferences.create(
            c, "probe_login", key,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    }

    fun save(c: Context, user: String, pass: String) { prefs(c).edit().putString("u", user).putString("p", pass).apply() }
    fun clear(c: Context) { prefs(c).edit().clear().apply() }
    fun has(c: Context): Boolean = try { val p = prefs(c); !p.getString("u", null).isNullOrEmpty() && !p.getString("p", null).isNullOrEmpty() } catch (e: Exception) { false }
    fun load(c: Context): Pair<String, String>? = try {
        val p = prefs(c); val u = p.getString("u", null); val w = p.getString("p", null)
        if (u.isNullOrEmpty() || w.isNullOrEmpty()) null else Pair(u, w)
    } catch (e: Exception) { null }
}
