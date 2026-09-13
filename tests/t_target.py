"""E2E da injeção direcionada (beta) junto com o clique ligado.

Tecla cai na janela alvo, não na do host, enquanto o autoclick roda.

Abre duas janelas — uma fingindo ser a que o usuário está usando, outra o alvo —
liga a função no WayClick e confere onde as teclas caíram, se o foco voltou e
quanto tempo cada ciclo levou.
"""
import sys as _sys, os as _o, time
_sys.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QLabel
import wayclick as a
a.AUTO_UPDATE_CHECK = False   # sem rede nos testes
a.SYSTEM_TRAY = False         # sem icone de bandeja nos testes
import os as _os                       # config isolada e limpa por teste
a.CFG = f"/tmp/wayclick-test-{_os.path.basename(__file__)}.json"
_os.path.exists(a.CFG) and _os.remove(a.CFG)

app = QApplication(_sys.argv)

host = QLabel("HOST — janela que o usuário está usando")
host.setAlignment(Qt.AlignCenter)
host.setStyleSheet("background:#402020;color:#fff;font-size:18px;")
host.setWindowTitle("WCHOST")
leaked = {"n": 0}
host.keyPressEvent = lambda e: leaked.__setitem__("n", leaked["n"] + 1)
host.resize(430, 110); host.move(60, 60); host.show()

target = QLabel("ALVO — deve receber as teclas")
target.setAlignment(Qt.AlignCenter)
target.setStyleSheet("background:#204020;color:#fff;font-size:18px;")
target.setWindowTitle("WCALVO")
got = {"n": 0}
target.keyPressEvent = lambda e: got.__setitem__("n", got["n"] + 1)
target.resize(430, 110); target.move(60, 240); target.show()

w = a.App()
app.aboutToQuit.connect(w.cleanup)
w.sound.setChecked(False)
R = {}


def setup():
    wins = w.refresh_windows()
    R["seen"] = [x["title"] for x in wins]
    tid = next((x["id"] for x in wins if x["title"] == "WCALVO"), None)
    hid = next((x["id"] for x in wins if x["title"] == "WCHOST"), None)
    R["found"] = bool(tid and hid)
    if not R["found"]:
        print("não achei as janelas de teste:", R["seen"]); app.quit(); return
    idx = next((i for i in range(w.win_sel.count())
                if (w.win_sel.itemData(i) or {}).get("id") == tid), -1)
    w.win_sel.setCurrentIndex(idx)
    w.win_key.set_key(a.KEYS["Space"], "Space")
    w.win_secs.setValue(1)
    w.click_box.setChecked(True)      # alvo + clique juntos
    w.bridge.activate(hid)                  # usuário "usando" a host
    QTimer.singleShot(400, start)


def start():
    R["t0"] = time.perf_counter()
    w.target_box.setChecked(True)
    w.delay.setValue(0)
    w.btn.click()                     # agora o Start controla a engine
    QTimer.singleShot(3600, stop)


def stop():
    R["elapsed"] = time.perf_counter() - R["t0"]
    R["hits"] = w.target_hits
    R["ms"] = w.target_ms
    w.btn.click()
    QTimer.singleShot(500, check)


def check():
    wins = w.bridge.windows()
    R["active_end"] = next((x["title"] for x in wins if x["active"]), "?")
    print(f"janelas vistas pelo KWin: {len(R['seen'])}")
    print(f"ciclos em {R.get('elapsed', 0):.1f}s a cada 1s: {R.get('hits')}")
    print(f"teclas na ALVO: {got['n']}  |  vazaram para a HOST: {leaked['n']}")
    print(f"janela ativa no fim: {R['active_end']} "
          f"(com clique ligado o foco é imprevisível, não é critério)")
    print(f"foco roubado por ciclo: {R.get('ms', 0):.0f} ms")
    ok = (R.get("hits", 0) >= 3 and got["n"] == R.get("hits")
          and leaked["n"] == 0)
    print("RESULTADO:", "tudo ok" if ok else "FALHOU")
    app.quit()


QTimer.singleShot(1500, setup)
QTimer.singleShot(20000, app.quit)
app.exec()
