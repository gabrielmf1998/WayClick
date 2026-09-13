"""O gatilho segurando só parte das engines.

O caso que motivou isto: "segurar o botão para clicar, mas a macro de teclado
rodando o tempo todo". Antes o modo era tudo ou nada — armar não rodava nada,
nem o que não tinha relação com o mouse.

Com "Keyboard" desmarcado em "Trigger holds", a macro sai junto com o Arm e o
clique continua esperando o botão.
"""
import fcntl, os, struct, sys, time
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
for r in (0, 1, 8):
    fcntl.ioctl(fd, a.UI_SET_RELBIT, r)
os.write(fd, struct.pack("80s", FAKE) + struct.pack("HHHH", 3, 0xAAA, 0xBBB, 1)
         + struct.pack("i", 0) + b"\x00" * 1024)
fcntl.ioctl(fd, a.UI_DEV_CREATE)
time.sleep(0.5)

a.is_mouse = a.is_mouse_any = lambda f: FAKE in a.dev_name(f.fileno())


def send(*events):
    buf = b"".join(struct.pack("llHHi", 0, 0, t, c, v) for t, c, v in events)
    os.write(fd, buf + struct.pack("llHHi", 0, 0, a.EV_SYN, 0, 0))


app = QApplication(sys.argv)
cat = QLabel("trigger scope: keyboard runs free, clicking waits")
cat.setAlignment(Qt.AlignCenter)
cat.setStyleSheet("background:#213;color:#fff;font-size:22px;")
got = {"key": 0, "click": 0}
cat.keyPressEvent = lambda e: got.__setitem__("key", got["key"] + 1)
cat.mousePressEvent = lambda e: got.__setitem__("click", got["click"] + 1)
cat.showFullScreen()
cat.setFocus()

w = a.App()
app.aboutToQuit.connect(w.cleanup)
w.sound.setChecked(False)
w.click_box.setChecked(True)
w.key_box.setChecked(True)
w.interval.setValue(50.0)
row = w.key_rows[0]
row.catcher.set_key(a.KEYS["Space"], "Space")
row.interval.setValue(100.0)
row.mode.setCurrentText("Repeat")
w.mode.setCurrentText("Runs while mouse button is held")
w.trig_boxes["click"].setChecked(True)      # o clique espera o botão
w.trig_boxes["key"].setChecked(False)       # a macro roda solta
w.delay.setValue(0); w.duration.setValue(0)

R = {}


def phase_arm():
    """Arm: a macro deve sair na hora, o clique deve esperar."""
    base = dict(got)
    w.set_running(True)
    QTimer.singleShot(1300, lambda: (
        R.__setitem__("arm_state", w._state),
        R.__setitem__("arm_keys", got["key"] - base["key"]),
        R.__setitem__("arm_clicks", got["click"] - base["click"]),
        phase_hold()))


def phase_hold():
    """Botão segurado: agora o clique também roda."""
    base = dict(got)
    send((a.EV_KEY, a.BTN["Left"], 1))
    QTimer.singleShot(1000, lambda: (
        R.__setitem__("hold_keys", got["key"] - base["key"]),
        R.__setitem__("hold_clicks", got["click"] - base["click"]),
        phase_release()))


def phase_release():
    """Soltou: o clique para, a macro continua."""
    send((a.EV_KEY, a.BTN["Left"], 0))
    base = dict(got)
    QTimer.singleShot(1200, lambda: (
        R.__setitem__("rel_keys", got["key"] - base["key"]),
        R.__setitem__("rel_clicks", got["click"] - base["click"]),
        R.__setitem__("rel_state", w._state),
        w.set_running(False),
        QTimer.singleShot(400, app.quit)))


QTimer.singleShot(900, phase_arm)
QTimer.singleShot(14000, app.quit)
app.exec()

try:
    fcntl.ioctl(fd, a.UI_DEV_DESTROY); os.close(fd)
except OSError:
    pass

print(f"apos Arm  (estado {R.get('arm_state')!r}): "
      f"Space {R.get('arm_keys')} (~13), cliques {R.get('arm_clicks')} (0)")
print(f"segurando o botao:              "
      f"Space {R.get('hold_keys')} (~10), cliques {R.get('hold_clicks')} (>10)")
# o release atravessa a thread do relay ate a do Qt antes do clicker parar,
# entao um clique ja emitido ainda pode chegar: a cauda e esperada, nao deriva
print(f"apos soltar (estado {R.get('rel_state')!r}):  "
      f"Space {R.get('rel_keys')} (~12), cliques {R.get('rel_clicks')} (cauda, <=2)")
ok = (R.get("arm_state") == "armed"
      and 8 <= (R.get("arm_keys") or 0) <= 18      # macro solta desde o Arm
      and R.get("arm_clicks") == 0                 # clique preso ao gatilho
      and (R.get("hold_clicks") or 0) > 10         # botao segurado: clica
      and 5 <= (R.get("hold_keys") or 0) <= 15     # macro segue rodando
      and (R.get("rel_clicks") or 0) <= 2          # soltou: so a cauda do stop
      and 7 <= (R.get("rel_keys") or 0) <= 18      # macro nao para junto
      and R.get("rel_state") == "armed")
print("RESULTADO:", "tudo ok" if ok else "FALHOU")
