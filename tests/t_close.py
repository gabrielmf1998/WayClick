import sys, os, json
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
import sys as _sys, os as _o
_sys.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
import wayclick as a
a.AUTO_UPDATE_CHECK = False   # sem rede nos testes
a.SYSTEM_TRAY = False         # sem icone de bandeja nos testes
import os as _os                       # config isolada e limpa por teste
a.CFG = f"/tmp/autoclick-test-{_os.path.basename(__file__)}.json"
_os.path.exists(a.CFG) and _os.remove(a.CFG)
app = QApplication(sys.argv)
w = a.App(); app.aboutToQuit.connect(w.cleanup); w.show()
w.click_box.setChecked(True); w.key_box.setChecked(False)
w.interval.setValue(42.0)
QTimer.singleShot(1000, w.close)      # fecha a janela -> app sai
QTimer.singleShot(3000, app.quit)     # rede de segurança
app.exec()
print("mouse liberado:", w.mouse is None, "| config salva:",
      json.load(open(a.CFG)).get("interval_ms"))
