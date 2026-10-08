package com.metis.phoneexec

import android.content.Context
import android.content.SharedPreferences
import android.util.Base64
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import java.security.MessageDigest
import java.security.SecureRandom

/**
 * Everything private lives here, in Keystore-backed EncryptedSharedPreferences (app-private, no backup):
 * the device token (minted on this phone, never leaves it except as the API bearer), the Breakout login
 * email, and the dedicated inbox's app password. Nothing here is ever logged, shown or put in a report.
 * Only the token's SHA-256 FINGERPRINT is shown/shared: it is not a secret (no pre-image from 256 random bits).
 */
object Store {
    private fun p(c: Context): SharedPreferences {
        val key = MasterKey.Builder(c).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build()
        return EncryptedSharedPreferences.create(
            c, "exec_store", key,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    }

    fun token(c: Context): String {
        val s = p(c)
        s.getString("token", null)?.let { return it }
        val b = ByteArray(32).also { SecureRandom().nextBytes(it) }
        val t = Base64.encodeToString(b, Base64.URL_SAFE or Base64.NO_PADDING or Base64.NO_WRAP)
        s.edit().putString("token", t).commit()
        return t
    }

    fun fingerprint(c: Context): String =
        MessageDigest.getInstance("SHA-256").digest(token(c).toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it) }

    fun get(c: Context, k: String): String? = try { p(c).getString(k, null)?.takeIf { it.isNotEmpty() } } catch (e: Exception) { null }
    fun put(c: Context, k: String, v: String?) { p(c).edit().putString(k, v ?: "").apply() }
    fun flag(c: Context, k: String): Boolean = try { p(c).getBoolean(k, false) } catch (e: Exception) { false }
    fun setFlag(c: Context, k: String, v: Boolean) { p(c).edit().putBoolean(k, v).commit() }

    const val API = "api_base"
    const val LOGIN_EMAIL = "breakout_email"
    const val INBOX_USER = "inbox_user"
    const val INBOX_PASS = "inbox_app_password"
    const val RELOGIN_LATCHED = "relogin_latched"
    const val TERMINAL_URL = "terminal_url"   // last account terminal URL seen while logged in; private, never reported
    const val DEFAULT_API = "https://ict-bot.duckdns.org"
}
