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
        versionCode = 1
        versionName = "1a.1"
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
}
