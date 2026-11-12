# RackTrack - the iOS and Android app

A Capacitor 6 shell, set up the same way as the team's apps (dark_mobile, RT-EOS). It opens a small start page (`www/`), finds the RackTrack server and then loads the server's app (`web/app/` in this folder). The screens live in one place, `web/app/`, so an app update needs no rebuild: restart the server and reopen the app.

- App id `com.racktrack.sweep`, name "RackTrack" (its id does not clash with the team's `com.racktrack.app`).
- The server address is remembered on the phone. Profile > Change server, or "change" under the sign-in, goes back to the start page.
- The default address comes from `www/config.js`: `npm run server -- http://<mac-ip>:8700`, then sync.

## Build

```bash
cd native
npm install
export RUBYOPT=-rlogger LANG=en_US.UTF-8        # CocoaPods on this Mac needs both

# iOS simulator
npx cap sync ios
xcodebuild -workspace ios/App/App.xcworkspace -scheme App -sdk iphonesimulator -configuration Debug -derivedDataPath ios/build build
xcrun simctl install booted ios/build/Build/Products/Debug-iphonesimulator/App.app
xcrun simctl launch booted com.racktrack.sweep

# a real iPhone: open in Xcode, pick your team under Signing, run
npx cap open ios

# Android debug APK (app/build/outputs/apk/debug/app-debug.apk)
export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
npx cap sync android && (cd android && ./gradlew assembleDebug)
```

`python3 scripts/make_icons.py` redraws the app icon and splash on both platforms.

## The camera

- **Record** and **Choose** (the phone's own camera, or a video file) work over plain http.
- **Guided** (the team's live guide) needs https, like any browser camera: start the server with `./server/run.sh --https`, connect to `https://<mac-ip>:8700`, and trust `server/cert.pem` on the phone once (iOS: install the profile, then Settings > General > About > Certificate Trust Settings).
