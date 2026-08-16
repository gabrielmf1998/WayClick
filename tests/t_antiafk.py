"""Anti-AFK: o ponteiro mexe de verdade e volta exatamente para onde estava?

Uma janela em tela cheia com mouse tracking registra cada posição recebida;
comparamos a primeira com a última e contamos os movimentos.
"""
import sys, time
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QLabel
import sys as _sys, os as _o
_sys.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
import wayclick as a
import os as _os                       # config isolada e limpa por teste
a.CFG = f"/tmp/autoclick-test-{_os.path.basename(__file__)}.json"
_os.path.exists(a.CFG) and _os.remove(a.CFG)

app = QApplication(sys.argv)
cat = QLabel("anti-afk test"); cat.setAlignment(Qt.AlignCenter)
cat.setStyleSheet("background:#123;color:#fff;font-size:22px;")
seen = []
cat.mouseMoveEvent = lambda e: seen.append((e.globalPosition().x(),
                                            e.globalPosition().y()))
cat.setMouseTracking(True)
cat.showFullScreen()

w = a.App()
app.aboutToQuit.connect(w.cleanup)
w.sound.setChecked(False)
w.afk_secs.setValue(1)

R = {}


def start():
    seen.clear()
    w.afk.setChecked(True)
    R["t0"] = time.perf_counter()
    QTimer.singleShot(4600, stop)


def stop():
    R["elapsed"] = time.perf_counter() - R["t0"]
    R["nudges"] = w.antiafk.count
    R["moves"] = len(seen)
    out, back = seen[0::2], seen[1::2]      # ida e volta de cada ciclo
    R["amplitude"] = max(abs(o[0] - b[0]) for o, b in zip(out, back))
    R["rest"] = sorted({round(b[0], 4) for b in back})
    R["drift"] = back[-1][0] - back[0][0]
    R["status"] = w.status.text()
    w.afk.setChecked(False)
    R["stopped"] = w.antiafk is None
    QTimer.singleShot(300, app.quit)


QTimer.singleShot(800, start)
QTimer.singleShot(9000, app.quit)
app.exec()

print(f"em {R.get('elapsed', 0):.1f}s a 1s: {R.get('nudges')} nudges, "
      f"{R.get('moves')} eventos de movimento na janela")
print(f"amplitude na tela: {R.get('amplitude', 0):.3f} px | "
      f"posicoes de repouso: {R.get('rest')} | deriva: {R.get('drift', 9):+.4f} px")
print(f"status mostra anti-AFK: {'⟲' in (R.get('status') or '')}")
print(f"parou ao desmarcar: {R.get('stopped')}")
ok = ((R.get("nudges") or 0) >= 4 and (R.get("moves") or 0) >= 8
      and abs(R.get("drift", 9)) < 0.001 and R.get("amplitude", 0) >= 1.0
      and R.get("stopped")
      and "⟲" in (R.get("status") or ""))
print("RESULTADO:", "tudo ok" if ok else "FALHOU")
