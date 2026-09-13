"""Macro com várias teclas ao mesmo tempo, cada uma no seu intervalo.

Conta o que chega numa janela real: Space a cada 100 ms e W a cada 250 ms
devem aparecer na mesma proporção dos intervalos, e remover uma linha tem que
calar só aquela tecla.
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
cat = QLabel("multi-key macro test"); cat.setAlignment(Qt.AlignCenter)
cat.setStyleSheet("background:#132;color:#fff;font-size:24px;")
got = {}
cat.keyPressEvent = lambda e: got.__setitem__(e.key(), got.get(e.key(), 0) + 1)
cat.showFullScreen()
cat.setFocus()

w = a.App()
app.aboutToQuit.connect(w.cleanup)
w.sound.setChecked(False)
w.click_box.setChecked(False)
w.key_box.setChecked(True)
w.mode.setCurrentText("No trigger (hotkey toggles)")
w.delay.setValue(0); w.duration.setValue(0)

first = w.key_rows[0]
first.catcher.set_key(a.KEYS["Space"], "Space")
first.interval.setValue(100.0)
first.mode.setCurrentText("Repeat")
second = w._add_key_row({"key": "W", "key_code": a.KEYS["W"],
                         "interval_ms": 250.0, "mode": "Repeat"})

R = {}
SPACE, W = Qt.Key_Space, Qt.Key_W


def phase_both():
    R["rows"] = len(w.key_rows)
    base = dict(got)
    w.set_running(True)
    QTimer.singleShot(2000, lambda: (
        R.__setitem__("space", got.get(SPACE, 0) - base.get(SPACE, 0)),
        R.__setitem__("w", got.get(W, 0) - base.get(W, 0)),
        R.__setitem__("threads", len(w.keymacros)),
        phase_drop()))


def phase_drop():
    """Remove a linha do W com a macro rodando: só o Space deve continuar."""
    w._remove_key_row(second)
    base = dict(got)
    QTimer.singleShot(1500, lambda: (
        R.__setitem__("rows_after", len(w.key_rows)),
        R.__setitem__("space_after", got.get(SPACE, 0) - base.get(SPACE, 0)),
        R.__setitem__("w_after", got.get(W, 0) - base.get(W, 0)),
        w.set_running(False),
        QTimer.singleShot(400, app.quit)))


QTimer.singleShot(900, phase_both)
QTimer.singleShot(12000, app.quit)
app.exec()

print(f"duas linhas ({R.get('rows')}), {R.get('threads')} threads: "
      f"Space {R.get('space')} (~20 esperados), W {R.get('w')} (~8)")
print(f"apos remover a linha do W ({R.get('rows_after')} linha): "
      f"Space {R.get('space_after')} (~15), W {R.get('w_after')} (0)")
ok = (R.get("rows") == 2 and R.get("threads") == 2
      and 14 <= (R.get("space") or 0) <= 26
      and 5 <= (R.get("w") or 0) <= 12
      and R.get("rows_after") == 1
      and 10 <= (R.get("space_after") or 0) <= 20
      and R.get("w_after") == 0)
print("RESULTADO:", "tudo ok" if ok else "FALHOU")
