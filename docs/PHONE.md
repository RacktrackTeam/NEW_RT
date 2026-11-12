# The phone app (native/) with the new sign-in

The phone app is the Capacitor shell in `native/` (app id `com.racktrack.sweep`, name RackTrack). It opens a small start page (`native/www/`), checks the engine's `/api/health`, then loads the engine's own screens at `<engine>/app/`. Those screens hold the new sign-in (the RT-EOS sign-in service, see docs/AUTH.md), so the app needs no change of its own for it.

## What changed

- The start page points at the engine: an address typed without a port gets `:8700` (was `:8600`, the old server), and the placeholder says so.
- `native/www/config.js`, the address the app tries first, is `http://10.10.1.5:8700` (this Mac's address on 2 Nov 2026, the engine's port). Change it with `npm run server -- http://<mac-ip>:8700`, then `npx cap copy`.

## What was built on 2 Nov 2026

| Build | State | Proof |
| --- | --- | --- |
| Android debug APK | built | `native/android/app/build/outputs/apk/debug/app-debug.apk`, 3.8 MB, package `com.racktrack.sweep` 1.0 (1), signed with the Android debug key (`CN=Android Debug`, apksigner verify). Built offline: `./gradlew --offline assembleDebug` with Android Studio's Java. Not uploaded anywhere. The release keystore was not used. |
| iOS simulator app | built | `xcodebuild -workspace ios/App/App.xcworkspace -scheme App -sdk iphonesimulator -configuration Debug -derivedDataPath ios/build build` (Xcode 26.6): BUILD SUCCEEDED. Installed and opened with `xcrun simctl install` and `xcrun simctl launch` on an iPhone 17 Pro simulator (iOS 26.5). No keystroke or click was sent to the Mac or the simulator. |
| iOS on a real iPhone | not built | Needs a signing team in Xcode; not asked for. |

Screens (`docs/proof/phone/`):
- `ios_simulator_start.png`: a fresh install with the default address. Nothing answered at `10.10.1.5:8700`, because the engine listens on 127.0.0.1 only (engine.sh) and the integration engine on 8700 was not running. The app shows its address form, as it should.
- `ios_simulator_signin_engine.png`: a test build pointed at the engine of this worktree (`http://127.0.0.1:8702`, which the simulator reaches on this Mac) opens the engine's new sign-in screen inside the app: email or username, password, Forgot password?, Create an organization, and the server line with change. That test build was not kept: `config.js` was put back and the app rebuilt.

## How to build again

    cd native
    npm install                                           # or copy node_modules from the main checkout
    export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home" RUBYOPT=-rlogger LANG=en_US.UTF-8
    npx cap copy
    (cd android && ./gradlew --offline assembleDebug)
    xcodebuild -workspace ios/App/App.xcworkspace -scheme App -sdk iphonesimulator -configuration Debug -derivedDataPath ios/build build
    xcrun simctl boot "iPhone 17 Pro"; xcrun simctl install booted ios/build/Build/Products/Debug-iphonesimulator/App.app; xcrun simctl launch booted com.racktrack.sweep

`ios/App/Pods` was copied from the main checkout (no pod install, no network). `npx cap sync` would run pod install; `npx cap copy` does not.

## What a real phone still needs

- The engine and the sign-in service must listen on the network, not only on 127.0.0.1: start the engine with `RACKTRACK_HOST=0.0.0.0` and give the screens the service's address with `RT_AUTH_URL=http://<mac-ip>:3302` (or 3305 in this worktree), and add the phone's origin to `CORS_ALLOWED_ORIGINS` in `data/auth/.env`.
- The camera guide needs https (README in native/).
- Signing in on the phone itself was not tried: it needs typing on the device, and no OS-level input is allowed here.
