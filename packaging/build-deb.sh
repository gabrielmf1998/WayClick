#!/usr/bin/env bash
# Builds wayclick_<version>_all.deb into dist/.
# Uses ar and tar directly, so it also builds on a distro without dpkg-deb.
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION=$(grep -oP '^VERSION = "\K[^"]+' wayclick.py)
ROOT=$(mktemp -d)
trap 'rm -rf "$ROOT"' EXIT

install -Dpm 0755 wayclick.py "$ROOT/usr/share/wayclick/wayclick.py"
install -d "$ROOT/usr/bin"
cat > "$ROOT/usr/bin/wayclick" <<'LAUNCH'
#!/usr/bin/env bash
exec python3 /usr/share/wayclick/wayclick.py "$@"
LAUNCH
chmod 0755 "$ROOT/usr/bin/wayclick"
install -Dpm 0644 packaging/wayclick.desktop "$ROOT/usr/share/applications/wayclick.desktop"
install -Dpm 0644 packaging/wayclick.svg "$ROOT/usr/share/icons/hicolor/scalable/apps/wayclick.svg"
install -Dpm 0644 packaging/99-wayclick-uinput.rules "$ROOT/lib/udev/rules.d/99-wayclick-uinput.rules"
install -Dpm 0644 packaging/uinput.conf "$ROOT/usr/lib/modules-load.d/wayclick-uinput.conf"
install -Dpm 0644 LICENSE "$ROOT/usr/share/doc/wayclick/copyright"
install -Dpm 0644 README.md "$ROOT/usr/share/doc/wayclick/README.md"
install -Dpm 0644 README.pt-BR.md "$ROOT/usr/share/doc/wayclick/README.pt-BR.md"

SIZE=$(du -ks "$ROOT" | cut -f1)
mkdir -p "$ROOT/DEBIAN"
cat > "$ROOT/DEBIAN/control" <<CONTROL
Package: wayclick
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.9), python3-pyside6.qtwidgets
Recommends: python3-pyside6.qtmultimedia
Maintainer: gabrielmf1998 <noreply@github.com>
Installed-Size: $SIZE
Homepage: https://github.com/gabrielmf1998/WayClick
Description: Autoclicker for Wayland, using /dev/uinput
 An autoclicker that works on Wayland. It creates a virtual mouse in the
 kernel through /dev/uinput, so its clicks arrive as real input with no X11,
 no xdotool and nothing running as root. Goes from 1 click/s down to a 0.1 ms
 interval, with a global hotkey, a hold mode, a keyboard macro, per-window key
 injection and an anti-AFK nudge.
 .
 Members of the 'input' group can use it right after install; the package
 ships the udev rule that grants that group access to /dev/uinput.
CONTROL
cat > "$ROOT/DEBIAN/postinst" <<'POST'
#!/bin/sh
set -e
udevadm control --reload-rules >/dev/null 2>&1 || true
modprobe uinput >/dev/null 2>&1 || true
if [ -x /usr/bin/update-desktop-database ]; then
    update-desktop-database -q /usr/share/applications || true
fi
if [ -x /usr/bin/gtk-update-icon-cache ]; then
    gtk-update-icon-cache -f -t /usr/share/icons/hicolor >/dev/null 2>&1 || true
fi
echo "WayClick: add yourself to the 'input' group if you have not yet:"
echo "  sudo usermod -aG input \$USER    (then log out and back in)"
POST
chmod 0755 "$ROOT/DEBIAN/postinst"

OUT="$PWD/dist"; mkdir -p "$OUT"
DEB="$OUT/wayclick_${VERSION}_all.deb"
BUILD=$(mktemp -d); trap 'rm -rf "$ROOT" "$BUILD"' EXIT
echo "2.0" > "$BUILD/debian-binary"
tar -C "$ROOT/DEBIAN" -czf "$BUILD/control.tar.gz" --owner=0 --group=0 .
tar -C "$ROOT" --exclude=./DEBIAN -czf "$BUILD/data.tar.gz" --owner=0 --group=0 .
( cd "$BUILD" && ar rcD "$DEB" debian-binary control.tar.gz data.tar.gz )
echo "built: $DEB"
