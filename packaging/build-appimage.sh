#!/usr/bin/env bash
# Builds WayClick-<version>-x86_64.AppImage into dist/.
#
# Bundles a portable CPython plus PySide6-Essentials, so it runs on any distro
# without installing anything. Qt modules WayClick does not use are stripped;
# that is what takes the AppDir from ~336 MB down to ~169 MB (53 MB compressed).
#
# QtMultimedia is not part of PySide6-Essentials, so inside the AppImage the
# hotkey beeps fall back to paplay/pw-play from the host.
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION=$(grep -oP '^VERSION = "\K[^"]+' wayclick.py)
PYVER=3.12
PY_RELEASE=python3.12.14-cp312-cp312-manylinux2014_x86_64.AppImage
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

echo "== fetching tools"
curl -fsSL -o "$WORK/appimagetool" \
  https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
curl -fsSL -o "$WORK/python.AppImage" \
  "https://github.com/niess/python-appimage/releases/download/python$PYVER/$PY_RELEASE"
chmod +x "$WORK/appimagetool" "$WORK/python.AppImage"

echo "== building AppDir"
( cd "$WORK" && ./python.AppImage --appimage-extract >/dev/null && mv squashfs-root AppDir )
APPDIR="$WORK/AppDir"
"$APPDIR/AppRun" -m pip install --no-cache-dir "PySide6-Essentials==6.9.*" >/dev/null

PS="$APPDIR/opt/python$PYVER/lib/python$PYVER/site-packages/PySide6"
rm -rf "$PS"/{lupdate,qmlls,qmlformat,qmllint,assistant,designer,linguist,qmlscene} \
       "$PS"/Qt/{qml,translations} "$PS"/{glue,include,typesystems,scripts,examples}
for m in OpenGL OpenGLWidgets Qml Quick QuickControls2 QuickWidgets QuickTest \
         Designer Help Concurrent PrintSupport Sql Test Svg SvgWidgets Network \
         Xml UiTools ExampleIcons; do
    rm -f "$PS/Qt$m.abi3.so" "$PS/Qt/lib/libQt6$m.so.6"*
done
rm -f "$PS"/Qt/lib/libQt6{Quick,Qml,Designer,Labs}*.so.6*

rm -f "$APPDIR"/AppRun "$APPDIR"/*.desktop "$APPDIR"/python.png "$APPDIR"/.DirIcon
install -Dm755 wayclick.py "$APPDIR/usr/bin/wayclick.py"
install -Dm644 packaging/wayclick.svg "$APPDIR/wayclick.svg"
sed 's|^Exec=.*|Exec=wayclick|; s|^Icon=.*|Icon=wayclick|' packaging/wayclick.desktop \
    > "$APPDIR/wayclick.desktop"
ln -sf wayclick.svg "$APPDIR/.DirIcon"
cat > "$APPDIR/AppRun" <<APPRUN
#!/bin/bash
HERE="\$(dirname "\$(readlink -f "\$0")")"
export PYTHONHOME="\$HERE/opt/python$PYVER"
export PYTHONPATH="\$HERE/opt/python$PYVER/lib/python$PYVER/site-packages"
export LD_LIBRARY_PATH="\$HERE/opt/python$PYVER/lib:\${LD_LIBRARY_PATH}"
exec "\$HERE/opt/python$PYVER/bin/python$PYVER" "\$HERE/usr/bin/wayclick.py" "\$@"
APPRUN
chmod +x "$APPDIR/AppRun"

echo "== packing"
mkdir -p dist
ARCH=x86_64 "$WORK/appimagetool" --no-appstream "$APPDIR" \
    "dist/WayClick-$VERSION-x86_64.AppImage" >/dev/null
chmod +x "dist/WayClick-$VERSION-x86_64.AppImage"
echo "built: dist/WayClick-$VERSION-x86_64.AppImage"
