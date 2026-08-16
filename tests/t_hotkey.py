"""E2E do atalho global: cria um teclado virtual, manda F8, confere o toggle."""
import fcntl, os, struct, sys, time
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
import sys as _sys, os as _o
_sys.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
import wayclick as a
import os as _os                       # config isolada e limpa por teste
a.CFG = f"/tmp/autoclick-test-{_os.path.basename(__file__)}.json"
_os.path.exists(a.CFG) and _os.remove(a.CFG)

# --- teclado virtual (precisa existir ANTES do App, que varre na criação) ---
kfd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
fcntl.ioctl(kfd, 0x40045564, 1)                       # UI_SET_EVBIT EV_KEY
for code in [1] + list(a.KEYNAMES.values()):          # ESC + todas as hotkeys
    fcntl.ioctl(kfd, 0x40045565, code)
os.write(kfd, struct.pack("80s", b"wayclick-test-keyboard")
         + struct.pack("HHHH", 3, 0x1111, 0x2222, 1) + struct.pack("i", 0)
         + b"\x00" * 1024)
fcntl.ioctl(kfd, 0x5501)
time.sleep(0.4)


def key(code, value):
    for t, c, v in ((1, code, value), (0, 0, 0)):
        os.write(kfd, struct.pack("llHHi", 0, 0, t, c, v))


app = QApplication(sys.argv)
w = a.App()
w.click_box.setChecked(True); w.key_box.setChecked(False)
w.mode.setCurrentText("Hotkey toggles")
w.sound.setChecked(False)
w.delay.setValue(0)
w.duration.setValue(0)
w.interval.setValue(50.0)
print("watcher ok:", w.watcher.ok, "| teclados:", len(w.watcher.files),
      "| atalho local desligado:", not w.local_sc.isEnabled())

log = []
steps = iter([
    ("F8 press/release -> deve LIGAR", lambda: (key(66, 1), key(66, 0)), True),
    ("F8 de novo -> deve DESLIGAR", lambda: (key(66, 1), key(66, 0)), False),
    ("hold: press -> LIGA", lambda: key(66, 1), True),
    ("hold: release -> DESLIGA", lambda: key(66, 0), False),
])


def step():
    try:
        desc, action, expect = next(steps)
    except StopIteration:
        app.quit()
        return
    if desc.startswith("hold"):
        w.mode.setCurrentText("Clicks while hotkey is held")
    action()
    QTimer.singleShot(250, lambda: check(desc, expect))


def check(desc, expect):
    got = w.running
    log.append((desc, expect, got))
    print(f"  {'OK ' if got == expect else 'FALHOU'} {desc}: running={got}")
    QTimer.singleShot(150, step)


QTimer.singleShot(600, step)
QTimer.singleShot(9000, app.quit)
app.exec()
w.set_running(False)
fcntl.ioctl(kfd, 0x5502); os.close(kfd)
print("RESULTADO:", "todos ok" if all(e == g for _, e, g in log) and len(log) == 4
      else f"falhas em {[d for d, e, g in log if e != g]}")
