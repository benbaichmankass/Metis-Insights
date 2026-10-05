plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.metis.phoneprobe"
    compileSdk = 34
    defaultConfig {
        applicationId = "com.metis.phoneprobe"
        minSdk = 26
        targetSdk = 34
        versionCode = 3
        versionName = "1a.3"
    }
    buildTypes {
        // Debug build only: sideloaded probe, signed with the runner's throwaway debug key.
        debug { isMinifyEnabled = false }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    implementation("androidx.webkit:webkit:1.12.1")
    // Keystore-backed encrypted preferences for the one login the operator types into a NATIVE field.
    implementation("androidx.security:security-crypto:1.1.0-alpha06")
}
