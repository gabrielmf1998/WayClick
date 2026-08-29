#!/usr/bin/env python3
"""WayClick — autoclicker para Wayland via /dev/uinput (kernel), sem X11."""
import ctypes, fcntl, glob, grp, json, math, os, pwd, re, select, shlex, shutil
import struct
import signal, subprocess, sys, tempfile, threading, time, wave

try:
    from PySide6.QtCore import (Qt, QObject, QPointF, QRectF, QTimer, QUrl, Signal,
                                Slot)
    from PySide6.QtGui import (QAction, QBrush, QColor, QDesktopServices, QIcon,
                               QLinearGradient,
                               QKeySequence, QPainter, QPen, QPixmap,
                               QShortcut)
    from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox,
                                   QFormLayout, QDoubleSpinBox, QGroupBox,
                                   QHBoxLayout, QLabel, QMenu, QMenuBar,
                                   QMessageBox, QPushButton, QScrollArea,
                                   QSpinBox, QSystemTrayIcon, QVBoxLayout,
                                   QWidget, QFrame, QListView, QTabWidget)
except ImportError:
    sys.exit("PySide6 is required.\n"
             "  Fedora/RHEL:   sudo dnf install python3-pyside6\n"
             "  Arch:          sudo pacman -S pyside6\n"
             "  Debian/Ubuntu: sudo apt install python3-pyside6.qtwidgets "
             "python3-pyside6.qtmultimedia\n"
             "  any distro:    pip install --user PySide6")

VERSION = "1.3.1"
HOMEPAGE = "https://github.com/gabrielmf1998/WayClick"

# ---------------------------------------------------------------- uinput ----
UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_RELBIT = 0x40045564, 0x40045565, 0x40045566
UI_SET_MSCBIT = 0x40045568
UI_DEV_CREATE, UI_DEV_DESTROY = 0x5501, 0x5502
UI_GET_SYSNAME = (2 << 30) | (64 << 16) | (ord("U") << 8) | 44
EV_SYN, EV_KEY, EV_REL, EV_ABS, EV_MSC = 0, 1, 2, 3, 4
SYN_REPORT = 0
BTN = {"Left": 0x110, "Right": 0x111, "Middle": 0x112}
BTN_ALL = range(0x110, 0x118)           # left..task
REL_ALL = (0, 1, 6, 7, 8, 9, 0x0b, 0x0c)  # x,y,hwheel,dial,wheel,misc,hi-res
EVENT_FMT = "llHHi"                      # struct input_event
EVENT_SIZE = struct.calcsize(EVENT_FMT)  # 24 em 64 bits, 16 em 32 bits
DEV_NAME = b"wayclick-virtual-mouse"


def _ior(nr, size):                      # _IOR('E', nr, size)
    return (2 << 30) | (size << 16) | (ord("E") << 8) | nr


EVIOCGNAME = _ior(0x06, 256)
EVIOCGID = _ior(0x02, 8)                                     # struct input_id
EVIOCGRAB = (1 << 30) | (4 << 16) | (ord("E") << 8) | 0x90   # _IOW('E',0x90,int)

OWN_NODES = set()     # /dev/input/eventN dos nossos devices virtuais


def eviocgbit(evtype, nbytes):
    return _ior(0x20 + evtype, nbytes)


def _ioctl_buf(fd, request, nbytes):
    buf = bytearray(nbytes)
    try:
        fcntl.ioctl(fd, request, buf)
    except OSError:
        return bytearray(nbytes)
    return buf


def dev_name(fd):
    return bytes(_ioctl_buf(fd, EVIOCGNAME, 256)).split(b"\x00", 1)[0]


def dev_bits(fd, evtype, nbytes=96):
    return _ioctl_buf(fd, eviocgbit(evtype, nbytes), nbytes)


def has_bit(bits, code):
    return code // 8 < len(bits) and bits[code // 8] >> (code % 8) & 1


def uinput_node(fd):
    """/dev/input/eventN do device que ACABAMOS de criar — é assim que a gente
    se reconhece: o clone tem o mesmo nome do mouse real, filtrar por nome não
    serviria."""
    buf = bytearray(64)
    try:
        fcntl.ioctl(fd, UI_GET_SYSNAME, buf)
    except OSError:
        return None
    sysname = bytes(buf).split(b"\x00", 1)[0].decode()
    for p in glob.glob(f"/sys/class/input/{sysname}/event*"):
        return "/dev/input/" + os.path.basename(p)
    return None


class VirtualMouse:
    """Mouse virtual no kernel: o compositor Wayland o trata como hardware.

    Se `clone_fd` for o mouse físico do usuário, o device virtual nasce com o
    mesmo nome e os mesmos vendor/product/version. Isso importa de verdade: o
    KDE guarda velocidade e perfil de aceleração por dispositivo
    ([Libinput][vendor][product][nome] no kcminputrc), e o hwdb casa o DPI pelo
    mesmo modalias — sem clonar, o ponteiro cai no default e a sensibilidade
    parece ter "resetado" quando o mouse real está capturado.
    """

    def __init__(self, clone_fd=None):
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        name, ident = DEV_NAME, (0x03, 0x1234, 0x5678, 1)
        if clone_fd is not None:
            name, ident = self._identity(clone_fd)
        self.name = name
        self.cloned = clone_fd is not None
        if clone_fd is None or not self._copy_caps(clone_fd):
            fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
            fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_REL)
            for code in BTN_ALL:
                fcntl.ioctl(self.fd, UI_SET_KEYBIT, code)
            for rel in REL_ALL:  # REL_X/Y fazem o libinput classificar como mouse
                fcntl.ioctl(self.fd, UI_SET_RELBIT, rel)
        dev = (struct.pack("80s", name)
               + struct.pack("HHHH", *ident)
               + struct.pack("i", 0)
               + b"\x00" * (4 * 64 * 4))          # struct uinput_user_dev
        os.write(self.fd, dev)
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        self.node = self._own_node()
        if self.node:
            OWN_NODES.add(self.node)
        time.sleep(0.4)  # deixa udev/libinput enumerarem o device

    @staticmethod
    def _identity(fd):
        ident = struct.unpack("HHHH", bytes(_ioctl_buf(fd, EVIOCGID, 8)))
        return dev_name(fd)[:79] or DEV_NAME, ident

    def _copy_caps(self, fd):
        """Espelha as capacidades do mouse físico (botões, eixos, MSC_SCAN)."""
        copied = False
        for evtype, setter, nbytes in ((EV_KEY, UI_SET_KEYBIT, 96),
                                       (EV_REL, UI_SET_RELBIT, 8),
                                       (EV_MSC, UI_SET_MSCBIT, 8)):
            bits = dev_bits(fd, evtype, nbytes)
            codes = [c for c in range(nbytes * 8) if has_bit(bits, c)]
            if not codes:
                continue
            fcntl.ioctl(self.fd, UI_SET_EVBIT, evtype)
            for c in codes:
                fcntl.ioctl(self.fd, setter, c)
            copied = True
        return copied

    def _own_node(self):
        return uinput_node(self.fd)

    @staticmethod
    def edge(code, value):
        """Um flanco do botão (press ou release) + SYN, em um write só."""
        return (struct.pack(EVENT_FMT, 0, 0, EV_KEY, code, value)
                + struct.pack(EVENT_FMT, 0, 0, EV_SYN, SYN_REPORT, 0))

    @staticmethod
    def packet(code):
        """press + release colados. Serve para quem escuta evento, mas some
        para quem consulta estado — ver o comentário em HOLD_S."""
        return VirtualMouse.edge(code, 1) + VirtualMouse.edge(code, 0)

    def _emit(self, etype, code, value):
        os.write(self.fd, struct.pack(EVENT_FMT, 0, 0, etype, code, value))

    def click(self, code):
        os.write(self.fd, self.packet(code))

    def move(self, dx, dy=0):
        self._emit(EV_REL, 0, dx)
        if dy:
            self._emit(EV_REL, 1, dy)
        self._emit(EV_SYN, SYN_REPORT, 0)

    def close(self):
        OWN_NODES.discard(self.node)
        try:
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        except OSError:
            pass
        os.close(self.fd)


# teclado completo para a macro (códigos de linux/input-event-codes.h).
# Nomes em inglês de propósito: rótulo de tecla é o mesmo em qualquer idioma.
KEYS = {
    "Space": 57, "Enter": 28, "Tab": 15, "Backspace": 14, "Esc": 1,
    "Delete": 111, "Insert": 110, "Home": 102, "End": 107,
    "Page Up": 104, "Page Down": 109,
    "Up": 103, "Down": 108, "Left": 105, "Right": 106,
    "Left Shift": 42, "Right Shift": 54, "Left Ctrl": 29, "Right Ctrl": 97,
    "Left Alt": 56, "Right Alt": 100, "Left Super": 125, "Right Super": 126,
    "Caps Lock": 58, "Num Lock": 69, "Scroll Lock": 70, "Menu": 127,
    "Print Screen": 99, "Pause": 119,
    "A": 30, "B": 48, "C": 46, "D": 32, "E": 18, "F": 33, "G": 34, "H": 35,
    "I": 23, "J": 36, "K": 37, "L": 38, "M": 50, "N": 49, "O": 24, "P": 25,
    "Q": 16, "R": 19, "S": 31, "T": 20, "U": 22, "V": 47, "W": 17, "X": 45,
    "Y": 21, "Z": 44,
    "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9, "9": 10,
    "0": 11,
    "F1": 59, "F2": 60, "F3": 61, "F4": 62, "F5": 63, "F6": 64, "F7": 65,
    "F8": 66, "F9": 67, "F10": 68, "F11": 87, "F12": 88,
    "F13": 183, "F14": 184, "F15": 185, "F16": 186, "F17": 187, "F18": 188,
    "F19": 189, "F20": 190, "F21": 191, "F22": 192, "F23": 193, "F24": 194,
    "Numpad 0": 82, "Numpad 1": 79, "Numpad 2": 80, "Numpad 3": 81,
    "Numpad 4": 75, "Numpad 5": 76, "Numpad 6": 77, "Numpad 7": 71,
    "Numpad 8": 72, "Numpad 9": 73, "Numpad .": 83, "Numpad +": 78,
    "Numpad -": 74, "Numpad *": 55, "Numpad /": 98, "Numpad Enter": 96,
    "- (minus)": 12, "= (equal)": 13, "[": 26, "]": 27, "\\": 43,
    "; (semicolon)": 39, "' (apostrophe)": 40, "` (grave)": 41,
    ", (comma)": 51, ". (period)": 52, "/ (slash)": 53,
    "Volume Up": 115, "Volume Down": 114, "Mute": 113,
    "Play/Pause": 164, "Next Track": 163, "Previous Track": 165,
}
KB_NAME = b"wayclick-virtual-keyboard"


class VirtualKeyboard:
    """Teclado virtual no kernel, para a macro de teclas.

    Declara todas as teclas de KEYS de uma vez, então trocar a tecla escolhida
    não exige recriar o device.
    """

    def __init__(self):
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
        for code in set(KEYS.values()):
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, code)
        os.write(self.fd, struct.pack("80s", KB_NAME)
                 + struct.pack("HHHH", 0x03, 0x1234, 0x5679, 1)
                 + struct.pack("i", 0) + b"\x00" * (4 * 64 * 4))
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        self.node = uinput_node(self.fd)
        if self.node:
            OWN_NODES.add(self.node)      # o watcher de atalho não pode nos ouvir
        time.sleep(0.4)

    @staticmethod
    def packet(code, value):
        return (struct.pack(EVENT_FMT, 0, 0, EV_KEY, code, value)
                + struct.pack(EVENT_FMT, 0, 0, EV_SYN, SYN_REPORT, 0))

    def tap(self, code, hold=0.04):
        """Aperta, segura e solta. O hold não é enfeite: com press e release
        colados a tecla não existe para quem consulta estado por quadro."""
        os.write(self.fd, self.packet(code, 1))
        time.sleep(hold)
        os.write(self.fd, self.packet(code, 0))

    def set(self, code, down):
        os.write(self.fd, self.packet(code, 1 if down else 0))

    def close(self):
        OWN_NODES.discard(self.node)
        try:
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        except OSError:
            pass
        os.close(self.fd)


class AntiAfk(threading.Thread):
    """Move 1 pixel e volta, periodicamente.

    Os dois movimentos vão separados por GAP de propósito: mandados no mesmo
    instante, o compositor somaria +1 e -1 no mesmo quadro e o jogo não veria
    deslocamento nenhum — que é justamente o que precisa ser visto para o
    contador de inatividade zerar.

    E o sentido inverte a cada ciclo. A aceleração do libinput escala cada
    evento pela velocidade estimada, e a ida (depois de 1 s parado) recebe um
    fator diferente da volta (50 ms depois), então elas não se anulam sozinhas.
    Alternando o sentido, o resíduo de um ciclo cancela o do seguinte e a
    posição fica presa entre dois valores em vez de derivar pela tela —
    medido: 0,0000 px de deriva.

    STEP é 4 e não 1 porque a aceleração encolhe o deslocamento: com o perfil
    flat a -0,55 daqui, 1 unidade virou 0,402 px de tela, ou seja, nem um pixel
    inteiro, e um jogo que lê coordenadas inteiras não veria movimento nenhum.
    4 unidades dão ~3 px, o bastante para registrar em qualquer configuração e
    ainda assim imperceptível, já que volta em 50 ms.
    """
    GAP = 0.05
    STEP = 4

    def __init__(self, mouse, seconds):
        super().__init__(daemon=True)
        self.mouse, self.seconds = mouse, max(seconds, 1)
        self._stop = threading.Event()
        self.count = 0

    def run(self):
        step = self.STEP
        while not self._stop.wait(self.seconds):
            try:
                self.mouse.move(step)
                stopping = self._stop.wait(self.GAP)
                self.mouse.move(-step)
                if stopping:
                    return
                self.count += 1
                step = -step
            except OSError:
                return                      # device recriado/fechado

    def stop(self):
        self._stop.set()


class KeyMacro(threading.Thread):
    """Repete uma tecla no intervalo dado, ou a mantém pressionada."""

    HOLD_S = 0.04

    def __init__(self, kb, interval_ms, keycode, hold=False):
        super().__init__(daemon=True)
        self.kb, self.code, self.hold = kb, keycode, hold
        self.interval = max(interval_ms, 1) / 1000.0
        self.press_time = min(self.HOLD_S, self.interval * 0.5)
        self._stop = threading.Event()
        self.count = 0

    def run(self):
        try:
            if self.hold:
                self.kb.set(self.code, True)
                self._stop.wait()
            else:
                nxt = time.perf_counter()
                while not self._stop.is_set():
                    self.kb.set(self.code, True)
                    self._stop.wait(self.press_time)
                    self.kb.set(self.code, False)
                    self.count += 1
                    nxt += self.interval
                    rest = nxt - time.perf_counter()
                    if rest > 0:
                        self._stop.wait(rest)
                    else:
                        nxt = time.perf_counter()
        except OSError:
            pass
        finally:
            if self.hold:                   # nunca deixar tecla presa
                try:
                    self.kb.set(self.code, False)
                except OSError:
                    pass

    def stop(self):
        self._stop.set()


class Clicker(threading.Thread):
    """Loop de cliques com deadline absoluto.

    Acima de ~1 kHz o sleep do SO (granularidade ~50-100 us) não dá conta, então
    o loop dorme só o "grosso" do intervalo e queima os últimos SPIN_S em
    busy-wait — é o que permite chegar em 0,1 ms (10.000 cliques/s).
    """
    SPIN_S = 0.0006  # busy-wait nos últimos 600 us
    HOLD_S = 0.04    # ver abaixo

    def __init__(self, mouse, interval_ms, button, limit=0):
        super().__init__(daemon=True)
        self.mouse, self.limit = mouse, limit
        self.interval = max(interval_ms, 0.1) / 1000.0
        # busy-wait curto: só o suficiente pra cobrir a granularidade do sleep
        self.spin = min(self.SPIN_S, self.interval * 0.3)
        # O botão precisa ficar baixo por um tempo real. Jogo não escuta evento,
        # ele pergunta "o botão está apertado?" a cada quadro; com press e
        # release colados, medimos 0 de 126 quadros pegando o botão baixo — o
        # clique simplesmente não existia para ele. Metade do intervalo, no
        # máximo 40 ms: a 100 ms dá 40 ms (uns 2,7 quadros a 60 fps) e a 0,1 ms
        # dá 50 us, preservando os 10.000 cliques/s.
        self.hold = min(self.HOLD_S, self.interval * 0.5)
        self.press = VirtualMouse.edge(button, 1)
        self.release = VirtualMouse.edge(button, 0)
        self._stop = threading.Event()
        self.count = 0

    def _until(self, deadline):
        """Dorme o grosso e queima o resto em busy-wait."""
        rest = deadline - time.perf_counter() - self.spin
        if rest > 0:
            self._stop.wait(rest)
        while time.perf_counter() < deadline:
            pass

    def run(self):
        try:  # ajuda a estabilizar o jitter; falha silenciosa sem privilégio
            os.nice(-5)
        except OSError:
            pass
        # o busy-wait segura a GIL; encurtar o switch interval mantém a UI e o
        # atalho global respondendo enquanto clicamos em alta frequência
        sys.setswitchinterval(0.002)
        write, fd = os.write, self.mouse.fd
        clock, stop = time.perf_counter, self._stop
        interval, hold = self.interval, self.hold
        nxt = clock()
        while not stop.is_set():
            write(fd, self.press)
            self._until(nxt + hold)
            write(fd, self.release)
            self.count += 1
            if self.limit and self.count >= self.limit:
                break
            nxt += interval
            self._until(nxt)
            if clock() - nxt > 0.05:  # atrasou demais (suspensão, carga): ressincroniza
                nxt = clock()

    def stop(self):
        self._stop.set()


# ------------------------------------------------------- hotkey global ------
# Lê /dev/input/event* direto: é o único jeito de atalho global no Wayland
# sem cooperação do compositor. Exige o usuário no grupo 'input'.
KEYNAMES = {"F6": 64, "F7": 65, "F8": 66, "F9": 67, "F10": 68, "F11": 87,
            "F12": 88, "Insert": 110, "Pause": 119, "ScrollLock": 70,
            "KP_Add": 78, "KP_Sub": 74}
KEY_ESC = 1
BTN_TOOL_FINGER = 0x145


def is_uinput_device(path):
    """Devices criados via uinput (os nossos, os de outra instância, sobras)
    ficam em /sys/devices/virtual/input/. Mouse USB fica sob o barramento e
    mouse Bluetooth sob virtual/misc/uhid/, então nenhum real é excluído —
    importante porque o nosso clone tem o mesmo nome do mouse do usuário."""
    real = os.path.realpath(f"/sys/class/input/{os.path.basename(path)}/device")
    return real.startswith("/sys/devices/virtual/input/")


def is_pointer_shaped(fd):
    return has_bit(dev_bits(fd, EV_KEY), BTN["Left"]) \
        and has_bit(dev_bits(fd, EV_REL, 8), 0)


def is_keyboard(f):
    """Só teclados interessam. Devices uinput NÃO são descartados aqui: quem usa
    keyd, kmonad ou input-remapper digita por um teclado virtual, e ele precisa
    valer como fonte de atalho. Só o que é uinput E tem cara de ponteiro fica de
    fora — esse é um clone do WayClick, e a 10 kHz custaria caro acompanhá-lo."""
    if is_uinput_device(f.name) and is_pointer_shaped(f.fileno()):
        return False
    keys = dev_bits(f.fileno(), EV_KEY)
    return has_bit(keys, KEY_ESC) or any(has_bit(keys, c)
                                         for c in KEYNAMES.values())


def has_mouse_caps(fd):
    """Botão + eixo relativo, sem eixo absoluto (o que descarta touchpad)."""
    keys, rels = dev_bits(fd, EV_KEY), dev_bits(fd, EV_REL, 8)
    if not (has_bit(keys, BTN["Left"]) and has_bit(rels, 0)):
        return False
    return not has_bit(dev_bits(fd, EV_ABS, 8), 0) \
        and not has_bit(keys, BTN_TOOL_FINGER)


def is_mouse(f):
    """Mouse físico."""
    return not is_uinput_device(f.name) and has_mouse_caps(f.fileno())


def is_mouse_any(f):
    """Inclui ponteiros virtuais — quem usa remapeador de mouse só tem esses."""
    return has_mouse_caps(f.fileno())


def event_paths():
    """/dev/input/event* em ordem numérica (event2 antes de event10)."""
    paths = glob.glob("/dev/input/event*")
    return sorted(paths, key=lambda p: int(re.sub(r"\D", "", p) or 0))


def open_devices(match, only=None):
    """Abre /dev/input/event* que passem em `match`. Devolve (abertos, negados).
    `only` restringe a caminhos específicos (usado pra capturar só o mouse
    escolhido, em vez de sequestrar todos os apontadores da máquina)."""
    files, denied = [], 0
    for path in event_paths():
        if path in OWN_NODES or (only is not None and path not in only):
            continue
        try:
            f = open(path, "rb", buffering=0)
        except OSError:
            denied += 1
            continue
        if match(f):
            files.append(f)
        else:
            f.close()
    return files, denied


def list_mice():
    """[(caminho, nome)] de todo mouse legível — qualquer marca, quantos forem."""
    found = []
    files = open_devices(is_mouse)[0] or open_devices(is_mouse_any)[0]
    for f in files:
        found.append((f.name, dev_name(f.fileno()).decode(errors="replace")
                      or os.path.basename(f.name)))
        f.close()
    return found


def input_access():
    """(pode_ler, e_membro_do_grupo_input) — os dois divergem até relogar."""
    can_read = any(os.access(p, os.R_OK)
                   for p in glob.glob("/dev/input/event*"))
    try:
        gid = grp.getgrnam("input").gr_gid
        user = pwd.getpwuid(os.getuid()).pw_name
        member = gid in os.getgrouplist(user, os.getgid())
    except (KeyError, OSError):
        member = False
    return can_read, member


def reexec_with_input_group():
    """Ganhar o grupo 'input' normalmente exige relogar. `sg` faz na hora, sem
    senha, quando o usuário já é membro — então reexecutamos por baixo dele."""
    if os.environ.get("WAYCLICK_SG") or not shutil.which("sg"):
        return
    can_read, member = input_access()
    if can_read or not member:
        return
    env = dict(os.environ, WAYCLICK_SG="1")
    cmd = shlex.join([sys.executable, os.path.abspath(__file__)] + sys.argv[1:])
    try:
        os.execvpe("sg", ["sg", "input", "-c", cmd], env)
    except OSError:
        pass


class HotkeyWatcher(QObject):
    pressed = Signal()
    released = Signal()

    def __init__(self, keycode):
        super().__init__()
        self.keycode = keycode
        self._run = True
        self.error = None
        self.files, self.denied = open_devices(is_keyboard)
        if not self.files:
            self.error = ("sem acesso a /dev/input" if self.denied
                          else "nenhum teclado encontrado")

    @property
    def ok(self):
        return bool(self.files)

    def start(self):
        if self.files:
            threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while self._run:
            try:
                r, _, _ = select.select(self.files, [], [], 0.5)
            except (OSError, ValueError):
                return
            for f in r:
                try:
                    data = f.read(4096)
                except OSError:
                    continue
                while data and len(data) >= EVENT_SIZE:
                    _, _, etype, code, value = struct.unpack_from(EVENT_FMT, data)
                    if etype == EV_KEY and code == self.keycode:
                        if value == 1:
                            self.pressed.emit()
                        elif value == 0:
                            self.released.emit()
                    data = data[EVENT_SIZE:]

    def set_key(self, keycode):
        self.keycode = keycode

    def stop(self):
        self._run = False


# --------------------------------------------------- segurar o botão --------
class MouseHold(QObject):
    """Modo "clicar enquanto segura": captura o mouse físico e o retransmite.

    O compositor agrega o estado dos botões por seat, então enquanto o botão
    físico está pressionado ele DESCARTA qualquer clique que a gente injete —
    medido: 0 de 20. Por isso a captura (EVIOCGRAB): o compositor deixa de ver
    o mouse real e passa a ver só o nosso, e nós repassamos tudo (movimento,
    roda, outros botões) menos o botão-gatilho, que vira o autoclick.
    """
    pressed = Signal()
    released = Signal()
    failed = Signal(str)

    def __init__(self, mouse, button_code, only=None):
        super().__init__()
        self.mouse, self.button = mouse, button_code
        self.only = only              # captura só o mouse escolhido na UI
        self.files = []
        self._run = False
        self._thread = None

    def start(self):
        files, denied = open_devices(is_mouse_any, self.only)
        if not files:
            self.failed.emit("no mouse found" if not denied
                             else "no access to /dev/input")
            return False
        grabbed = []
        for f in files:
            try:
                fcntl.ioctl(f, EVIOCGRAB, 1)
                grabbed.append(f)
            except OSError:
                f.close()
        if not grabbed:
            self.failed.emit("could not grab the mouse")
            return False
        for f in files:
            if f not in grabbed:
                f.close()
        self.files = grabbed
        self._run = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return True

    def _loop(self):
        out_fd, trigger = self.mouse.fd, self.button
        while self._run:
            try:
                r, _, _ = select.select(self.files, [], [], 0.2)
            except (OSError, ValueError):
                break
            for f in r:
                try:
                    data = f.read(4096)          # lote inteiro de uma vez
                except OSError:
                    continue
                if not data:
                    continue
                keep = bytearray()
                for i in range(0, len(data) - EVENT_SIZE + 1, EVENT_SIZE):
                    _, _, etype, code, value = struct.unpack_from(EVENT_FMT, data, i)
                    if etype == EV_KEY and code == trigger:
                        if value == 1:
                            self.pressed.emit()
                        elif value == 0:
                            self.released.emit()
                        continue                 # gatilho não é repassado
                    keep += data[i:i + EVENT_SIZE]
                if keep:
                    try:
                        os.write(out_fd, bytes(keep))
                    except OSError:
                        pass
        self._release()

    def _release(self):
        for f in self.files:
            try:
                fcntl.ioctl(f, EVIOCGRAB, 0)
            except OSError:
                pass
            try:
                f.close()
            except OSError:
                pass
        self.files = []

    def stop(self):
        if not self._run:
            return
        self._run = False
        if self._thread:
            self._thread.join(timeout=1.0)
        self._release()


# ------------------------------------------------------------------- som ----
class Beeper:
    """Dois blips curtos (agudo = ligou, grave = desligou). Sem arquivo externo:
    os WAVs são sintetizados no primeiro uso."""
    RATE = 44100

    def __init__(self):
        self.effects = {}
        self.player = None
        self.paths = {}
        d = os.path.join(os.environ.get("XDG_RUNTIME_DIR")
                         or tempfile.gettempdir(), f"wayclick-{os.getuid()}")
        try:
            os.makedirs(d, exist_ok=True)
            for name, freq in (("on", 1046.5), ("off", 622.25)):
                path = os.path.join(d, f"{name}.wav")
                if not os.path.exists(path):
                    self._write_wav(path, freq)
                self.paths[name] = path
        except OSError:
            return
        try:                     # QtMultimedia é pacote separado em algumas distros
            from PySide6.QtCore import QUrl
            from PySide6.QtMultimedia import QSoundEffect
        except ImportError:
            self.player = (shutil.which("paplay") or shutil.which("pw-play")
                           or shutil.which("aplay"))
            return
        for name, path in self.paths.items():
            eff = QSoundEffect()
            eff.setSource(QUrl.fromLocalFile(path))
            eff.setVolume(0.35)
            self.effects[name] = eff

    @classmethod
    def _write_wav(cls, path, freq, ms=70):
        n = int(cls.RATE * ms / 1000)
        fade = int(cls.RATE * 0.006)      # rampa: sem ela o blip estala
        frames = bytearray()
        for i in range(n):
            amp = min(1.0, i / fade, (n - i) / fade)
            v = int(20000 * amp * math.sin(2 * math.pi * freq * i / cls.RATE))
            frames += struct.pack("<h", v)
        with wave.open(path, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(cls.RATE)
            w.writeframes(bytes(frames))

    def play(self, name):
        eff = self.effects.get(name)
        if eff is not None:
            eff.play()
        elif self.player and name in self.paths:
            subprocess.Popen([self.player, self.paths[name]],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# -------------------------------------- injeção direcionada a uma janela ----
class XKeyEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong),
                ("send_event", ctypes.c_int), ("display", ctypes.c_void_p),
                ("window", ctypes.c_ulong), ("root", ctypes.c_ulong),
                ("subwindow", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("x", ctypes.c_int), ("y", ctypes.c_int),
                ("x_root", ctypes.c_int), ("y_root", ctypes.c_int),
                ("state", ctypes.c_uint), ("keycode", ctypes.c_uint),
                ("same_screen", ctypes.c_int)]


class XEvent(ctypes.Union):
    _fields_ = [("type", ctypes.c_int), ("xkey", XKeyEvent),
                ("pad", ctypes.c_long * 24)]


class XClassHint(ctypes.Structure):
    # c_void_p e não c_char_p: com c_char_p o ctypes entrega bytes já copiados e
    # o XFree acabaria liberando o ponteiro errado, corrompendo o heap
    _fields_ = [("res_name", ctypes.c_void_p), ("res_class", ctypes.c_void_p)]



# No Wayland não existe "entregar este evento naquela janela": o input pertence
# ao seat e vai para quem está em foco — nem o fake_input do KWin, único
# protocolo de injeção que ele implementa, tem argumento de surface.
#
# O caminho que funciona é outro: teclado segue o foco. Damos foco à janela
# alvo por um instante, mandamos a tecla pelo nosso teclado uinput (evento de
# kernel, que app nenhum pode ignorar, ao contrário de XSendEvent sintético) e
# devolvemos o foco. Medido: 12 ms para ativar, 12 ms para devolver, a tecla
# cai na alvo e zero vaza para a janela que o usuário estava usando.
#
# Só vale para teclado. Clique segue o cursor, não o foco, então exigiria
# teleportar o ponteiro do usuário — aí sim atrapalharia.
KWIN_LIST_JS = """
var out = [];
var ws = workspace.windowList ? workspace.windowList() : workspace.clientList();
for (var i = 0; i < ws.length; i++) {
  var w = ws[i];
  if (!w.normalWindow || w.skipTaskbar) continue;
  out.push({id: String(w.internalId), cls: String(w.resourceClass),
            pid: w.pid, title: String(w.caption), active: w.active === true});
}
callDBus("%(svc)s", "%(path)s", "%(iface)s", "reply", JSON.stringify(out));
"""

KWIN_ACTIVATE_JS = """
var ws = workspace.windowList ? workspace.windowList() : workspace.clientList();
var prev = "";
for (var i = 0; i < ws.length; i++) if (ws[i].active) prev = String(ws[i].internalId);
for (var i = 0; i < ws.length; i++) {
  if (String(ws[i].internalId) === "%(target)s") workspace.activeWindow = ws[i];
}
callDBus("%(svc)s", "%(path)s", "%(iface)s", "reply", prev);
"""


class XInject:
    """Injeção de tecla numa janela X11 específica, via XSendEvent.

    Este é o único caminho que entrega input a uma janela sem mexer no foco do
    usuário: o X11 endereça o evento à janela, e o cliente processa mesmo sem
    foco e mesmo minimizado — medido, 3 de 3 nas duas situações. É o oposto do
    Wayland, onde o evento vai para quem tem foco e ponto.

    Vale só para clientes X11 (sob Xwayland). App Wayland nativo não tem window
    id para endereçar. E o evento vai marcado como sintético, então app que só
    aceita input "de verdade" pode ignorar — por isso a UI testa antes.
    """
    KEYPRESS, KEYRELEASE = 2, 3
    PRESS_MASK, RELEASE_MASK = 1 << 0, 1 << 1

    def __init__(self):
        self.ok = False
        self.error = None
        self.dpy = None
        if not os.environ.get("DISPLAY"):
            self.error = "no X11 display (Xwayland not running)"
            return
        try:
            import ctypes.util
            lib = ctypes.util.find_library("X11")
            self.x = ctypes.CDLL(lib) if lib else None
        except OSError:
            self.x = None
        if not self.x:
            self.error = "libX11 not found"
            return
        self._declare()
        self.dpy = self.x.XOpenDisplay(None)
        if not self.dpy:
            self.error = "could not open the X11 display"
            return
        self.root = self.x.XDefaultRootWindow(self.dpy)
        self.ok = True

    def _declare(self):
        """ctypes sem argtypes trunca ponteiro para int e derruba o processo."""
        x, D, W = self.x, ctypes.c_void_p, ctypes.c_ulong
        x.XOpenDisplay.argtypes = [ctypes.c_char_p]; x.XOpenDisplay.restype = D
        x.XDefaultRootWindow.argtypes = [D]; x.XDefaultRootWindow.restype = W
        x.XInternAtom.argtypes = [D, ctypes.c_char_p, ctypes.c_int]
        x.XInternAtom.restype = W
        x.XGetWindowProperty.argtypes = [
            D, W, W, ctypes.c_long, ctypes.c_long, ctypes.c_int, W,
            ctypes.POINTER(W), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte))]
        x.XGetWindowProperty.restype = ctypes.c_int
        x.XFree.argtypes = [ctypes.c_void_p]
        x.XStringToKeysym.argtypes = [ctypes.c_char_p]
        x.XStringToKeysym.restype = W
        x.XKeysymToKeycode.argtypes = [D, W]
        x.XKeysymToKeycode.restype = ctypes.c_ubyte
        x.XSendEvent.argtypes = [D, W, ctypes.c_int, ctypes.c_long,
                                 ctypes.POINTER(XEvent)]
        x.XSendEvent.restype = ctypes.c_int
        x.XFlush.argtypes = [D]; x.XFlush.restype = ctypes.c_int
        x.XGetClassHint.argtypes = [D, W, ctypes.POINTER(XClassHint)]
        x.XGetClassHint.restype = ctypes.c_int
        x.XkbKeycodeToKeysym.argtypes = [D, ctypes.c_ubyte, ctypes.c_uint,
                                         ctypes.c_uint]
        x.XkbKeycodeToKeysym.restype = W
        x.XKeysymToString.argtypes = [W]
        x.XKeysymToString.restype = ctypes.c_char_p
        x.XQueryTree.argtypes = [D, W, ctypes.POINTER(W), ctypes.POINTER(W),
                                 ctypes.POINTER(ctypes.POINTER(W)),
                                 ctypes.POINTER(ctypes.c_uint)]
        x.XQueryTree.restype = ctypes.c_int

    def _prop(self, win, name, prop_type):
        actual_type = ctypes.c_ulong()
        actual_fmt = ctypes.c_int()
        nitems = ctypes.c_ulong()
        after = ctypes.c_ulong()
        data = ctypes.POINTER(ctypes.c_ubyte)()
        atom = self.x.XInternAtom(self.dpy, name.encode(), 0)
        r = self.x.XGetWindowProperty(
            self.dpy, win, atom, 0, 1024, 0, prop_type,
            ctypes.byref(actual_type), ctypes.byref(actual_fmt),
            ctypes.byref(nitems), ctypes.byref(after), ctypes.byref(data))
        if r != 0 or not data:
            return None, 0
        return data, nitems.value

    def windows(self):
        """[(xid, pid, classe)] das janelas X11 gerenciadas agora."""
        out = []
        data, n = self._prop(self.root, "_NET_CLIENT_LIST", 33)   # XA_WINDOW
        if not data:
            return out
        ids = ctypes.cast(data, ctypes.POINTER(ctypes.c_ulong))
        wins = [ids[i] for i in range(n)]
        self.x.XFree(data)
        for w in wins:
            pid = 0
            d, cnt = self._prop(w, "_NET_WM_PID", 6)              # XA_CARDINAL
            if d and cnt:
                pid = ctypes.cast(d, ctypes.POINTER(ctypes.c_ulong))[0]
                self.x.XFree(d)
            hint = XClassHint()
            cls = ""
            if self.x.XGetClassHint(self.dpy, w, ctypes.byref(hint)):
                if hint.res_class:
                    cls = ctypes.string_at(hint.res_class).decode(errors="replace")
                    self.x.XFree(hint.res_class)
                if hint.res_name:
                    self.x.XFree(hint.res_name)
            out.append((w, int(pid), cls))
        return out

    def keysym_name(self, evdev_code):
        """Código evdev -> nome de keysym do X, direto do mapa de teclado.
        Evita manter uma tabela de tradução à mão."""
        if not self.ok:
            return None
        ks = self.x.XkbKeycodeToKeysym(self.dpy, evdev_code + 8, 0, 0)
        if not ks:
            return None
        name = self.x.XKeysymToString(ks)
        return name.decode() if name else None

    def children(self, win, depth=2, limit=6):
        """Subjanelas do alvo. Muita aplicação recebe o input numa janela
        filha, não na de topo que o gerenciador lista."""
        out = []
        root, parent = ctypes.c_ulong(), ctypes.c_ulong()
        kids = ctypes.POINTER(ctypes.c_ulong)()
        nkids = ctypes.c_uint()
        if not self.x.XQueryTree(self.dpy, win, ctypes.byref(root),
                                 ctypes.byref(parent), ctypes.byref(kids),
                                 ctypes.byref(nkids)):
            return out
        for i in range(min(nkids.value, limit)):
            child = kids[i]
            out.append(child)
            if depth > 1:
                out.extend(self.children(child, depth - 1, limit))
        if kids:
            self.x.XFree(kids)
        return out[:limit]

    def send_key(self, win, keysym_name, hold=0.05):
        """press + release endereçados à janela. O hold importa: sem ele, quem
        consulta estado por quadro não vê nada."""
        if not self.ok:
            return False
        ks = self.x.XStringToKeysym(keysym_name.encode())
        if not ks:
            return False
        kc = self.x.XKeysymToKeycode(self.dpy, ks)
        targets = [win] + self.children(win)
        stamp = int(time.time() * 1000) & 0xFFFFFFFF   # app costuma olhar time
        for etype, mask in ((self.KEYPRESS, self.PRESS_MASK),
                            (self.KEYRELEASE, self.RELEASE_MASK)):
            for target in targets:
                ev = XEvent()
                ev.type = etype
                k = ev.xkey
                k.type, k.display = etype, self.dpy
                k.window, k.root, k.subwindow = target, self.root, 0
                k.time = stamp
                k.x = k.y = k.x_root = k.y_root = 1
                k.state, k.keycode, k.same_screen = 0, kc, 1
                self.x.XSendEvent(self.dpy, target, 0, mask, ctypes.byref(ev))
            self.x.XFlush(self.dpy)
            stamp += int(hold * 1000)
            if etype == self.KEYPRESS:
                time.sleep(hold)
        return True


CODE_NAMES = {}
for _n, _c in KEYS.items():
    CODE_NAMES.setdefault(_c, _n)


def key_label(code, event=None):
    """Nome amigável para um código evdev capturado do teclado."""
    if code in CODE_NAMES:
        return CODE_NAMES[code]
    if event is not None:
        txt = QKeySequence(event.key()).toString()
        if txt:
            return txt
    return f"key {code}"


class KeyCatcher(QPushButton):
    """Clique e aperte a tecla. Melhor que caçar numa lista de 122 itens, e
    pega qualquer tecla do teclado, não só as que estão na lista: o Qt entrega
    o scancode nativo, que é o código evdev + 8 (conferido em letra, função,
    modificador, numérico e seta)."""
    changed = Signal()

    def __init__(self, code=57, name="Space"):
        super().__init__()
        self.code, self.name = code, name
        self._arming = False
        self.clicked.connect(self._arm)
        self.setToolTip(_("Click, then press the key you want"))
        self._refresh()

    def _refresh(self):
        self.setText(_("Press a key…") if self._arming else self.name)

    def _arm(self):
        self._arming = True
        self._refresh()
        self.setFocus()
        self.grabKeyboard()

    def _disarm(self):
        self._arming = False
        self.releaseKeyboard()
        self._refresh()

    def set_key(self, code, name=None):
        self.code = code
        self.name = name or key_label(code)
        self._arming = False
        self._refresh()
        self.changed.emit()

    def keyPressEvent(self, event):
        if not self._arming:
            return          # sem isso Space e Enter re-disparariam o botão
        code = event.nativeScanCode() - 8
        if event.key() == Qt.Key_Escape:
            self._disarm()
            return
        self.releaseKeyboard()
        self.set_key(code, key_label(code, event))


# nomes de keysym do X para as teclas que a macro oferece
X_KEYSYM = {"Space": "space", "Enter": "Return", "Tab": "Tab", "Esc": "Escape",
            "Backspace": "BackSpace", "Delete": "Delete", "Insert": "Insert",
            "Home": "Home", "End": "End", "Page Up": "Prior",
            "Page Down": "Next", "Up": "Up", "Down": "Down", "Left": "Left",
            "Right": "Right", "Left Shift": "Shift_L",
            "Right Shift": "Shift_R", "Left Ctrl": "Control_L",
            "Right Ctrl": "Control_R", "Left Alt": "Alt_L",
            "Right Alt": "Alt_R", "Left Super": "Super_L",
            "Right Super": "Super_R"}


def x_keysym(name):
    """Nome da tecla na UI -> keysym do X."""
    if name in X_KEYSYM:
        return X_KEYSYM[name]
    if len(name) == 1:
        return name.lower() if name.isalpha() else name
    if name.startswith("F") and name[1:].isdigit():
        return name
    if name.startswith("Numpad "):
        tail = name.split(" ", 1)[1]
        return {"+": "KP_Add", "-": "KP_Subtract", "*": "KP_Multiply",
                "/": "KP_Divide", ".": "KP_Decimal",
                "Enter": "KP_Enter"}.get(tail, "KP_" + tail)
    return None


class KWinBridge(QObject):
    """Conversa com o KWin via scripting: só ele enxerga as janelas no Wayland.

    O script JS roda dentro do compositor e devolve o resultado chamando um
    serviço D-Bus nosso — scripts do KWin podem fazer callDBus, mas não podem
    escrever em arquivo nem receber chamadas.
    """
    IFACE = "org.wayclick.Bridge"

    def __init__(self):
        super().__init__()
        self.ok = False
        self.error = None
        self._reply = None
        try:
            from PySide6.QtDBus import QDBusConnection
        except ImportError:
            self.error = "PySide6.QtDBus not available"
            return
        self.service = f"org.wayclick.Bridge{os.getpid()}"
        self.path = "/bridge"
        bus = QDBusConnection.sessionBus()
        if not (bus.registerService(self.service)
                and bus.registerObject(self.path, self.IFACE, self,
                                       QDBusConnection.ExportAllSlots)):
            self.error = "could not register the D-Bus service"
            return
        self.script = os.path.join(
            os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir(),
            f"wayclick-kwin-{os.getpid()}.js")
        self.ok = True

    @Slot(str)
    def reply(self, payload):
        self._reply = payload

    def _run(self, js, timeout=2.0):
        if not self.ok:
            return None
        self._reply = None
        try:
            with open(self.script, "w") as fh:
                fh.write(js % {"svc": self.service, "path": self.path,
                               "iface": self.IFACE, "target": self._target})
        except OSError:
            return None
        run = lambda *a: subprocess.run(a, capture_output=True, text=True)
        run("gdbus", "call", "--session", "-d", "org.kde.KWin", "-o",
            "/Scripting", "-m", "org.kde.kwin.Scripting.unloadScript", "wayclick")
        r = run("gdbus", "call", "--session", "-d", "org.kde.KWin", "-o",
                "/Scripting", "-m", "org.kde.kwin.Scripting.loadScript",
                self.script, "wayclick")
        sid = "".join(c for c in r.stdout if c.isdigit())
        if not sid:
            self.error = "KWin refused the script (is this KWin?)"
            return None
        run("gdbus", "call", "--session", "-d", "org.kde.KWin", "-o",
            f"/Scripting/Script{sid}", "-m", "org.kde.kwin.Script.run")
        deadline = time.perf_counter() + timeout
        app = QApplication.instance()
        while self._reply is None and time.perf_counter() < deadline:
            app.processEvents()
            time.sleep(0.004)
        return self._reply

    _target = ""

    def windows(self):
        self._target = ""
        raw = self._run(KWIN_LIST_JS)
        try:
            return json.loads(raw) if raw else []
        except ValueError:
            return []

    def activate(self, internal_id):
        """Ativa a janela e devolve quem estava ativa antes."""
        self._target = internal_id
        return self._run(KWIN_ACTIVATE_JS)

    def cleanup(self):
        try:
            os.remove(self.script)
        except (OSError, AttributeError):
            pass


_ICON_CACHE = {}


def app_icon(resource_class):
    """Ícone do app a partir da classe da janela, via arquivos .desktop."""
    if resource_class in _ICON_CACHE:
        return _ICON_CACHE[resource_class]
    name = None
    dirs = [os.path.join(d, "applications") for d in
            [os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")]
            + (os.environ.get("XDG_DATA_DIRS") or "/usr/share").split(":")]
    wanted = resource_class.lower()
    for d in dirs:
        for cand in (f"{resource_class}.desktop", f"{wanted}.desktop"):
            path = os.path.join(d, cand)
            if os.path.exists(path):
                name = _desktop_icon(path)
                break
        if name:
            break
    if not name:                       # último recurso: casar por StartupWMClass
        for d in dirs:
            for f in glob.glob(os.path.join(d, "*.desktop")):
                if _desktop_wmclass(f) == wanted:
                    name = _desktop_icon(f)
                    break
            if name:
                break
    icon = QIcon.fromTheme(name or resource_class)
    if icon.isNull():
        icon = QIcon.fromTheme("application-x-executable")
    _ICON_CACHE[resource_class] = icon
    return icon


def _desktop_field(path, key):
    try:
        with open(path, errors="replace") as fh:
            for line in fh:
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return None


def _desktop_icon(path):
    return _desktop_field(path, "Icon")


def _desktop_wmclass(path):
    v = _desktop_field(path, "StartupWMClass")
    return v.lower() if v else None


# ------------------------------------------------------------- idioma ------
# Dicionário simples em vez de .ts/.qm: o app é um arquivo só, e assim quem for
# traduzir para outro idioma só precisa copiar um bloco aqui.
TRANSLATIONS = {
    "pt_BR": {
        "WayClick": "WayClick",
        "File": "Arquivo", "Settings": "Configurações", "Help": "Ajuda",
        "Theme": "Tema", "Language": "Idioma",
        "Tray icon": "Ícone da bandeja", "Shape": "Formato", "Color": "Cor",
        "Cursor": "Cursor", "Mouse": "Mouse", "Dot": "Ponto", "Ring": "Anel",
        "Match state": "Conforme o estado", "Green": "Verde", "Blue": "Azul",
        "Purple": "Roxo", "Orange": "Laranja", "Red": "Vermelho",
        "Teal": "Verde-azulado", "Grey": "Cinza",
        "System": "Sistema", "Dark": "Escuro", "Light": "Claro",
        "Start": "Iniciar", "Stop": "Parar",
        "Hide to tray": "Esconder na bandeja",
        "Show window": "Mostrar janela", "Hide window": "Esconder janela",
        "Quit": "Sair", "About": "Sobre",
        "Project on GitHub": "Projeto no GitHub",
        "Press a key…": "Aperte uma tecla…",
        "Click, then press the key you want":
            "Clique e depois aperte a tecla que quiser",
        "Send key to a window": "Mandar tecla para uma janela",
        "Window:": "Janela:", "Every:": "A cada:",
        "no window found": "nenhuma janela encontrada",
        "Targeted key unavailable: {msg}": "Tecla direcionada indisponível: {msg}",
        "Lost the target window.": "Perdi a janela alvo.",
        "Pick a window first (↻ to rescan).":
            "Escolha uma janela primeiro (↻ para reprocurar).",
        "That key has no X11 equivalent.": "Essa tecla não tem equivalente no X11.",
        "⚠ Pure Wayland: reachable only by stealing focus for an instant. "
        "Reopen this program in X11 mode and it takes the key directly, even "
        "minimized — SDL_VIDEODRIVER=x11, GDK_BACKEND=x11 or "
        "QT_QPA_PLATFORM=xcb.":
            "⚠ Wayland puro: só dá para alcançar roubando o foco por um "
            "instante. Reabra este programa em modo X11 e ele recebe a tecla "
            "direto, mesmo minimizado — SDL_VIDEODRIVER=x11, GDK_BACKEND=x11 "
            "ou QT_QPA_PLATFORM=xcb.",
        "Nothing to run: enable Click, Keyboard macro or Send key to a window.":
            "Nada para executar: habilite Clique, Macro de teclado ou Mandar "
            "tecla para uma janela.",
        "Click": "Clique", "Keyboard": "Teclado", "Window": "Janela",
        "Trigger": "Acionamento",
        "Enable clicking": "Habilitar o clique",
        "Enable the keyboard macro": "Habilitar a macro de teclado",
        "Enable sending the key": "Habilitar o envio da tecla",
        "1000 ms is 1 click/s, 0.1 ms is 10,000. Lower is faster.":
            "1000 ms é 1 clique/s, 0,1 ms é 10.000. Menor é mais rápido.",
        "Click the key field and press the key you want.":
            "Clique no campo da tecla e aperte a tecla que quiser.",
        "Mouse:": "Mouse:", "Interval:": "Intervalo:", "Button:": "Botão:",
        "Key:": "Tecla:", "Action:": "Ação:", "Mode:": "Modo:",
        "Start delay:": "Atraso ao iniciar:",
        "Auto-stop after:": "Parar sozinho após:",
        "Global hotkey:": "Atalho global:",
        "Left": "Esquerdo", "Right": "Direito", "Middle": "Meio",
        "Repeat": "Repetir", "Hold": "Segurar",
        "Hotkey toggles": "Atalho liga e desliga",
        "Clicks while hotkey is held": "Age enquanto o atalho é segurado",
        "Clicks while mouse button is held":
            "Age enquanto o botão do mouse é segurado",
        "never": "nunca", "Rescan mice": "Reprocurar mouses",
        "Sound feedback on hotkey": "Som ao acionar o atalho",
        "Start with system": "Iniciar com o sistema",
        "Anti-AFK: nudge the cursor every":
            "Anti-AFK: mexer o cursor a cada",
        "Stopped": "Parado", "Starting in {n}s…": "Começa em {n}s…",
        "Stopped (time is up)": "Parado (tempo esgotado)",
        "RUNNING": "RODANDO", "ARMED — hold {btn} mouse button":
            "ARMADO — segure o botão {btn} do mouse",
        "held": "segurada", "nothing enabled": "nada habilitado",
        "stops in {n}s": "para em {n}s", "anti-AFK": "anti-AFK",
        "clicks/s": "cliques/s",
        "Move the cursor off this window before starting — otherwise it clicks "
        "itself. That is what the {d}s start delay is for. {hk} toggles; Esc stops.":
            "Tire o cursor desta janela antes de iniciar — senão ele clica em si "
            "mesmo. É para isso que serve o atraso de {d}s. {hk} liga e desliga; "
            "Esc para.",
        "Hold mode: while armed, your {btn} mouse button is captured and turned "
        "into the click stream — hold it to autoclick, release to stop. "
        "{hk} arms/disarms; Esc disarms.":
            "Modo segurar: enquanto armado, o botão {btn} do mouse é capturado e "
            "vira o fluxo de cliques — segure para clicar, solte para parar. "
            "{hk} arma e desarma; Esc desarma.",
        "Global hotkey OFF: you are in the 'input' group but this session "
        "started before that, so it has no access yet. Log out and back in, "
        "or run:  sg input -c '{cmd}'":
            "Atalho global DESLIGADO: você está no grupo 'input', mas esta "
            "sessão começou antes disso e ainda não tem acesso. Saia e entre de "
            "novo, ou rode:  sg input -c '{cmd}'",
        "Global hotkey OFF (no access to /dev/input). Run this, then log out "
        "and back in:\nsudo usermod -aG input $USER\nUntil then the hotkey "
        "only works with this window focused.":
            "Atalho global DESLIGADO (sem acesso a /dev/input). Rode isto e "
            "depois saia e entre de novo:\nsudo usermod -aG input $USER\nAté lá "
            "o atalho só funciona com esta janela em foco.",
        "no mouse detected": "nenhum mouse detectado",
        "Hold mode unavailable: {msg}": "Modo segurar indisponível: {msg}",
        "no mouse found": "nenhum mouse encontrado",
        "no access to /dev/input": "sem acesso a /dev/input",
        "could not grab the mouse": "não foi possível capturar o mouse",
        "Autoclicker for Wayland using /dev/uinput.":
            "Autoclicker para Wayland usando /dev/uinput.",
    },
}
LANGS = {"English": "en_US", "Português (BR)": "pt_BR"}
LANG = "en_US"


def _(text, **fmt):
    out = TRANSLATIONS.get(LANG, {}).get(text, text)
    return out.format(**fmt) if fmt else out


def default_language():
    loc = (os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES")
           or os.environ.get("LANG") or "")
    return "pt_BR" if loc.lower().startswith("pt") else "en_US"


# --------------------------------------------------------------- tema ------
# As cores saem dos esquemas do próprio KDE (/usr/share/color-schemes/*.colors),
# então "Breeze Dark" aqui é exatamente o Breeze Dark do sistema, e não um
# escuro inventado. Quem não tiver os arquivos cai nos dois embutidos.
SCHEME_DIRS = ["/usr/share/color-schemes",
               os.path.join(os.environ.get("XDG_DATA_HOME")
                            or os.path.expanduser("~/.local/share"),
                            "color-schemes")]

# grupo do .colors -> (chave, papel do QPalette)
SCHEME_MAP = [
    ("Colors:Window", "BackgroundNormal", "Window"),
    ("Colors:Window", "ForegroundNormal", "WindowText"),
    ("Colors:View", "BackgroundNormal", "Base"),
    ("Colors:View", "BackgroundAlternate", "AlternateBase"),
    ("Colors:View", "ForegroundNormal", "Text"),
    ("Colors:View", "ForegroundInactive", "PlaceholderText"),
    ("Colors:View", "ForegroundLink", "Link"),
    ("Colors:View", "ForegroundNegative", "BrightText"),
    ("Colors:Button", "BackgroundNormal", "Button"),
    ("Colors:Button", "ForegroundNormal", "ButtonText"),
    ("Colors:Selection", "BackgroundNormal", "Highlight"),
    ("Colors:Selection", "ForegroundNormal", "HighlightedText"),
    ("Colors:Tooltip", "BackgroundNormal", "ToolTipBase"),
    ("Colors:Tooltip", "ForegroundNormal", "ToolTipText"),
]
DISABLED_MAP = [("Colors:Window", "ForegroundInactive", "WindowText"),
                ("Colors:View", "ForegroundInactive", "Text"),
                ("Colors:Button", "ForegroundInactive", "ButtonText")]

BUILTIN_SCHEMES = {
    "Dark": {"Window": "#2a2e32", "WindowText": "#fcfcfc", "Base": "#1f2225",
             "AlternateBase": "#2a2e32", "Text": "#fcfcfc", "Button": "#31363b",
             "ButtonText": "#fcfcfc", "Highlight": "#3daee9",
             "HighlightedText": "#fcfcfc", "Link": "#1d99f3",
             "BrightText": "#da4453", "PlaceholderText": "#a1a9b1",
             "ToolTipBase": "#31363b", "ToolTipText": "#fcfcfc",
             "_disabled": {"WindowText": "#7f8c8d", "Text": "#7f8c8d",
                           "ButtonText": "#7f8c8d"}},
    "Light": {"Window": "#eff0f1", "WindowText": "#232629", "Base": "#fcfcfc",
              "AlternateBase": "#eff0f1", "Text": "#232629",
              "Button": "#eff0f1", "ButtonText": "#232629",
              "Highlight": "#3daee9", "HighlightedText": "#fcfcfc",
              "Link": "#2980b9", "BrightText": "#da4453",
              "PlaceholderText": "#7f8c8d", "ToolTipBase": "#f7f7f7",
              "ToolTipText": "#232629",
              "_disabled": {"WindowText": "#a8a8a8", "Text": "#a8a8a8",
                            "ButtonText": "#a8a8a8"}},
}
_SCHEMES = None
_SYS = {}


def _read_scheme(path):
    """Lê um .colors do KDE (formato INI) para o formato de paleta daqui."""
    groups, cur = {}, None
    try:
        with open(path, errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("[") and line.endswith("]"):
                    cur = line[1:-1]
                    groups[cur] = {}
                elif cur and "=" in line:
                    k, v = line.split("=", 1)
                    groups[cur][k.strip()] = v.strip()
    except OSError:
        return None
    rgb = lambda v: "#%02x%02x%02x" % tuple(int(x) for x in v.split(",")[:3])
    spec, disabled = {}, {}
    try:
        for group, key, role in SCHEME_MAP:
            val = groups.get(group, {}).get(key)
            if val:
                spec[role] = rgb(val)
        for group, key, role in DISABLED_MAP:
            val = groups.get(group, {}).get(key)
            if val:
                disabled[role] = rgb(val)
    except (ValueError, TypeError):
        return None
    if "Window" not in spec or "WindowText" not in spec:
        return None
    spec["_disabled"] = disabled
    name = groups.get("General", {}).get("Name") or \
        os.path.basename(path).replace(".colors", "")
    return name, spec


def color_schemes():
    """{nome visível: paleta}, dos esquemas instalados mais os embutidos."""
    global _SCHEMES
    if _SCHEMES is not None:
        return _SCHEMES
    found = {}
    for d in SCHEME_DIRS:
        for path in sorted(glob.glob(os.path.join(d, "*.colors"))):
            got = _read_scheme(path)
            if got:
                found[got[0]] = got[1]
    for name, spec in BUILTIN_SCHEMES.items():
        found.setdefault(name, spec)
    _SCHEMES = found
    return found


def _palette(spec):
    from PySide6.QtGui import QPalette
    pal = QPalette()
    for role, color in spec.items():
        if role != "_disabled":
            pal.setColor(getattr(QPalette, role), QColor(color))
    for role, color in spec.get("_disabled", {}).items():
        pal.setColor(QPalette.Disabled, getattr(QPalette, role), QColor(color))
    return pal


def apply_theme(name):
    """Fusion + paleta: o estilo nativo ignora paleta, então forçar cor exige
    trocar de estilo junto. 'System' devolve estilo e paleta originais."""
    app = QApplication.instance()
    if not app:
        return
    _SYS.setdefault("palette", QApplication.palette())
    _SYS.setdefault("style", app.style().objectName())
    spec = color_schemes().get(name)
    if spec:
        app.setStyle("Fusion")
        app.setPalette(_palette(spec))
    else:
        app.setStyle(_SYS["style"])
        app.setPalette(_SYS["palette"])
    for w in app.topLevelWidgets():          # sem isto o já desenhado não muda
        w.setPalette(app.palette())
        for child in w.findChildren(QWidget):
            child.setPalette(app.palette())


# ---------------------------------------------------------- ícone/tray ------
STATE_COLORS = {"run": "#27ae60", "wait": "#f39c12",
                "armed": "#3daee9", "": "#9aa3ab"}
ARROW = [(20, 8), (20, 47), (29, 39), (35, 54), (43, 50), (37, 35), (48, 34)]
INK = "#12161a"


def _draw_arrow(p, color, scale):
    from PySide6.QtGui import QPainterPath
    path = QPainterPath()
    pts = [QPointF(x * scale, y * scale) for x, y in ARROW]
    path.moveTo(pts[0])
    for q in pts[1:]:
        path.lineTo(q)
    path.closeSubpath()
    pen = QPen(QColor(INK), 5 * scale)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(QColor(color))
    p.drawPath(path)


def wayclick_icon(state="", size=64):
    """Ícone do app: cursor com ondas de clique, numa placa arredondada."""
    px = QPixmap(size, size)
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 64.0
    grad = QLinearGradient(0, 0, 0, size)
    grad.setColorAt(0, QColor("#3a4650"))
    grad.setColorAt(1, QColor("#222a31"))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(grad))
    p.drawRoundedRect(QRectF(2 * s, 2 * s, 60 * s, 60 * s), 14 * s, 14 * s)
    col = QColor(STATE_COLORS.get(state, STATE_COLORS[""]))
    pen = QPen(col, 4 * s)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    for i, r in enumerate((13, 19)):
        p.setOpacity(0.8 - i * 0.35)
        p.drawArc(QRectF((24 - r) * s, (20 - r) * s, 2 * r * s, 2 * r * s),
                  -20 * 16, 100 * 16)
    p.setOpacity(1.0)
    _draw_arrow(p, "#f7f9fa" if not state else col, s)
    p.end()
    return QIcon(px)


# Estilos da bandeja. Cada um desenha num quadrado 64x64, escalado por `s`.
def _tray_cursor(p, col, s):
    _draw_arrow(p, col, s)


def _tray_mouse(p, col, s):
    p.setBrush(QColor(col))
    p.setPen(QPen(QColor(INK), 5 * s))
    p.drawRoundedRect(QRectF(16 * s, 6 * s, 32 * s, 52 * s), 16 * s, 18 * s)
    p.setPen(QPen(QColor(INK), 4 * s))
    p.drawLine(18 * s, 27 * s, 46 * s, 27 * s)
    p.setBrush(QColor(INK))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(29 * s, 12 * s, 6 * s, 13 * s), 3 * s, 3 * s)


def _tray_dot(p, col, s):
    p.setPen(QPen(QColor(INK), 5 * s))
    p.setBrush(QColor(col))
    p.drawEllipse(QRectF(12 * s, 12 * s, 40 * s, 40 * s))


def _tray_ring(p, col, s):
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QColor(INK), 12 * s))
    p.drawEllipse(QRectF(13 * s, 13 * s, 38 * s, 38 * s))
    p.setPen(QPen(QColor(col), 8 * s))
    p.drawEllipse(QRectF(13 * s, 13 * s, 38 * s, 38 * s))


TRAY_STYLES = {"Cursor": _tray_cursor, "Mouse": _tray_mouse,
               "Dot": _tray_dot, "Ring": _tray_ring}
TRAY_COLORS = {"Match state": None, "Green": "#27ae60", "Blue": "#3daee9",
               "Purple": "#9b59b6", "Orange": "#f39c12", "Red": "#e74c3c",
               "Teal": "#1abc9c", "Grey": "#9aa3ab"}
BURST_FRAMES = 6


def tray_icon(state="", phase=0, style="Cursor", color="Match state",
              burst=None, size=64):
    """Bandeja: sem placa, para ficar legível a 22 px como os outros ícones do
    painel. A onda muda de opacidade por fase enquanto roda, e `burst` toca um
    anel que cresce e some — o retorno visual de "acabei de ligar"."""
    px = QPixmap(size, size)
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 64.0
    fixed = TRAY_COLORS.get(color)
    col = QColor(fixed or STATE_COLORS.get(state, STATE_COLORS[""]))
    if burst is not None and burst < BURST_FRAMES:
        t = burst / (BURST_FRAMES - 1.0)
        r = (10 + 22 * t) * s
        p.setPen(QPen(col, max(1.0, 6 * (1 - t) * s)))
        p.setBrush(Qt.NoBrush)
        p.setOpacity(max(0.0, 0.85 * (1 - t)))
        p.drawEllipse(QRectF(32 * s - r, 32 * s - r, 2 * r, 2 * r))
        p.setOpacity(1.0)
    elif state in ("run", "armed"):
        pen = QPen(col, 5 * s)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.setOpacity((0.95, 0.6, 0.28)[phase % 3])
        p.drawArc(QRectF(6 * s, 2 * s, 34 * s, 34 * s), -20 * 16, 100 * 16)
        p.setOpacity(1.0)
    TRAY_STYLES.get(style, _tray_cursor)(p, col.name(), s)
    p.end()
    return QIcon(px)


# ------------------------------------------------------------- autostart ----
XDG_CONFIG = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
AUTOSTART = os.path.join(XDG_CONFIG, "autostart", "wayclick.desktop")
DESKTOP_ENTRY = """[Desktop Entry]
Type=Application
Name=WayClick
Comment=Autoclicker for Wayland, using /dev/uinput
Exec={exec}
Icon=input-mouse
Terminal=false
StartupNotify=false
X-GNOME-Autostart-enabled=true
"""


def autostart_enabled():
    return os.path.exists(AUTOSTART)


def set_autostart(on):
    """Entrada XDG em ~/.config/autostart. Começa na bandeja: no login o app
    já herda o grupo 'input', então não precisa do desvio pelo `sg`."""
    if not on:
        try:
            os.remove(AUTOSTART)
        except FileNotFoundError:
            pass
        return True
    try:
        os.makedirs(os.path.dirname(AUTOSTART), exist_ok=True)
        cmd = shlex.join([sys.executable, os.path.abspath(__file__), "--tray"])
        with open(AUTOSTART, "w") as fh:
            fh.write(DESKTOP_ENTRY.format(exec=cmd))
        return True
    except OSError:
        return False


# ------------------------------------------------------------------ UI ------
MODES = {"Hotkey toggles": "toggle",
         "Clicks while hotkey is held": "hotkey_hold",
         "Clicks while mouse button is held": "mouse_hold"}
CFG = os.path.join(XDG_CONFIG, "wayclick.json")


class App(QWidget):
    def __init__(self):
        super().__init__()
        self._state = ""
        self._pulse = 0
        self._burst = None
        self.mouse = None
        self.keyboard = None
        self.clicker = None
        self.keymacro = None
        self.antiafk = None
        self.running = False

        cfg = {}
        # aproveita a config do nome antigo do projeto, se existir
        old = os.path.join(XDG_CONFIG, "autoclick-wayland.json")
        src = CFG if os.path.exists(CFG) else old
        if os.path.exists(src):
            try:
                with open(src) as fh:
                    cfg = json.load(fh)
            except Exception:
                pass

        global LANG
        LANG = cfg.get("language") or default_language()
        self.theme = cfg.get("theme", "System")
        self.tray_style = cfg.get("tray_style", "Cursor")
        self.tray_color = cfg.get("tray_color", "Match state")
        apply_theme(self.theme)
        self.setWindowTitle(_("WayClick"))

        self.interval = QDoubleSpinBox()
        self.interval.setRange(0.1, 1000.0)
        self.interval.setDecimals(1)
        self.interval.setSingleStep(1.0)
        self.interval.setSuffix(" ms")
        self.interval.setValue(cfg.get("interval_ms", 100.0))
        self.rate = QLabel()
        self.interval.valueChanged.connect(self._show_rate)
        self.btn_sel = QComboBox()
        for name, code in BTN.items():
            self.btn_sel.addItem(_(name), name)
        self.btn_sel.setCurrentIndex(
            max(0, self.btn_sel.findData(cfg.get("button", "Left"))))
        self.delay = QSpinBox(); self.delay.setRange(0, 10)
        self.delay.setValue(cfg.get("delay", 3)); self.delay.setSuffix(" s")
        self.duration = QSpinBox(); self.duration.setRange(0, 3600)
        self.duration.setValue(cfg.get("duration", 0))
        self.duration.setSuffix(" s")
        self.duration.setSpecialValueText(_("never"))
        self.dev = QComboBox()
        self.dev.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.dev.setMinimumContentsLength(22)
        self.refresh_btn = QPushButton("↻")
        self.refresh_btn.setFixedWidth(30)
        self.refresh_btn.setToolTip(_("Rescan mice"))
        self.refresh_btn.clicked.connect(lambda: self.refresh_mice(keep=True))
        dev_row = QHBoxLayout()
        dev_row.addWidget(self.dev, 1)
        dev_row.addWidget(self.refresh_btn)
        self._wanted_mouse = cfg.get("mouse_name")
        self.refresh_mice()
        self.dev.currentIndexChanged.connect(self._mouse_changed)

        self.hk = QComboBox(); self.hk.addItems(KEYNAMES.keys())
        self.hk.setCurrentText(cfg.get("hotkey", "F8"))
        self.mode = QComboBox()
        for label, key in MODES.items():
            self.mode.addItem(_(label), key)
        self.mode.setCurrentIndex(
            max(0, self.mode.findData(cfg.get("mode", "toggle"))))
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.sound = QCheckBox(_("Sound feedback on hotkey"))
        self.sound.setChecked(cfg.get("sound", True))
        self.autostart = QCheckBox(_("Start with system"))
        self.autostart.setChecked(autostart_enabled())
        self.autostart.toggled.connect(self._toggle_autostart)

        # --- macro de teclado ---
        self.key_sel = KeyCatcher(cfg.get("key_code", KEYS["Space"]),
                                  cfg.get("key", "Space"))
        self.key_interval = QDoubleSpinBox()
        self.key_interval.setRange(1.0, 10000.0)
        self.key_interval.setDecimals(0)
        self.key_interval.setSingleStep(50.0)
        self.key_interval.setSuffix(" ms")
        self.key_interval.setValue(cfg.get("key_interval_ms", 200.0))
        self.key_mode = QComboBox()
        for label in ("Repeat", "Hold"):
            self.key_mode.addItem(_(label), label)
        self.key_mode.setCurrentIndex(
            max(0, self.key_mode.findData(cfg.get("key_mode", "Repeat"))))
        self.key_mode.currentIndexChanged.connect(
            lambda: self.key_interval.setEnabled(
                self.key_mode.currentData() == "Repeat"))
        self.key_interval.setEnabled(self.key_mode.currentData() == "Repeat")

        self._labels = []            # (widget, texto-fonte) para retraduzir
        self._hints = []
        click_form = QFormLayout()
        self._row(click_form, "Mouse:", dev_row)
        self._row(click_form, "Interval:", self.interval)
        click_form.addRow("", self.rate)
        self._row(click_form, "Button:", self.btn_sel)
        self.click_box = QCheckBox(_("Enable clicking"))
        self.click_box.setChecked(cfg.get("click_enabled", True))

        key_form = QFormLayout()
        self._row(key_form, "Key:", self.key_sel)
        self._row(key_form, "Interval:", self.key_interval)
        self._row(key_form, "Action:", self.key_mode)
        self.key_box = QCheckBox(_("Enable the keyboard macro"))
        self.key_box.setChecked(cfg.get("key_enabled", False))
        self.key_box.toggled.connect(self._key_box_toggled)

        form = QFormLayout()
        self._row(form, "Mode:", self.mode)
        self._row(form, "Start delay:", self.delay)
        self._row(form, "Auto-stop after:", self.duration)
        self._row(form, "Global hotkey:", self.hk)
        trigger_form = form

        # --- injeção direcionada a uma janela (beta) ---
        self.win_sel = QComboBox()
        self.win_sel.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.win_sel.setMinimumContentsLength(22)
        self._scrollable(self.win_sel)
        self.win_refresh = QPushButton("↻")
        self.win_refresh.setFixedWidth(30)
        self.win_refresh.clicked.connect(self.refresh_windows)
        win_row = QHBoxLayout()
        win_row.addWidget(self.win_sel, 1)
        win_row.addWidget(self.win_refresh)
        self.win_key = KeyCatcher(cfg.get("target_key_code", KEYS["Space"]),
                                  cfg.get("target_key", "Space"))
        self.win_sel.currentIndexChanged.connect(self._update_win_warn)
        self.win_secs = QSpinBox(); self.win_secs.setRange(1, 3600)
        self.win_secs.setValue(cfg.get("target_seconds", 60))
        self.win_secs.setSuffix(" s")
        self.win_secs.valueChanged.connect(self._target_restart)

        self.win_warn = QLabel("")
        self.win_warn.setWordWrap(True)
        self.win_warn.setStyleSheet("color:#d04030;font-size:11px;")
        self.win_warn.setTextInteractionFlags(Qt.TextSelectableByMouse)

        target_form = QFormLayout()
        self._row(target_form, "Window:", win_row)
        self._row(target_form, "Key:", self.win_key)
        self._row(target_form, "Every:", self.win_secs)
        # o aviso fica fora do form: QLabel com quebra de linha dentro de
        # QFormLayout não calcula a altura e sai cortado
        self.target_box = QCheckBox(_("Enable sending the key"))
        self.target_box.setChecked(False)
        self.target_box.toggled.connect(self._target_toggled)

        # --- anti-AFK (independente do Start) ---
        self.afk = QCheckBox(_("Anti-AFK: nudge the cursor every"))
        self.afk.setChecked(False)
        self.afk_secs = QSpinBox(); self.afk_secs.setRange(1, 600)
        self.afk_secs.setValue(cfg.get("afk_seconds", 1)); self.afk_secs.setSuffix(" s")
        self.afk.toggled.connect(self._afk_toggled)
        self.afk_secs.valueChanged.connect(self._afk_restart)
        afk_row = QHBoxLayout()
        afk_row.addWidget(self.afk)
        afk_row.addWidget(self.afk_secs)
        afk_row.addStretch()

        self.status = QLabel(_("Stopped"))
        self.status.setAlignment(Qt.AlignCenter)
        self.btn = QPushButton(_("Start"))
        self.btn.setMinimumHeight(46)
        self.btn.clicked.connect(lambda: self.set_running(not self.running))

        self.warn = QLabel(""); self.warn.setWordWrap(True)
        self.warn.setStyleSheet("color:#c86000;font-size:11px;")
        self.warn.setTextInteractionFlags(Qt.TextSelectableByMouse)


        checks = QHBoxLayout()
        checks.addWidget(self.sound)
        checks.addWidget(self.autostart)
        checks.addStretch()

        # Abas em vez de tudo empilhado: com todos os grupos numa tela só a
        # janela passava de 900 px de altura e não cabia num 1366x768.
        self.tabs = QTabWidget()
        self.tab_pages = []
        self.tabs.addTab(self._page(self.click_box, click_form,
                                    _("1000 ms is 1 click/s, 0.1 ms is 10,000. "
                                      "Lower is faster.")), "")
        self.tabs.addTab(self._page(self.key_box, key_form,
                                    _("Click the key field and press the key "
                                      "you want.")), "")
        self.tabs.addTab(self._page(self.target_box, target_form, None,
                                    extra=self.win_warn), "")
        self.tabs.addTab(self._page(None, trigger_form, None,
                                    extra=[afk_row, checks]), "")
        self._tab_names = ["Click", "Keyboard", "Window", "Trigger"]
        self._sync_tabs()
        for w_ in (self.click_box, self.key_box, self.target_box):
            w_.toggled.connect(self._sync_tabs)

        outer = QVBoxLayout(self)
        outer.setMenuBar(self._build_menu())
        outer.addWidget(self.tabs, 1)
        outer.addWidget(self.status)
        outer.addWidget(self.btn)
        outer.addWidget(self.warn)
        self.resize(460, 470)
        self.setWindowIcon(wayclick_icon())
        self._show_rate()
        self._paint_status()

        # atalho global
        self.watcher = HotkeyWatcher(KEYNAMES[self.hk.currentText()])
        self.watcher.pressed.connect(self.on_press)
        self.watcher.released.connect(self.on_release)
        self.watcher.start()
        self.hk.currentTextChanged.connect(self.on_hotkey_change)

        # fallback só com a janela em foco; desligado quando o global funciona,
        # senão a mesma tecla dispararia duas vezes e se anularia
        self.local_sc = QShortcut(QKeySequence(self.hk.currentText()), self)
        self.local_sc.setContext(Qt.ApplicationShortcut)
        self.local_sc.activated.connect(lambda: self.set_running(not self.running))
        self.local_sc.setEnabled(not self.watcher.ok)
        QShortcut(QKeySequence("Esc"), self).activated.connect(
            lambda: self.set_running(False))

        self.countdown = QTimer(self); self.countdown.setInterval(1000)
        self.countdown.timeout.connect(self._tick)
        self._left = 0
        self.autostop = QTimer(self); self.autostop.setInterval(1000)
        self.autostop.timeout.connect(self._tick_autostop)
        self._remain = 0

        self.beeper = Beeper()
        self.holder = None      # MouseHold, só no modo "mouse_hold"
        self.bridge = None      # KWinBridge, criado sob demanda
        self.xinject = None     # XInject, para janelas X11
        self.target_hits = 0
        self.target_ms = 0.0
        self.target_timer = QTimer(self)
        self.target_timer.timeout.connect(self._target_tick)
        self._build_tray()

        # cria o mouse virtual já no início: o UI_DEV_CREATE precisa de ~0,4 s
        # de settle e travaria a UI se fosse feito no clique de Start
        self.ensure_mouse()

        if not self.watcher.ok:
            member = input_access()[1]
            if member:
                self.warn.setText(_(
                    "Global hotkey OFF: you are in the 'input' group but this "
                    "session started before that, so it has no access yet. "
                    "Log out and back in, or run:  sg input -c '{cmd}'",
                    cmd=f"python3 {os.path.abspath(__file__)}"))
            else:
                self.warn.setText(_(
                    "Global hotkey OFF (no access to /dev/input). Run this, "
                    "then log out and back in:\nsudo usermod -aG input $USER\n"
                    "Until then the hotkey only works with this window "
                    "focused."))
        self._hint()

    # ---------------------------------------------------------- helpers --
    def _rate_str(self):
        cps = 1000.0 / self.interval.value()
        n = f"{cps:,.0f}" if cps >= 10 else f"{cps:.1f}"
        return f"{n} " + _("clicks/s")

    def _show_rate(self):
        self.rate.setText("= " + self._rate_str())
        self.rate.setStyleSheet("color:#7a7a7a;font-size:11px;")

    def _hint(self):
        if self.warn.text() and not getattr(self, "_hinted", False):
            return
        self._hinted = True
        hk, btn = self.hk.currentText(), self.btn_sel.currentText()
        if self.mode_key() == "mouse_hold":
            self.warn.setText(_(
                "Hold mode: while armed, your {btn} mouse button is captured "
                "and turned into the click stream — hold it to autoclick, "
                "release to stop. {hk} arms/disarms; Esc disarms.",
                btn=btn.lower(), hk=hk))
        else:
            self.warn.setText(_(
                "Move the cursor off this window before starting — otherwise "
                "it clicks itself. That is what the {d}s start delay is for. "
                "{hk} toggles; Esc stops.", d=self.delay.value(), hk=hk))

    def _page(self, enable, form, hint=None, extra=None):
        """Uma aba: caixa de habilitar (quando a aba é uma engine), o
        formulário, e o que mais vier. Desabilitar a caixa apaga os campos,
        como o QGroupBox marcável fazia antes."""
        page = QWidget()
        col = QVBoxLayout(page)
        if enable is not None:
            col.addWidget(enable)
        fields = QWidget()
        fields.setLayout(form)
        col.addWidget(fields)
        if enable is not None:
            enable.toggled.connect(fields.setEnabled)
            fields.setEnabled(enable.isChecked())
        if hint:
            lbl = QLabel(hint)
            lbl.setWordWrap(True)
            lbl.setEnabled(False)          # cinza pela paleta, não hardcoded
            col.addWidget(lbl)
            self._hints.append((lbl, hint))
        for item in (extra if isinstance(extra, list) else [extra] if extra else []):
            if isinstance(item, QWidget):
                col.addWidget(item)
            elif item is not None:
                col.addLayout(item)
        col.addStretch()
        self.tab_pages.append(page)
        return page

    def _sync_tabs(self):
        """Marca a aba cuja engine está ligada, para saber sem abrir."""
        on = [self.click_box.isChecked(), self.key_box.isChecked(),
              self.target_box.isChecked(), None]
        for i, name in enumerate(self._tab_names):
            mark = "● " if on[i] else ""
            self.tabs.setTabText(i, mark + _(name))

    @staticmethod
    def _scrollable(combo, visible=5):
        """Popup com no máximo `visible` itens e rolagem. Trocar a view por uma
        QListView faz o Qt usar o popup de item view, que respeita
        maxVisibleItems — via stylesheet (combobox-popup:0) também funciona, mas
        aí a combo perde o desenho nativo e fica branca em tema escuro."""
        combo.setView(QListView())
        combo.setMaxVisibleItems(visible)

    # -------------------------------------------------------- menu/i18n --
    def _row(self, form, text, widget):
        lbl = QLabel(_(text))
        self._labels.append((lbl, text))
        form.addRow(lbl, widget)
        return lbl

    def _build_menu(self):
        bar = QMenuBar()
        self.m_file = bar.addMenu(_("File"))
        self.act_run = self.m_file.addAction(_("Start"))
        self.act_run.triggered.connect(lambda: self.set_running(not self.running))
        self.act_hide = self.m_file.addAction(_("Hide to tray"))
        self.act_hide.triggered.connect(self.hide)
        self.m_file.addSeparator()
        self.act_quit = self.m_file.addAction(_("Quit"))
        self.act_quit.triggered.connect(QApplication.instance().quit)

        self.m_set = bar.addMenu(_("Settings"))
        self.m_theme = self.m_set.addMenu(_("Theme"))
        self.theme_acts = {}
        for name in ["System"] + sorted(color_schemes()):
            act = self.m_theme.addAction(_(name))
            act.setCheckable(True)
            act.triggered.connect(lambda _c=False, n=name: self._set_theme(n))
            self.theme_acts[name] = act
        self.m_tray = self.m_set.addMenu(_("Tray icon"))
        self.m_style = self.m_tray.addMenu(_("Shape"))
        self.style_acts = {}
        for name in TRAY_STYLES:
            act = self.m_style.addAction(_(name))
            act.setCheckable(True)
            act.setChecked(name == self.tray_style)
            act.triggered.connect(lambda _c=False, n=name: self._set_tray_style(n))
            self.style_acts[name] = act
        self.m_tcolor = self.m_tray.addMenu(_("Color"))
        self.color_acts = {}
        for name in TRAY_COLORS:
            act = self.m_tcolor.addAction(_(name))
            act.setCheckable(True)
            act.setChecked(name == self.tray_color)
            act.triggered.connect(lambda _c=False, n=name: self._set_tray_color(n))
            self.color_acts[name] = act

        self.m_lang = self.m_set.addMenu(_("Language"))
        self.lang_acts = {}
        for label, code in LANGS.items():
            act = self.m_lang.addAction(label)     # nome do idioma não traduz
            act.setCheckable(True)
            act.triggered.connect(lambda _c=False, x=code: self._set_language(x))
            self.lang_acts[code] = act
        self.m_set.addSeparator()
        self.act_sound = self.m_set.addAction(_("Sound feedback on hotkey"))
        self.act_sound.setCheckable(True)
        self.act_sound.toggled.connect(self.sound.setChecked)
        self.sound.toggled.connect(self.act_sound.setChecked)
        self.act_auto = self.m_set.addAction(_("Start with system"))
        self.act_auto.setCheckable(True)
        self.act_auto.toggled.connect(self.autostart.setChecked)
        self.autostart.toggled.connect(self.act_auto.setChecked)

        for n, act in self.theme_acts.items():
            act.setChecked(n == self.theme)
        for c, act in self.lang_acts.items():
            act.setChecked(c == LANG)
        self.act_sound.setChecked(self.sound.isChecked())
        self.act_auto.setChecked(self.autostart.isChecked())

        self.m_help = bar.addMenu(_("Help"))
        self.act_site = self.m_help.addAction(_("Project on GitHub"))
        self.act_site.triggered.connect(
            lambda: QDesktopServices.openUrl(QUrl(HOMEPAGE)))
        self.m_help.addSeparator()
        self.act_about = self.m_help.addAction(_("About"))
        self.act_about.triggered.connect(self._about)
        return bar

    def _set_theme(self, name):
        self.theme = name
        for n, act in self.theme_acts.items():
            act.setChecked(n == name)
        apply_theme(name)

    def _set_language(self, code):
        global LANG
        LANG = code
        for c, act in self.lang_acts.items():
            act.setChecked(c == code)
        self.retranslate()

    def _about(self):
        QMessageBox.about(
            self, _("About"),
            f"<b>WayClick {VERSION}</b><br>"
            f"{_('Autoclicker for Wayland using /dev/uinput.')}<br><br>"
            f'<a href="{HOMEPAGE}">{HOMEPAGE}</a>')

    @staticmethod
    def _retext(combo, labels):
        """Retraduz os itens sem mexer na seleção (o valor vive no userData)."""
        keep = combo.currentIndex()
        combo.blockSignals(True)
        for i, src in enumerate(labels):
            combo.setItemText(i, _(src))
        combo.setCurrentIndex(keep)
        combo.blockSignals(False)

    def retranslate(self):
        self.setWindowTitle(_("WayClick"))
        for lbl, src in self._labels:
            lbl.setText(_(src))
        self.click_box.setText(_("Enable clicking"))
        self.key_box.setText(_("Enable the keyboard macro"))
        self.target_box.setText(_("Enable sending the key"))
        self.afk.setText(_("Anti-AFK: nudge the cursor every"))
        for lbl, src in self._hints:
            lbl.setText(_(src))
        self._sync_tabs()
        self._update_win_warn()
        self.sound.setText(_("Sound feedback on hotkey"))
        self.autostart.setText(_("Start with system"))
        self.refresh_btn.setToolTip(_("Rescan mice"))
        self.duration.setSpecialValueText(_("never"))
        self._retext(self.mode, list(MODES.keys()))
        self._retext(self.btn_sel, list(BTN.keys()))
        self._retext(self.key_mode, ["Repeat", "Hold"])
        self.m_file.setTitle(_("File"))
        self.m_set.setTitle(_("Settings"))
        self.m_theme.setTitle(_("Theme"))
        self.m_tray.setTitle(_("Tray icon"))
        self.m_style.setTitle(_("Shape"))
        self.m_tcolor.setTitle(_("Color"))
        for name, act in self.style_acts.items():
            act.setText(_(name))
        for name, act in self.color_acts.items():
            act.setText(_(name))
        self.m_lang.setTitle(_("Language"))
        self.m_help.setTitle(_("Help"))
        self.act_hide.setText(_("Hide to tray"))
        self.act_quit.setText(_("Quit"))
        self.act_about.setText(_("About"))
        self.act_site.setText(_("Project on GitHub"))
        self.act_sound.setText(_("Sound feedback on hotkey"))
        self.act_auto.setText(_("Start with system"))
        for name, act in self.theme_acts.items():
            act.setText(_(name))
        self.btn.setText(_("Stop") if self.running else _("Start"))
        self.act_run.setText(_("Stop") if self.running else _("Start"))
        self.warn.setText("")
        self._hinted = False
        self._hint()
        self.refresh_mice(keep=True)
        self._show_rate()
        self._show_status()
        self._sync_tray()

    # ------------------------------------------------------------- tray --
    def _build_tray(self):
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(self._tray_pixmap(), self)
        menu = QMenu()
        self.act_toggle = QAction(_("Start"), self)
        self.act_toggle.triggered.connect(
            lambda: self.set_running(not self.running))
        self.act_window = QAction(_("Hide window"), self)
        self.act_window.triggered.connect(self._toggle_window)
        quit_act = QAction(_("Quit"), self)
        quit_act.triggered.connect(QApplication.instance().quit)
        menu.addAction(self.act_toggle)
        menu.addSeparator()
        menu.addAction(self.act_window)
        menu.addAction(quit_act)
        # a janela fechada pelo X só esconde e não avisa ninguém; atualizar os
        # rótulos na abertura do menu evita ter que sobrescrever closeEvent
        menu.aboutToShow.connect(self._sync_tray)
        self.tray.setContextMenu(menu)
        self.menu = menu
        self.tray.activated.connect(self._tray_activated)
        self.pulse_timer = QTimer(self)
        self.pulse_timer.setInterval(550)
        self.pulse_timer.timeout.connect(self._pulse_tick)
        self.burst_timer = QTimer(self)
        self.burst_timer.setInterval(70)
        self.burst_timer.timeout.connect(self._burst_tick)
        self.tray.show()
        self._sync_tray()
        # com a bandeja ativa, fechar a janela só esconde; sair é pelo menu
        QApplication.instance().setQuitOnLastWindowClosed(False)

    def _tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._toggle_window()

    def _toggle_window(self):
        if self.isVisible():
            self.hide()
        else:
            self.showNormal()
            self.raise_()
            self.activateWindow()
        self._sync_tray()

    def _tray_pixmap(self):
        return tray_icon(getattr(self, "_state", ""), self._pulse,
                         self.tray_style, self.tray_color, self._burst)

    def _pulse_tick(self):
        self._pulse = (self._pulse + 1) % 3
        self.tray.setIcon(self._tray_pixmap())

    def _burst_tick(self):
        """Anel que cresce e some ao ligar ou desligar: feedback de que o
        atalho pegou, mesmo com a janela escondida."""
        self._burst += 1
        if self._burst >= BURST_FRAMES:
            self._burst = None
            self.burst_timer.stop()
        if self.tray:
            self.tray.setIcon(self._tray_pixmap())

    def _burst_start(self):
        if not self.tray:
            return
        self._burst = 0
        self.burst_timer.start()
        self.tray.setIcon(self._tray_pixmap())

    def _set_tray_style(self, name):
        self.tray_style = name
        for n, act in self.style_acts.items():
            act.setChecked(n == name)
        self._sync_tray()

    def _set_tray_color(self, name):
        self.tray_color = name
        for n, act in self.color_acts.items():
            act.setChecked(n == name)
        self._sync_tray()

    def _sync_tray(self):
        if not getattr(self, "tray", None):   # _paint_status roda antes da tray
            return
        state = getattr(self, "_state", "")
        if state in ("run", "armed"):         # anima só enquanto trabalha
            if not self.pulse_timer.isActive():
                self.pulse_timer.start()
        else:
            self.pulse_timer.stop()
            self._pulse = 0
        label = {"run": _("RUNNING").capitalize(), "wait": _("Starting in {n}s…", n=""),
                 "armed": _("ARMED — hold {btn} mouse button",
                            btn=self.btn_sel.currentText().lower())
                 }.get(state, _("Stopped"))
        self.tray.setIcon(self._tray_pixmap())
        self.tray.setToolTip(f"WayClick — {label}")
        self.act_toggle.setText(_("Stop") if self.running else _("Start"))
        self.act_window.setText(_("Hide window") if self.isVisible()
                                else _("Show window"))

    def _toggle_autostart(self, on):
        if not set_autostart(on):
            self.warn.setText("Could not write " + AUTOSTART)

    def mode_key(self):
        return self.mode.currentData()

    def _mode_changed(self, _text):
        self.set_running(False)
        self.warn.setText("")
        self._hinted = False
        self._hint()

    def _beep(self, which):
        if self.sound.isChecked():
            self.beeper.play(which)

    def _paint_status(self):
        color = STATE_COLORS.get(getattr(self, "_state", ""), STATE_COLORS[""])
        self.status.setStyleSheet(
            f"font-size:17px;font-weight:bold;color:{color};padding:10px;")
        self._sync_tray()

    # ------------------------------------------------------------ mouses --
    def refresh_mice(self, keep=False):
        """Lista todos os mouses legíveis. Qualquer marca/quantidade serve."""
        want = self.selected_mouse()[1] if keep else self._wanted_mouse
        self.dev.blockSignals(True)
        self.dev.clear()
        mice = list_mice()
        for path, name in mice:
            self.dev.addItem(f"{name}  ({os.path.basename(path)})", (path, name))
        if not mice:
            self.dev.addItem(_("no mouse detected"), None)
        idx = next((i for i, (_, n) in enumerate(mice) if n == want), 0)
        self.dev.setCurrentIndex(idx)
        self.dev.blockSignals(False)
        self.dev.setEnabled(bool(mice))
        return mice

    def selected_mouse(self):
        data = self.dev.currentData()
        return data if data else (None, None)

    def _mouse_changed(self, _idx):
        """Trocar de mouse recria o device virtual: a identidade clonada tem que
        ser a do mouse que vai ser capturado, senão a sensibilidade não bate."""
        self.set_running(False)
        self._wanted_mouse = self.selected_mouse()[1]
        if self.antiafk:            # a thread guarda o fd antigo
            self.antiafk.stop()
            self.antiafk = None
        if self.mouse:
            self.mouse.close()
            self.mouse = None
        self.ensure_mouse()
        self._afk_restart()

    def ensure_mouse(self):
        if self.mouse is not None:
            return True
        # clona o mouse escolhido para herdar velocidade/perfil de aceleração
        # que o usuário configurou; sem mouse acessível, cai no genérico
        path = self.selected_mouse()[0]
        clone, _ = open_devices(is_mouse_any if path else is_mouse,
                                [path] if path else None)
        try:
            self.mouse = VirtualMouse(clone[0].fileno() if clone else None)
        except (PermissionError, OSError) as e:
            self.warn.setText(f"/dev/uinput failed: {e}\n"
                              "sudo usermod -aG input $USER  (then re-login)")
            return False
        finally:
            for f in clone:
                f.close()
        return True

    # ------------------------------------------------------------ estado --
    def set_running(self, on):
        """running = ligado/armado. No modo "segurar", clicar de fato só
        acontece com o botão do mouse pressionado."""
        if on == self.running:
            return
        if on:
            if not self.ensure_mouse():
                return
            if not (self.click_box.isChecked() or self.key_box.isChecked()
                    or self.target_box.isChecked()):
                self.warn.setText(_("Nothing to run: enable Click, Keyboard "
                                    "macro or Send key to a window."))
                return
            self.running = True
            self.btn.setText(_("Stop"))
            self.act_run.setText(_("Stop"))
            self._burst_start()
            self._beep("on")
            self._left = self.delay.value()
            if self._left:
                self._state = "wait"
                self.status.setText(_("Starting in {n}s…", n=self._left))
                self.countdown.start()
            else:
                self._engage()
        else:
            self.running = False
            self.countdown.stop()
            self.autostop.stop()
            self._stop_clicker()
            if self.holder:
                self.holder.stop()
                self.holder = None
            self._state = ""
            self._show_status()
            self.btn.setText(_("Start"))
            self.act_run.setText(_("Start"))
            self._burst_start()
            self._beep("off")
        self._paint_status()

    def _tick(self):
        self._left -= 1
        if self._left > 0:
            self.status.setText(_("Starting in {n}s…", n=self._left))
        else:
            self.countdown.stop()
            self._engage()

    def _engage(self):
        """Fim do atraso: sai clicando, ou arma a captura do botão do mouse."""
        self._remain = self.duration.value()
        if self._remain:
            self.autostop.start()
        if self.mode_key() == "mouse_hold":
            self._arm_hold()
        else:
            self._start_clicking()

    def _arm_hold(self):
        path = self.selected_mouse()[0]
        self.holder = MouseHold(self.mouse, BTN[self.btn_sel.currentData()],
                                only=[path] if path else None)
        self.holder.pressed.connect(self._start_clicking)
        self.holder.released.connect(self._hold_released)
        self.holder.failed.connect(self._hold_failed)
        if not self.holder.start():
            self.holder = None
            return
        self._state = "armed"
        self._show_status()
        self._paint_status()

    def _hold_failed(self, msg):
        self.warn.setText(_("Hold mode unavailable: {msg}", msg=_(msg)))
        self.set_running(False)

    def _hold_released(self):
        self._stop_clicker()
        if self.running:
            self._state = "armed"
            self._show_status()
            self._paint_status()

    def _start_clicking(self):
        """Liga o que estiver habilitado: cliques, macro de teclado e/ou a
        tecla direcionada a uma janela."""
        if self.clicker or self.keymacro or self.target_timer.isActive():
            return
        if self.click_box.isChecked():
            self.clicker = Clicker(self.mouse, self.interval.value(),
                                   BTN[self.btn_sel.currentData()])
            self.clicker.start()
        if self.key_box.isChecked() and self.ensure_keyboard():
            self.keymacro = KeyMacro(self.keyboard, self.key_interval.value(),
                                     self.key_sel.code,
                                     self.key_mode.currentData() == "Hold")
            self.keymacro.start()
        self._state = "run"
        if self.target_box.isChecked():
            self._target_start()
        self._show_status()
        self._paint_status()

    def _stop_clicker(self):
        self.target_timer.stop()
        if self.clicker:
            self.clicker.stop()
            self.clicker = None
        if self.keymacro:
            self.keymacro.stop()
            self.keymacro = None

    # ---------------------------------------------------------- teclado --
    def ensure_keyboard(self):
        if self.keyboard is None:
            try:
                self.keyboard = VirtualKeyboard()
            except OSError as e:
                self.warn.setText(f"virtual keyboard failed: {e}")
                return False
        return True

    def _key_box_toggled(self, on):
        """Cria/destroi o device só quando a macro é ligada — evita deixar um
        teclado virtual pendurado na sessão de quem não usa a função."""
        if on:
            self.ensure_keyboard()
        else:
            self._stop_clicker() if self.keymacro else None
            if self.keyboard:
                self.keyboard.close()
                self.keyboard = None

    # ------------------------------------------ janela alvo (beta) --------
    def refresh_windows(self):
        """Lista as janelas abertas, com ícone, mantendo a seleção se possível."""
        if self.bridge is None:
            self.bridge = KWinBridge()
        keep = (self.win_sel.currentData() or {}).get("id")
        self.win_sel.blockSignals(True)
        self.win_sel.clear()
        wins = self.bridge.windows() if self.bridge.ok else []
        if self.xinject is None:
            self.xinject = XInject()
        xs = self.xinject.windows() if self.xinject.ok else []
        by_pid = {pid: xid for xid, pid, _cls in xs if pid}
        by_cls = {cls.lower(): xid for xid, _pid, cls in xs if cls}
        for w in wins:
            xid = by_pid.get(w["pid"]) or by_cls.get(w["cls"].lower())
            w["xid"] = xid
            mark = "⌨ " if xid else "◐ "     # injeção real x precisa de foco
            label = f"{mark}{w['title'][:36]}  —  {w['cls']}"
            self.win_sel.addItem(app_icon(w["cls"]), label,
                                 {"id": w["id"], "xid": xid})
        if not wins:
            self.win_sel.addItem(_("no window found"), None)
        idx = next((i for i in range(self.win_sel.count())
                    if (self.win_sel.itemData(i) or {}).get("id") == keep), -1)
        self.win_sel.setCurrentIndex(idx if idx >= 0 else 0)
        self.win_sel.blockSignals(False)
        self.win_sel.setEnabled(bool(wins))
        self._update_win_warn()
        if not self.bridge.ok:
            self.warn.setText(_("Targeted key unavailable: {msg}",
                                msg=self.bridge.error or "?"))
        return wins

    def _ensure_xinject(self):
        if self.xinject is None:
            self.xinject = XInject()
        return self.xinject

    def _update_win_warn(self):
        """Wayland puro não tem como receber tecla endereçada: avisa e ensina
        a reabrir o programa como cliente X11, que aí entra na injeção real."""
        if not hasattr(self, "win_warn"):
            return
        sel = self.win_sel.currentData() or {}
        if not self.win_sel.isEnabled() or not sel or sel.get("xid"):
            self._set_win_warn("")
        else:
            self._set_win_warn(_(
                "⚠ Pure Wayland: reachable only by stealing focus for an "
                "instant. Reopen this program in X11 mode and it takes the key "
                "directly, even minimized — SDL_VIDEODRIVER=x11, "
                "GDK_BACKEND=x11 or QT_QPA_PLATFORM=xcb."))

    def _set_win_warn(self, text):
        """Reserva a altura de verdade do texto: QLabel com quebra de linha
        informa uma linha só como mínimo, e o layout espreme as linhas de cima."""
        self.win_warn.setText(text)
        if not text:
            self.win_warn.setMinimumHeight(0)
            return
        width = max(self.win_warn.width(), 360)
        rect = self.win_warn.fontMetrics().boundingRect(
            0, 0, width, 0, Qt.TextWordWrap, text)
        self.win_warn.setMinimumHeight(rect.height() + 6)

    def _target_ready(self):
        """Janela escolhida pronta. Só janela Wayland precisa do teclado
        virtual: a X11 recebe a tecla endereçada, sem passar pelo seat."""
        if not self.win_sel.count() or self.win_sel.currentData() is None:
            self.refresh_windows()
        sel = self.win_sel.currentData()
        if not sel:
            self.warn.setText(_("Pick a window first (↻ to rescan)."))
            return False
        if sel.get("xid"):
            if not ((self._ensure_xinject().keysym_name(self.win_key.code))
                    or x_keysym(self.win_key.name)):
                self.warn.setText(_("That key has no X11 equivalent."))
                return False
            return True
        return self.ensure_keyboard()

    def _target_start(self):
        """Manda a tecla na hora e depois a cada N segundos — esperar o
        intervalo inteiro para o primeiro envio parecia que não funcionava."""
        if not self._target_ready():
            return False
        self._target_tick()
        self.target_timer.start(self.win_secs.value() * 1000)
        return True

    def _target_toggled(self, on):
        if not on:
            self.target_timer.stop()
        elif self.running and self._state == "run":
            self._target_start()
        else:
            self.refresh_windows()
            self.ensure_keyboard()   # 0,4 s de settle: paga agora, não no Start
        self._show_status()

    def _target_restart(self, _v=None):
        if self.target_timer.isActive():
            self.target_timer.start(self.win_secs.value() * 1000)

    def _target_tick(self):
        """Janela X11: injeta direto, sem tocar no foco nem precisar dela
        visível. Janela Wayland: não há como endereçar, então cai no truque de
        dar foco por um instante e devolver."""
        sel = self.win_sel.currentData() or {}
        wid, xid = sel.get("id"), sel.get("xid")
        if not wid:
            return
        if xid:
            keysym = (self._ensure_xinject().keysym_name(self.win_key.code)
                      or x_keysym(self.win_key.name))
            if keysym and self.xinject and self.xinject.ok:
                t0 = time.perf_counter()
                if self.xinject.send_key(xid, keysym):
                    self.target_hits += 1
                    self.target_ms = (time.perf_counter() - t0) * 1000
                    self._show_status()
                    return
        if not self.keyboard:
            return
        t0 = time.perf_counter()
        prev = self.bridge.activate(wid)
        if prev is None:
            self.warn.setText(_("Lost the target window."))
            self.target_box.setChecked(False)
            return
        # o hold do tap já é a folga para a tecla chegar antes de devolver foco
        self.keyboard.tap(self.win_key.code)
        self.target_hits += 1
        if prev and prev != wid:
            self.bridge.activate(prev)
        # quanto tempo o foco do usuário ficou roubado neste ciclo
        self.target_ms = (time.perf_counter() - t0) * 1000
        self._show_status()

    # ---------------------------------------------------------- anti-afk --
    def _afk_toggled(self, on):
        if self.antiafk:
            self.antiafk.stop()
            self.antiafk = None
        if on and self.ensure_mouse():
            self.antiafk = AntiAfk(self.mouse, self.afk_secs.value())
            self.antiafk.start()
        self._show_status()

    def _afk_restart(self, _v=None):
        if self.afk.isChecked():
            self._afk_toggled(True)

    def _show_status(self):
        state = getattr(self, "_state", "")
        if state == "armed":
            txt = "◆ " + _("ARMED — hold {btn} mouse button",
                           btn=self.btn_sel.currentText().lower())
        elif state == "run":
            parts = []
            if self.clicker:
                parts.append(self._rate_str())
            if self.keymacro:
                parts.append(f"{self.key_sel.name} "
                             + (_("held") if self.keymacro.hold
                                else f"{self.key_interval.value():.0f} ms"))
            txt = ("● " + _("RUNNING") + "  ("
                   + " + ".join(parts or [_("nothing enabled")]) + ")")
        else:
            txt = _("Stopped")
        if self._remain:
            txt += "  —  " + _("stops in {n}s", n=self._remain)
        if self.antiafk:
            txt += "   ⟲ " + _("anti-AFK")
        if self.target_timer.isActive():
            txt += ("   ⌨ " + self.win_key.name
                    + f" ×{self.target_hits}"
                    + (f" ({self.target_ms:.0f} ms)" if self.target_ms else ""))
        self.status.setText(txt)

    def _tick_autostop(self):
        self._remain -= 1
        if self._remain <= 0:
            self.set_running(False)
            self.status.setText(_("Stopped (time is up)"))
        else:
            self._show_status()

    # ------------------------------------------------------------ atalho --
    def on_press(self):
        if self.mode_key() == "hotkey_hold":
            self.set_running(True)
        else:
            self.set_running(not self.running)

    def on_release(self):
        if self.mode_key() == "hotkey_hold":
            self.set_running(False)

    def on_hotkey_change(self, text):
        self.watcher.set_key(KEYNAMES[text])
        self.local_sc.setKey(QKeySequence(text))
        self.local_sc.setEnabled(not self.watcher.ok)

    # Limpeza via aboutToQuit e não closeEvent: sobrescrever closeEvent faz o
    # shiboken embrulhar o QCloseEvent, o que segfaulta no PySide6 + Python
    # 3.15rc do Fedora 45 quando a janela é fechada pelo botão do compositor.
    def cleanup(self):
        if getattr(self, "_cleaned", False):
            return
        self._cleaned = True
        self.set_running(False)
        if self.antiafk:
            self.antiafk.stop()
            self.antiafk = None
        self.target_timer.stop()
        if self.bridge:
            self.bridge.cleanup()
        self.watcher.stop()
        if self.keyboard:
            self.keyboard.close()
            self.keyboard = None
        if self.mouse:
            self.mouse.close()
            self.mouse = None
        try:
            with open(CFG, "w") as fh:
                json.dump({"interval_ms": self.interval.value(),
                           "button": self.btn_sel.currentData(),
                           "delay": self.delay.value(),
                           "duration": self.duration.value(),
                           "hotkey": self.hk.currentText(),
                           "mode": self.mode_key(),
                           "sound": self.sound.isChecked(),
                           "mouse_name": self.selected_mouse()[1],
                           "click_enabled": self.click_box.isChecked(),
                           "key_enabled": self.key_box.isChecked(),
                           "key": self.key_sel.name,
                           "key_code": self.key_sel.code,
                           "key_interval_ms": self.key_interval.value(),
                           "key_mode": self.key_mode.currentData(),
                           "afk_seconds": self.afk_secs.value(),
                           "target_key": self.win_key.name,
                           "target_key_code": self.win_key.code,
                           "target_seconds": self.win_secs.value(),
                           "theme": self.theme,
                           "tray_style": self.tray_style,
                           "tray_color": self.tray_color,
                           "language": LANG}, fh)
        except Exception:
            pass


USAGE = f"""WayClick {VERSION} — autoclicker for Wayland, via /dev/uinput

usage: wayclick [--tray] [--version] [--help]

  --tray      start hidden in the system tray
  --version   print the version and exit
  --help      show this help and exit

{HOMEPAGE}"""


if __name__ == "__main__":
    if "--help" in sys.argv[1:] or "-h" in sys.argv[1:]:
        print(USAGE)
        sys.exit(0)
    if "--version" in sys.argv[1:] or "-V" in sys.argv[1:]:
        print(f"WayClick {VERSION}")
        sys.exit(0)
    reexec_with_input_group()   # antes do Qt: pode substituir o processo
    app = QApplication(sys.argv)
    w = App()
    app.aboutToQuit.connect(w.cleanup)

    # Ctrl+C no terminal: sem isso o sinal só é visto quando o interpretador
    # volta a rodar bytecode, e o KeyboardInterrupt estoura no meio da limpeza
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    wake = QTimer()
    wake.setInterval(200)
    wake.timeout.connect(lambda: None)   # devolve o controle ao Python
    wake.start()

    if "--tray" in sys.argv[1:] and w.tray:
        w.hide()                          # início silencioso, só a bandeja
        w._sync_tray()
    else:
        w.show()
    sys.exit(app.exec())
