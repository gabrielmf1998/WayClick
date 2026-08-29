#!/usr/bin/env bash
# WayClick installer — https://github.com/gabrielmf1998/WayClick
#
#   curl -fsSL https://raw.githubusercontent.com/gabrielmf1998/WayClick/main/install.sh | bash
#
# Installs to ~/.local (no root needed for the app itself). sudo is used only
# for the distro packages, the uinput udev rule and the 'input' group — each
# step tells you what it is about to run.
set -euo pipefail

REPO="https://github.com/gabrielmf1998/WayClick"
RAW="https://raw.githubusercontent.com/gabrielmf1998/WayClick/main"
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/wayclick"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
warn() { printf '\033[33m  ! %s\033[0m\n' "$*"; }
die()  { printf '\033[31mError: %s\033[0m\n' "$*" >&2; exit 1; }

need_sudo() {
    [ "$(id -u)" -eq 0 ] && { SUDO=""; return; }
    command -v sudo >/dev/null || die "sudo not found and not running as root"
    SUDO="sudo"
}

# ---------------------------------------------------------------- distro ----
detect_distro() {
    [ -r /etc/os-release ] || die "cannot read /etc/os-release"
    . /etc/os-release
    DISTRO_ID="${ID:-unknown}"
    DISTRO_LIKE="${ID_LIKE:-}"
    case " $DISTRO_ID $DISTRO_LIKE " in
        *" arch "*|*" archlinux "*|*" manjaro "*) FAMILY="arch" ;;
        *" fedora "*|*" rhel "*|*" centos "*)     FAMILY="fedora" ;;
        *" debian "*|*" ubuntu "*)                FAMILY="debian" ;;
        *) FAMILY="unknown" ;;
    esac
}

install_deps() {
    if python3 -c 'import PySide6.QtWidgets' 2>/dev/null; then
        info "PySide6 already present"
        python3 -c 'import PySide6.QtMultimedia' 2>/dev/null \
            || warn "PySide6.QtMultimedia missing — hotkey beeps will be silent"
        return
    fi
    bold "Installing PySide6 (needs sudo)"
    need_sudo
    case "$FAMILY" in
        arch)   $SUDO pacman -Sy --needed --noconfirm pyside6 ;;
        fedora) $SUDO dnf install -y python3-pyside6 ;;
        debian) $SUDO apt-get update
                $SUDO apt-get install -y python3-pyside6.qtwidgets \
                    python3-pyside6.qtmultimedia python3-pyside6.qtsvg \
                    || $SUDO apt-get install -y python3-pyside6.qtwidgets ;;
        *)      warn "unknown distro ($DISTRO_ID); trying pip"
                python3 -m pip install --user PySide6 ;;
    esac
    python3 -c 'import PySide6.QtWidgets' 2>/dev/null \
        || die "PySide6 still not importable — install it manually, see $REPO"
}

# ------------------------------------------------------------ permissions ---
setup_uinput() {
    # atualizar não deveria pedir senha: se a regra já existe e o device está
    # gravável, não há nada de root a fazer
    if [ -e /etc/udev/rules.d/99-wayclick-uinput.rules ] && [ -w /dev/uinput ]; then
        info "/dev/uinput already set up"
        return
    fi
    bold "Setting up /dev/uinput access (needs sudo)"
    need_sudo
    # o módulo não vem carregado em toda distro, e sem ele nem existe o device
    $SUDO modprobe uinput 2>/dev/null || true
    echo uinput | $SUDO tee /etc/modules-load.d/uinput.conf >/dev/null
    # e em várias distros o device nasce root:root, fora do grupo input
    $SUDO tee /etc/udev/rules.d/99-wayclick-uinput.rules >/dev/null <<'RULE'
# WayClick: let members of the 'input' group create virtual input devices
KERNEL=="uinput", SUBSYSTEM=="misc", MODE="0660", GROUP="input", OPTIONS+="static_node=uinput"
RULE
    $SUDO udevadm control --reload-rules 2>/dev/null || true
    $SUDO udevadm trigger --name-match=uinput 2>/dev/null || true
    info "udev rule installed at /etc/udev/rules.d/99-wayclick-uinput.rules"
}

setup_group() {
    if id -nG "$USER" | tr ' ' '\n' | grep -qx input; then
        info "user already in the 'input' group"
    else
        bold "Adding $USER to the 'input' group (needs sudo)"
        need_sudo
        $SUDO usermod -aG input "$USER"
        NEED_RELOGIN=1
    fi
    # o grupo só entra em sessões novas; se a atual não tem, avisa
    if ! (id -G | tr ' ' '\n' | grep -qx "$(getent group input | cut -d: -f3)"); then
        NEED_RELOGIN=1
    fi
}

# ------------------------------------------------------------------ app -----
fetch_app() {
    bold "Installing WayClick to $APP_DIR"
    mkdir -p "$APP_DIR" "$BIN_DIR" "$DESKTOP_DIR" "$ICON_DIR"
    if [ -f "$(dirname "$0")/wayclick.py" ]; then
        cp "$(dirname "$0")/wayclick.py" "$APP_DIR/wayclick.py"
        info "copied from local checkout"
    else
        command -v curl >/dev/null || die "curl not found"
        curl -fsSL "$RAW/wayclick.py" -o "$APP_DIR/wayclick.py"
        info "downloaded from $REPO"
    fi
    chmod +x "$APP_DIR/wayclick.py"

    cat > "$BIN_DIR/wayclick" <<EOF
#!/usr/bin/env bash
exec python3 "$APP_DIR/wayclick.py" "\$@"
EOF
    chmod +x "$BIN_DIR/wayclick"

    cat > "$ICON_DIR/wayclick.svg" <<'SVG'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <defs>
    <linearGradient id="p" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#3a4650"/><stop offset="1" stop-color="#222a31"/>
    </linearGradient>
  </defs>
  <rect x="2" y="2" width="60" height="60" rx="14" fill="url(#p)"/>
  <path d="M 24 20 m 0 -13 a 13 13 0 0 1 12.2 8.4" fill="none"
        stroke="#9aa3ab" stroke-width="4" stroke-linecap="round" opacity="0.8"/>
  <path d="M 24 20 m 0 -19 a 19 19 0 0 1 17.9 12.3" fill="none"
        stroke="#9aa3ab" stroke-width="4" stroke-linecap="round" opacity="0.45"/>
  <path d="M20 8 L20 47 L29 39 L35 54 L43 50 L37 35 L48 34 Z"
        fill="#f7f9fa" stroke="#12161a" stroke-width="5" stroke-linejoin="round"/>
</svg>
SVG

    cat > "$DESKTOP_DIR/wayclick.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=WayClick
Comment=Autoclicker for Wayland, using /dev/uinput
Exec=$BIN_DIR/wayclick
Icon=wayclick
Terminal=false
Categories=Utility;
StartupNotify=false
EOF
    refresh_caches
}

refresh_caches() {
    # sem isto o KDE continua mostrando o ícone antigo depois de atualizar:
    # ele guarda o ícone em cache e não repara que o arquivo mudou
    update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
    touch "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor" 2>/dev/null || true
    gtk-update-icon-cache -f -t \
        "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor" >/dev/null 2>&1 || true
    rm -f "${XDG_CACHE_HOME:-$HOME/.cache}/icon-cache.kcache" 2>/dev/null || true
    kbuildsycoca6 --noincremental >/dev/null 2>&1 || \
        kbuildsycoca5 --noincremental >/dev/null 2>&1 || true
}

# ----------------------------------------------------------------- main -----
NEED_RELOGIN=0
bold "WayClick installer"
detect_distro
info "distro: $DISTRO_ID (${FAMILY} family)"
[ "${XDG_SESSION_TYPE:-}" = "wayland" ] || warn "not a Wayland session right now"

install_deps
setup_uinput
setup_group
fetch_app

echo
bold "Done."
info "Run:  wayclick        (or find WayClick in your app menu)"
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) warn "$BIN_DIR is not in your PATH — add it to your shell rc" ;;
esac
if [ "$NEED_RELOGIN" = "1" ]; then
    echo
    warn "Log out and back in to pick up the 'input' group."
    info "Until then WayClick starts itself through 'sg input' automatically."
fi
