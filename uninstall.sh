#!/usr/bin/env bash
# Removes a WayClick installed by install.sh (everything lives in ~/.local).
#   bash uninstall.sh            keeps your settings and the udev rule
#   bash uninstall.sh --purge    also removes settings, autostart and udev rule
set -euo pipefail

APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/wayclick"
BIN="$HOME/.local/bin/wayclick"
DESKTOP="${XDG_DATA_HOME:-$HOME/.local/share}/applications/wayclick.desktop"
ICON="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps/wayclick.svg"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/wayclick.json"
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/wayclick.desktop"
RULE=/etc/udev/rules.d/99-wayclick-uinput.rules

purge=0
[ "${1:-}" = "--purge" ] && purge=1

pkill -f "$APP_DIR/wayclick.py" 2>/dev/null || true
for f in "$BIN" "$DESKTOP" "$ICON"; do
    [ -e "$f" ] && rm -f "$f" && echo "  removed $f"
done
[ -d "$APP_DIR" ] && rm -rf "$APP_DIR" && echo "  removed $APP_DIR"
[ -e "$AUTOSTART" ] && rm -f "$AUTOSTART" && echo "  removed $AUTOSTART"

if [ "$purge" = "1" ]; then
    [ -e "$CONFIG" ] && rm -f "$CONFIG" && echo "  removed $CONFIG"
    if [ -e "$RULE" ]; then
        echo "  removing the udev rule needs sudo"
        sudo rm -f "$RULE" && sudo udevadm control --reload-rules || true
        echo "  removed $RULE"
    fi
    echo
    echo "Your user is still in the 'input' group. To undo that too:"
    echo "  sudo gpasswd -d $USER input"
else
    echo
    echo "Kept: settings ($CONFIG) and the udev rule."
    echo "Use --purge to remove those as well."
fi
update-desktop-database "${XDG_DATA_HOME:-$HOME/.local/share}/applications" 2>/dev/null || true
rm -f "${XDG_CACHE_HOME:-$HOME/.cache}/icon-cache.kcache" 2>/dev/null || true
kbuildsycoca6 --noincremental >/dev/null 2>&1 || true
echo "Done."
