"""E2E da macro de teclado: as teclas chegam mesmo numa janela real?

Conta keyPressEvent numa janela em tela cheia enquanto a macro roda, nos dois
modos (repetir e segurar).
"""
import sys, time
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QLabel
import sys as _sys, os as _o
_sys.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
import wayclick as a
a.AUTO_UPDATE_CHECK = False   # sem rede nos testes
import os as _os                       # config isolada e limpa por teste
a.CFG = f"/tmp/autoclick-test-{_os.path.basename(__file__)}.json"
_os.path.exists(a.CFG) and _os.remove(a.CFG)

app = QApplication(sys.argv)
cat = QLabel("keyboard macro test"); cat.setAlignment(Qt.AlignCenter)
cat.setStyleSheet("background:#231;color:#fff;font-size:24px;")
got = {"press": 0, "release": 0, "keys": set()}
cat.keyPressEvent = lambda e: (got.__setitem__("press", got["press"] + 1),
                               got["keys"].add(e.key()))
cat.keyReleaseEvent = lambda e: got.__setitem__("release", got["release"] + 1)
cat.showFullScreen()
cat.setFocus()

w = a.App()
app.aboutToQuit.connect(w.cleanup)
w.sound.setChecked(False)
w.click_box.setChecked(False)          # só teclado neste teste
w.key_box.setChecked(True)
row = w.key_rows[0]                    # a macro agora e uma lista de teclas
row.catcher.set_key(a.KEYS["Space"], "Space")
row.interval.setValue(50.0)
row.mode.setCurrentText("Repeat")
w.delay.setValue(0); w.duration.setValue(0)

R = {}


def phase_repeat():
    R["base"] = dict(got)
    w.set_running(True)
    QTimer.singleShot(1200, lambda: (
        R.__setitem__("repeat", {k: (got[k] - R["base"][k]) if k != "keys"
                                 else None for k in got}),
        R.__setitem__("emitted", w.keymacros[0].count),
        w.set_running(False),
        QTimer.singleShot(400, phase_hold)))


def phase_hold():
    row.mode.setCurrentText("Hold")
    row.catcher.set_key(a.KEYS["Left Shift"], "Left Shift")  # modificador: nao tem auto-repeat
    base = dict(got)
    w.set_running(True)
    QTimer.singleShot(900, lambda: (
        R.__setitem__("hold_press", got["press"] - base["press"]),
        R.__setitem__("hold_release_before_stop", got["release"] - base["release"]),
        w.set_running(False),
        QTimer.singleShot(400, lambda: (
            R.__setitem__("hold_release_after_stop",
                          got["release"] - base["release"]),
            app.quit()))))


QTimer.singleShot(900, phase_repeat)
QTimer.singleShot(9000, app.quit)
app.exec()

rep = R.get("repeat", {})
print(f"repeat 50 ms por ~1,2 s: emitidas {R.get('emitted')}, "
      f"recebidas {rep.get('press')}")
print(f"hold: press recebidos {R.get('hold_press')} | "
      f"releases durante o hold {R.get('hold_release_before_stop')} | "
      f"depois do stop {R.get('hold_release_after_stop')}")
ok = (20 <= (rep.get("press") or 0) <= 30
      and R.get("hold_press") == 1
      and R.get("hold_release_before_stop") == 0
      and R.get("hold_release_after_stop") == 1)
print("RESULTADO:", "tudo ok" if ok else "FALHOU")
