import sys, time
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QLabel
import sys as _sys, os as _o
_sys.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
import wayclick as a
a.AUTO_UPDATE_CHECK = False   # sem rede nos testes
a.CFG = "/tmp/autoclick-test-config.json"

ms = float(sys.argv[1])
app = QApplication(sys.argv[:1])
c = QLabel("catcher"); c.setAlignment(Qt.AlignCenter)
c.setStyleSheet("background:#123;color:#fff;font-size:26px;")
n = {"c": 0}
c.mousePressEvent = lambda e: n.__setitem__("c", n["c"] + 1)
c.showFullScreen()

w = a.App(); w.interval.setValue(ms); w.delay.setValue(0); w.duration.setValue(0)
w.click_box.setChecked(True); w.key_box.setChecked(False)
w.mode.setCurrentText("Hotkey toggles"); w.sound.setChecked(False)
ui = {"ticks": 0, "worst": 0.0, "last": None}
def heartbeat():           # mede travamento da thread da UI
    now = time.perf_counter()
    if ui["last"] is not None:
        ui["worst"] = max(ui["worst"], now - ui["last"] - 0.02)
    ui["last"] = now; ui["ticks"] += 1
hb = QTimer(); hb.setInterval(20); hb.timeout.connect(heartbeat); hb.start()

t = {}
QTimer.singleShot(600, lambda: (ui.update({"worst":0.0,"last":None}), w.set_running(True), t.__setitem__("t0", time.perf_counter())))
def stop():
    t["t1"] = time.perf_counter(); t["n"] = w.clicker.count; w.set_running(False)
QTimer.singleShot(3600, stop)
QTimer.singleShot(4200, app.quit)
app.exec()
dur = t["t1"] - t["t0"]
print(f"alvo {ms} ms = {1000/ms:,.0f} c/s")
print(f"emitido: {t['n']:,} em {dur:.2f}s = {t['n']/dur:,.0f} c/s "
      f"({dur/t['n']*1000:.3f} ms/clique)")
print(f"recebido pela janela: {n['c']:,}  ({n['c']/t['n']*100:.1f}%)")
print(f"UI: {ui['ticks']} ticks, pior travada {ui['worst']*1000:.0f} ms")
