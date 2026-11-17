#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

APP="dist/Крышка.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

swiftc -O -o "$APP/Contents/MacOS/lid-awake" mac/main.swift -framework Cocoa -framework WebKit
swiftc -O -o /tmp/lid-awake-make-icon mac/make-icon.swift -framework AppKit
/tmp/lid-awake-make-icon /tmp/lid-awake-icon-1024.png

ICONSET="/tmp/lid-awake.iconset"
rm -rf "$ICONSET"
mkdir -p "$ICONSET"
sips -z 16 16 /tmp/lid-awake-icon-1024.png --out "$ICONSET/icon_16x16.png" >/dev/null
sips -z 32 32 /tmp/lid-awake-icon-1024.png --out "$ICONSET/icon_16x16@2x.png" >/dev/null
sips -z 32 32 /tmp/lid-awake-icon-1024.png --out "$ICONSET/icon_32x32.png" >/dev/null
sips -z 64 64 /tmp/lid-awake-icon-1024.png --out "$ICONSET/icon_32x32@2x.png" >/dev/null
sips -z 128 128 /tmp/lid-awake-icon-1024.png --out "$ICONSET/icon_128x128.png" >/dev/null
sips -z 256 256 /tmp/lid-awake-icon-1024.png --out "$ICONSET/icon_128x128@2x.png" >/dev/null
sips -z 256 256 /tmp/lid-awake-icon-1024.png --out "$ICONSET/icon_256x256.png" >/dev/null
sips -z 512 512 /tmp/lid-awake-icon-1024.png --out "$ICONSET/icon_256x256@2x.png" >/dev/null
sips -z 512 512 /tmp/lid-awake-icon-1024.png --out "$ICONSET/icon_512x512.png" >/dev/null
cp /tmp/lid-awake-icon-1024.png "$ICONSET/icon_512x512@2x.png"
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"

cp mac/Info.plist "$APP/Contents/Info.plist"
cp server.py index.html "$APP/Contents/Resources/"
chmod +x "$APP/Contents/MacOS/lid-awake"
codesign --force --sign - "$APP"
echo "собрано: $APP"
