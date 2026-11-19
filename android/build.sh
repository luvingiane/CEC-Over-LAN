#!/bin/bash
# Builds the TV app without Gradle: aapt2, javac, d8, zipalign, apksigner (all from ./sdk).
#   CEC_HOST=<the computer's address> CEC_TOKEN=<token> ./build.sh      -> build/cec-over-lan-tv.apk
# The address and token are written into the APK, so it is yours: do not share it.
set -euo pipefail
cd "$(dirname "$0")"
: "${CEC_HOST:?set CEC_HOST to the address of the computer the TV shows}"
: "${CEC_TOKEN:?set CEC_TOKEN to the contents of ~/cec-over-lan/token}"
PORT=${CEC_PORT:-8766}; ACTIVE=${CEC_ACTIVE_PATH:-0x1000}; CONSUME=${CEC_CONSUME:-false}
SDK=$PWD/sdk; BT=$SDK/build-tools/34.0.0; JAR=$SDK/platforms/android-34/android.jar
KS=${CEC_KEYSTORE:-$HOME/.config/cec-over-lan/tv-app.keystore}
rm -rf build && mkdir -p build/gen/org/cecoverlan/tv build/classes
python3 gen_keynames.py /usr/include/linux/input-event-codes.h build/gen/org/cecoverlan/tv/KeyNames.java
cat > build/gen/org/cecoverlan/tv/Config.java <<EOF
package org.cecoverlan.tv;
final class Config {
    static final String HOST = "$CEC_HOST", TOKEN = "$CEC_TOKEN", ACTIVE_PATH = "$ACTIVE";
    static final int PORT = $PORT;
    static final boolean CONSUME = $CONSUME;
}
EOF
"$BT/aapt2" compile --dir res -o build/res.zip
"$BT/aapt2" link -o build/base.apk -I "$JAR" --manifest AndroidManifest.xml \
    --min-sdk-version 26 --target-sdk-version 34 build/res.zip
javac --release 11 -cp "$JAR" -d build/classes $(find src build/gen -name '*.java')
"$BT/d8" --lib "$JAR" --min-api 26 --output build $(find build/classes -name '*.class')
(cd build && zip -q base.apk classes.dex)
"$BT/zipalign" -f 4 build/base.apk build/aligned.apk
if [ ! -f "$KS" ]; then
    mkdir -p "$(dirname "$KS")"
    keytool -genkeypair -keystore "$KS" -storepass cec-over-lan -keypass cec-over-lan -alias tv \
        -keyalg RSA -keysize 2048 -validity 36500 -dname "CN=CEC-Over-LAN" >/dev/null 2>&1
fi
"$BT/apksigner" sign --ks "$KS" --ks-pass pass:cec-over-lan --key-pass pass:cec-over-lan \
    --out build/cec-over-lan-tv.apk build/aligned.apk
"$BT/apksigner" verify build/cec-over-lan-tv.apk && echo "built: $PWD/build/cec-over-lan-tv.apk"
