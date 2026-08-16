import sys
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QLabel
import sys as _sys, os as _o
_sys.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
import wayclick as a
a.CFG = "/tmp/autoclick-test-config.json"
app = QApplication(sys.argv)
c = QLabel("catcher"); c.setAlignment(Qt.AlignCenter)
c.setStyleSheet("background:#123;color:#fff;font-size:26px;")
n={"c":0}; c.mousePressEvent=lambda e:(n.__setitem__("c",n["c"]+1), c.setText(f"cliques {n['c']}"))
c.showFullScreen()
w = a.App(); w.interval.setValue(50.0); w.delay.setValue(0); w.duration.setValue(2)
w.click_box.setChecked(True); w.key_box.setChecked(False)
w.mode.setCurrentText("Hotkey toggles"); w.sound.setChecked(False)
QTimer.singleShot(500, lambda: w.set_running(True))
QTimer.singleShot(1500, lambda: print("meio:", w.status.text()))
QTimer.singleShot(3200, lambda: print("fim:", w.status.text(), "| botao:", w.btn.text(), "| running:", w.running))
QTimer.singleShot(5000, lambda: print("cliques apos parar (deve continuar igual):", n["c"]))
QTimer.singleShot(5300, app.quit)
app.exec()
print("TOTAL:", n["c"], "(esperado ~40 = 50ms x 2s)")
