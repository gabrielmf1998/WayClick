"""Injeção real numa janela X11: sem foco, e depois minimizada.

Este é o requisito que o modo "dar foco por um instante" nunca atende. O alvo
roda em processo separado de propósito: no mesmo processo o Qt roteia o evento
para a janela que tem foco e o teste mente.
"""
import os as _o, subprocess, sys as _sys, time
_sys.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QLabel
import wayclick as a
a.CFG = f"/tmp/wayclick-test-{_o.path.basename(__file__)}.json"
_o.path.exists(a.CFG) and _o.remove(a.CFG)

VICTIM = r'''
import os, sys
os.environ["QT_QPA_PLATFORM"] = "xcb"
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QLabel
app = QApplication(sys.argv)
w = QLabel("ALVO X11")
w.setAlignment(Qt.AlignCenter)
w.setStyleSheet("background:#204020;color:#fff;font-size:18px;")
w.setWindowTitle("WCX11VICTIM")
n = {"k": 0}
w.keyPressEvent = lambda e: n.__setitem__("k", n["k"] + 1) if e.key() == Qt.Key_Space else None
w.resize(360, 90); w.move(760, 40); w.show()
print("READY", flush=True)
def dump(tag):
    print(tag, n["k"], flush=True)
QTimer.singleShot(6000, lambda: dump("SEM_FOCO"))
QTimer.singleShot(6500, lambda: (w.showMinimized(), print("MIN", flush=True)))
QTimer.singleShot(13000, lambda: (dump("MINIMIZADA"), app.quit()))
app.exec()
'''

open("/tmp/wc_victim.py", "w").write(VICTIM)
victim = subprocess.Popen([_sys.executable, "/tmp/wc_victim.py"],
                          stdout=subprocess.PIPE, text=True, bufsize=1)
assert victim.stdout.readline().strip() == "READY"

app = QApplication(_sys.argv)
host = QLabel("HOST com foco")
host.setAlignment(Qt.AlignCenter)
host.setStyleSheet("background:#402020;color:#fff;font-size:18px;")
host.setWindowTitle("WCHOSTX")
leak = {"k": 0}
host.keyPressEvent = lambda e: leak.__setitem__("k", leak["k"] + 1)
host.resize(360, 90); host.move(760, 200); host.show()

w = a.App()
app.aboutToQuit.connect(w.cleanup)
w.sound.setChecked(False)
R = {}


def pump(secs):
    end = time.perf_counter() + secs
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.004)


def go():
    w.click_box.setChecked(False)
    w.key_box.setChecked(False)
    wins = w.refresh_windows()
    idx = next((i for i in range(w.win_sel.count())
                if "WCX11VICTIM" in w.win_sel.itemText(i)), -1)
    if idx < 0:
        print("não achei a janela alvo na lista:",
              [w.win_sel.itemText(i) for i in range(w.win_sel.count())])
        app.quit(); return
    w.win_sel.setCurrentIndex(idx)
    R["label"] = w.win_sel.itemText(idx)
    R["xid"] = (w.win_sel.currentData() or {}).get("xid")
    hid = next((x["id"] for x in wins if x["title"] == "WCHOSTX"), None)
    w.bridge.activate(hid)                    # foco fica na HOST o tempo todo
    pump(0.5)
    R["active"] = next((x["title"] for x in w.bridge.windows() if x["active"]), "?")

    w.win_key.set_key(a.KEYS["Space"], "Space")
    w.win_secs.setValue(1)
    w.delay.setValue(0)
    w.target_box.setChecked(True)
    w.btn.click()
    pump(3.2)
    R["hits_sem_foco"] = w.target_hits
    R["active_meio"] = next((x["title"] for x in w.bridge.windows()
                             if x["active"]), "?")
    pump(5.0)                                  # o alvo se minimiza em 6,5 s
    R["hits_total"] = w.target_hits
    w.btn.click()
    R["ms"] = w.target_ms
    QTimer.singleShot(300, done)


def done():
    victim.wait(timeout=10)
    out = dict(l.split() for l in victim.stdout.read().strip().splitlines()
               if " " in l)
    print(f"alvo na lista        : {R.get('label')}")
    print(f"tem id X11           : {hex(R['xid']) if R.get('xid') else 'NAO'}")
    print(f"foco durante o teste : {R.get('active')} -> {R.get('active_meio')}")
    print(f"ciclos disparados    : {R.get('hits_total')} "
          f"({R.get('ms', 0):.0f} ms cada)")
    print(f"recebidas SEM FOCO   : {out.get('SEM_FOCO')}")
    print(f"recebidas MINIMIZADA : {out.get('MINIMIZADA')} (acumulado)")
    print(f"vazaram para a HOST  : {leak['k']}")
    sem = int(out.get("SEM_FOCO", -1))
    tot = int(out.get("MINIMIZADA", -1))
    ok = (R.get("xid") and sem >= 2 and tot > sem and leak["k"] == 0
          and R.get("active_meio") == "WCHOSTX")
    print("RESULTADO:", "tudo ok" if ok else "FALHOU")
    app.quit()


QTimer.singleShot(1500, go)
QTimer.singleShot(40000, app.quit)
app.exec()
