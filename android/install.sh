#!/bin/bash
# Installs the app on the TV and turns the accessibility service on. Needs adb to reach the TV once
# (run it where adb is paired, e.g. on the machine the receiver runs on):
#   ./install.sh <TV ip:port> <path to cec-over-lan-tv.apk>
set -euo pipefail
T=${1:?TV ip:port}; APK=${2:?path to the apk}
PKG=org.cecoverlan.tv; SVC=$PKG/$PKG.KeyService
# push + pm install: the streamed `adb install` dropped half way on the Xiaomi TV
adb -s "$T" push "$APK" /data/local/tmp/cec.apk >/dev/null
adb -s "$T" shell pm install -r /data/local/tmp/cec.apk
adb -s "$T" shell rm -f /data/local/tmp/cec.apk
# add the service to the ones already enabled (another app, e.g. a launcher, may rely on its own)
CUR=$(adb -s "$T" shell settings get secure enabled_accessibility_services | tr -d '\r')
case ":$CUR:" in
    *":$SVC:"*) ;;
    *) if [ -z "$CUR" ] || [ "$CUR" = null ]; then NEW=$SVC; else NEW=$CUR:$SVC; fi
       adb -s "$T" shell settings put secure enabled_accessibility_services "$NEW" ;;
esac
adb -s "$T" shell settings put secure accessibility_enabled 1
adb -s "$T" shell appops set "$PKG" ACCESS_RESTRICTED_SETTINGS allow || true
for P in DUMP QUERY_AUDIO_STATE MODIFY_AUDIO_ROUTING; do
    adb -s "$T" shell pm grant "$PKG" android.permission.$P 2>&1 | head -1 || true
done
echo "enabled: $(adb -s "$T" shell settings get secure enabled_accessibility_services)"
