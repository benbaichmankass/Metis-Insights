plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// STABLE SIGNING (PHONE-EXEC-1B): the release key lives ONLY in GitHub Actions secrets
// (PHONE_EXEC_KEYSTORE_B64 / PHONE_EXEC_KEYSTORE_PASSWORD), decoded to a temp file by CI and passed here by env.
// The same key signs every build, so an update installs OVER the old app and keeps its WebView session.
// No keystore is ever committed (.gitignore: *.jks). Without the env vars only the debug build is produced.
val ksPath: String? = System.getenv("PHONE_EXEC_KEYSTORE")
val ksPass: String? = System.getenv("PHONE_EXEC_KEYSTORE_PASSWORD")

android {
    namespace = "com.metis.phoneexec"
    compileSdk = 34
    defaultConfig {
        applicationId = "com.metis.phoneexec"
        minSdk = 26
        targetSdk = 34
        versionCode = (System.getenv("GITHUB_RUN_NUMBER") ?: "1").toInt()
        versionName = "1b." + (System.getenv("GITHUB_RUN_NUMBER") ?: "dev")
    }
    signingConfigs {
        if (ksPath != null && ksPass != null) {
            create("stable") {
                storeFile = file(ksPath)
                storePassword = ksPass
                keyAlias = "phoneexec"
                keyPassword = ksPass
            }
        }
    }
    buildTypes {
        release {
            isMinifyEnabled = false
            if (ksPath != null && ksPass != null) signingConfig = signingConfigs.getByName("stable")
        }
        debug { isMinifyEnabled = false }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    packaging { resources { excludes += setOf("META-INF/LICENSE.md", "META-INF/NOTICE.md", "META-INF/*.SF", "META-INF/mailcap", "META-INF/javamail.*") } }
}

dependencies {
    implementation("androidx.webkit:webkit:1.12.1")
    implementation("androidx.security:security-crypto:1.1.0-alpha06")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
    // IMAP (read-only folder open) for the dedicated Breakout-login inbox.
    implementation("com.sun.mail:android-mail:1.6.7")
    implementation("com.sun.mail:android-activation:1.6.7")
}
