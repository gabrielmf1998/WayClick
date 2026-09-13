"""E2E do modo "clicar enquanto segura", com um mouse físico falso.

is_mouse é restringido ao device de teste de propósito: assim o grab não
sequestra o mouse real de quem está rodando o teste.
"""
import fcntl, os, struct, sys, threading, time
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QLabel
import sys as _sys, os as _o
_sys.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
import wayclick as a
a.AUTO_UPDATE_CHECK = False   # sem rede nos testes
import os as _os                       # config isolada e limpa por teste
a.CFG = f"/tmp/autoclick-test-{_os.path.basename(__file__)}.json"
_os.path.exists(a.CFG) and _os.remove(a.CFG)

FAKE = b"wayclick-fake-physical-mouse"

fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
fcntl.ioctl(fd, a.UI_SET_EVBIT, a.EV_KEY); fcntl.ioctl(fd, a.UI_SET_EVBIT, a.EV_REL)
for c in a.BTN.values():
    fcntl.ioctl(fd, a.UI_SET_KEYBIT, c)
for r in (0, 1, 8):                      # X, Y, WHEEL
    fcntl.ioctl(fd, a.UI_SET_RELBIT, r)
os.write(fd, struct.pack("80s", FAKE) + struct.pack("HHHH", 3, 0xAAA, 0xBBB, 1)
         + struct.pack("i", 0) + b"\x00" * 1024)
fcntl.ioctl(fd, a.UI_DEV_CREATE)
time.sleep(0.5)

a.is_mouse = a.is_mouse_any = lambda f: FAKE in a.dev_name(f.fileno())     # grab só no device de teste


def send(*events):
    buf = b"".join(struct.pack("llHHi", 0, 0, t, c, v) for t, c, v in events)
    os.write(fd, buf + struct.pack("llHHi", 0, 0, a.EV_SYN, 0, 0))


app = QApplication(sys.argv)
cat = QLabel("hold test"); cat.setAlignment(Qt.AlignCenter)
cat.setStyleSheet("background:#132;color:#fff;font-size:24px;")
got = {"press": 0, "release": 0, "move": 0}
cat.mousePressEvent = lambda e: got.__setitem__("press", got["press"] + 1)
cat.mouseReleaseEvent = lambda e: got.__setitem__("release", got["release"] + 1)
cat.mouseMoveEvent = lambda e: got.__setitem__("move", got["move"] + 1)
cat.setMouseTracking(True)
cat.showFullScreen()

w = a.App()
w.click_box.setChecked(True); w.key_box.setChecked(False)
app.aboutToQuit.connect(w.cleanup)
w.mode.setCurrentText("Runs while mouse button is held")
w.delay.setValue(0); w.duration.setValue(0); w.interval.setValue(20.0)
w.sound.setChecked(False)

R = {}


def phase1():
    w.set_running(True)
    QTimer.singleShot(400, phase2)


def phase2():
    R["armed"] = (w._state, w.holder is not None and len(w.holder.files))
    R["idle_before"] = dict(got)
    send((a.EV_KEY, 0x110, 1))                       # SEGURA o botão
    QTimer.singleShot(120, lambda: send((a.EV_REL, 0, 25), (a.EV_REL, 1, 15)))
    QTimer.singleShot(900, phase3)


def phase3():
    R["during"] = {k: got[k] - R["idle_before"][k] for k in got}
    R["state_holding"] = w._state
    send((a.EV_KEY, 0x110, 0))                       # SOLTA
    QTimer.singleShot(300, phase4)


def phase4():
    after = dict(got)
    R["state_released"] = w._state
    QTimer.singleShot(500, lambda: finish(after))


def finish(after):
    R["after_release"] = {k: got[k] - after[k] for k in got}
    w.set_running(False)
    R["ungrabbed"] = w.holder is None
    QTimer.singleShot(100, app.quit)


QTimer.singleShot(700, phase1)
QTimer.singleShot(12000, app.quit)
app.exec()
fcntl.ioctl(fd, a.UI_DEV_DESTROY); os.close(fd)

print("armado (estado, devices capturados):", R.get("armed"))
print("parado antes de segurar:", R.get("idle_before"))
print("durante o hold (~0,9 s a 20 ms):", R.get("during"), "estado:",
      R.get("state_holding"))
print("depois de soltar:", R.get("after_release"), "estado:",
      R.get("state_released"))
print("grab liberado no stop:", R.get("ungrabbed"))
d = R.get("during", {})
ok = (R.get("armed", (None, 0))[0] == "armed" and 30 <= d.get("press", 0) <= 60
      and d.get("move", 0) > 0 and R.get("state_holding") == "run"
      and R.get("state_released") == "armed"
      and R.get("after_release", {}).get("press", 1) == 0
      and R.get("ungrabbed"))
print("RESULTADO:", "tudo ok" if ok else "FALHOU")
