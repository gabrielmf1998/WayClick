"""Cenário do usuário: Click e Keyboard macro DESMARCADOS, só a tecla
direcionada. O Start (e o atalho) têm que funcionar, e a primeira tecla tem que
sair na hora, sem esperar o intervalo inteiro.
"""
import sys as _sys, os as _o, time
_sys.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QLabel
import wayclick as a
a.AUTO_UPDATE_CHECK = False   # sem rede nos testes
import os as _os
a.CFG = f"/tmp/wayclick-test-{_os.path.basename(__file__)}.json"
_os.path.exists(a.CFG) and _os.remove(a.CFG)

app = QApplication(_sys.argv)

host = QLabel("HOST"); host.setAlignment(Qt.AlignCenter)
host.setStyleSheet("background:#402020;color:#fff;font-size:18px;")
host.setWindowTitle("WCHOST")
leaked = {"n": 0}
host.keyPressEvent = lambda e: leaked.__setitem__("n", leaked["n"] + 1)
host.resize(400, 100); host.move(60, 60); host.show()

target = QLabel("ALVO"); target.setAlignment(Qt.AlignCenter)
target.setStyleSheet("background:#204020;color:#fff;font-size:18px;")
target.setWindowTitle("WCALVO")
got = {"n": 0, "t": []}
target.keyPressEvent = lambda e: (got.__setitem__("n", got["n"] + 1),
                                  got["t"].append(time.perf_counter()))
target.resize(400, 100); target.move(60, 220); target.show()

w = a.App()
app.aboutToQuit.connect(w.cleanup)
w.sound.setChecked(False)
R = {}


def setup():
    w.click_box.setChecked(False)          # exatamente o que o usuário fez
    w.key_box.setChecked(False)
    wins = w.refresh_windows()
    tid = next((x["id"] for x in wins if x["title"] == "WCALVO"), None)
    hid = next((x["id"] for x in wins if x["title"] == "WCHOST"), None)
    if not (tid and hid):
        print("não achei as janelas de teste"); app.quit(); return
    idx = next((i for i in range(w.win_sel.count())
                if (w.win_sel.itemData(i) or {}).get("id") == tid), -1)
    w.win_sel.setCurrentIndex(idx)
    w.target_box.setChecked(True)
    w.win_key.set_key(a.KEYS["Space"], "Space")
    w.win_secs.setValue(2)
    w.delay.setValue(0)
    w.bridge.activate(hid)
    QTimer.singleShot(400, press_start)


def press_start():
    R["t0"] = time.perf_counter()
    w.btn.click()                          # botão Start, como o usuário aperta
    R["running"] = w.running
    R["warn"] = w.warn.text()[:60]
    # a primeira tecla tem que ter saído já, não daqui a 2 s
    R["first_ms"] = ((got["t"][0] - R["t0"]) * 1000) if got["t"] else None
    QTimer.singleShot(4600, stop)


def stop():
    R["hits"] = w.target_hits
    R["ms"] = w.target_ms
    w.btn.click()                          # Stop
    R["running_after"] = w.running
    QTimer.singleShot(1200, check)


def check():
    after = got["n"]
    QTimer.singleShot(1500, lambda: final(after))


def final(after):
    wins = w.bridge.windows()
    active = next((x["title"] for x in wins if x["active"]), "?")
    print(f"Start funcionou com só o alvo marcado: {R.get('running')}")
    print(f"aviso na tela: {R.get('warn') or '(nenhum)'}")
    print(f"primeira tecla em: {R.get('first_ms')} ms (intervalo era 2000 ms)")
    print(f"ciclos: {R.get('hits')} | teclas na ALVO: {got['n']} | "
          f"vazaram para a HOST: {leaked['n']}")
    print(f"parou de verdade no Stop: {got['n'] == after} "
          f"(running={R.get('running_after')})")
    print(f"foco no fim: {active} | roubo por ciclo: {R.get('ms', 0):.0f} ms")
    ok = (R.get("running") and R.get("first_ms") is not None
          and R["first_ms"] < 700 and R.get("hits", 0) >= 3
          and got["n"] == R.get("hits") and leaked["n"] == 0
          and got["n"] == after and active == "WCHOST")
    print("RESULTADO:", "tudo ok" if ok else "FALHOU")
    app.quit()


QTimer.singleShot(1500, setup)
QTimer.singleShot(25000, app.quit)
app.exec()
