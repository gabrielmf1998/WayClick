"""Modo "segurar o botão" com o clique DESLIGADO — só a macro de teclado.

Dois regressões de uma vez:

  1. o modo de gatilho vale para tudo, não só para o clique: armado, a macro
     não dispara sozinha, e passa a disparar quando o botão é segurado;
  2. sem clique não existe quem reemita o botão-gatilho, então o relay tem que
     deixá-lo passar. Engolindo, o botão do usuário morre enquanto armado.

Como em t_hold.py, o grab é restrito a um mouse falso, para não sequestrar o
mouse real de quem roda o teste.
"""
import fcntl, os, struct, sys, time
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QLabel
import sys as _sys, os as _o
_sys.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
import wayclick as a
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

a.is_mouse = a.is_mouse_any = lambda f: FAKE in a.dev_name(f.fileno())


def send(*events):
    buf = b"".join(struct.pack("llHHi", 0, 0, t, c, v) for t, c, v in events)
    os.write(fd, buf + struct.pack("llHHi", 0, 0, a.EV_SYN, 0, 0))


app = QApplication(sys.argv)
cat = QLabel("hold + keyboard macro, no clicking")
cat.setAlignment(Qt.AlignCenter)
cat.setStyleSheet("background:#312;color:#fff;font-size:22px;")
got = {"key": 0, "lmb": 0, "rmb": 0}
cat.keyPressEvent = lambda e: got.__setitem__("key", got["key"] + 1)
cat.mousePressEvent = lambda e: got.__setitem__(
    "lmb" if e.button() == Qt.LeftButton else "rmb",
    got["lmb" if e.button() == Qt.LeftButton else "rmb"] + 1)
cat.showFullScreen()
cat.setFocus()

w = a.App()
app.aboutToQuit.connect(w.cleanup)
w.sound.setChecked(False)
w.click_box.setChecked(False)                 # o ponto do teste
w.key_box.setChecked(True)
w.key_sel.set_key(a.KEYS["Space"], "Space")
w.key_interval.setValue(100.0)
w.key_mode.setCurrentText("Repeat")
w.mode.setCurrentText("Runs while mouse button is held")
w.delay.setValue(0); w.duration.setValue(0)

R = {}


def phase_armed():
    """Start apertado: arma, mas nada roda até o botão descer."""
    base = dict(got)
    w.set_running(True)
    QTimer.singleShot(1300, lambda: (
        R.__setitem__("armed_state", w._state),
        R.__setitem__("armed_keys", got["key"] - base["key"]),
        phase_hold()))


def phase_hold():
    """Botão-gatilho segurado: a macro roda E o clique chega na janela."""
    base = dict(got)
    send((a.EV_KEY, a.BTN["Left"], 1))
    QTimer.singleShot(1300, lambda: (
        R.__setitem__("hold_keys", got["key"] - base["key"]),
        R.__setitem__("hold_lmb", got["lmb"] - base["lmb"]),
        phase_other()))


def phase_other():
    """Botão que não é o gatilho: o relay de sempre, como controle."""
    send((a.EV_KEY, a.BTN["Left"], 0))
    base = dict(got)
    QTimer.singleShot(300, lambda: send((a.EV_KEY, a.BTN["Right"], 1)))
    QTimer.singleShot(500, lambda: send((a.EV_KEY, a.BTN["Right"], 0)))
    QTimer.singleShot(900, lambda: (
        R.__setitem__("other_rmb", got["rmb"] - base["rmb"]),
        R.__setitem__("after_release_state", w._state),
        w.set_running(False),
        QTimer.singleShot(400, app.quit)))


QTimer.singleShot(900, phase_armed)
QTimer.singleShot(14000, app.quit)
app.exec()

try:
    fcntl.ioctl(fd, a.UI_DEV_DESTROY); os.close(fd)
except OSError:
    pass

print(f"armado, sem tocar no mouse: estado {R.get('armed_state')!r}, "
      f"teclas {R.get('armed_keys')}")
print(f"segurando o gatilho: teclas {R.get('hold_keys')}, "
      f"cliques entregues {R.get('hold_lmb')}")
print(f"botão não-gatilho: cliques entregues {R.get('other_rmb')} | "
      f"ao soltar, estado {R.get('after_release_state')!r}")
ok = (R.get("armed_state") == "armed"
      and R.get("armed_keys") == 0            # nada roda só por armar
      and 8 <= (R.get("hold_keys") or 0) <= 18  # ~10/s a 100 ms
      and R.get("hold_lmb") == 1              # o botão do usuário sobrevive
      and R.get("other_rmb") == 1
      and R.get("after_release_state") == "armed")
print("RESULTADO:", "tudo ok" if ok else "FALHOU")
