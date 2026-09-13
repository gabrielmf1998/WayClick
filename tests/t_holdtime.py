"""A tecla e o clique ficam baixos tempo suficiente para um jogo perceber?

Regressão do bug que fazia macro e autoclick "não funcionarem" em jogo: com
press e release colados, quem consulta estado por quadro nunca via nada.

Jogo não escuta evento, ele pergunta "espaço está apertado?" a cada quadro.
Esta janela faz as duas contas: eventos recebidos, e quantos quadros de 16 ms
(60 fps) pegaram a tecla efetivamente para baixo.
"""
import sys, time
import os as _o
sys.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QLabel
import wayclick as a
a.AUTO_UPDATE_CHECK = False   # sem rede nos testes
a.SYSTEM_TRAY = False         # sem icone de bandeja nos testes

app = QApplication(sys.argv)
w = QLabel("medindo duração da tecla")
w.setAlignment(Qt.AlignCenter)
w.setStyleSheet("background:#123;color:#fff;font-size:20px;")
w.showFullScreen()

st = {"down": False, "events": 0, "frames_down": 0, "frames": 0}
w.keyPressEvent = lambda e: (st.__setitem__("down", True),
                             st.__setitem__("events", st["events"] + 1)) \
    if e.key() == Qt.Key_Space and not e.isAutoRepeat() else None
w.keyReleaseEvent = lambda e: st.__setitem__("down", False) \
    if e.key() == Qt.Key_Space and not e.isAutoRepeat() else None


w.mousePressEvent = lambda e: (st.__setitem__("down", True),
                               st.__setitem__("events", st["events"] + 1))
w.mouseReleaseEvent = lambda e: st.__setitem__("down", False)


def frame():                       # o "loop do jogo", 60 fps
    st["frames"] += 1
    if st["down"]:
        st["frames_down"] += 1


poll = QTimer(); poll.setInterval(16); poll.timeout.connect(frame); poll.start()

kb = a.VirtualKeyboard()
R = {}


def run(label, engine, secs=2.0):
    st.update(events=0, frames_down=0, frames=0)
    engine.start()
    end = time.perf_counter() + secs
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.002)
    engine.stop()
    time.sleep(0.15)
    app.processEvents()
    R[label] = (st["events"], st["frames_down"], st["frames"])
    print(f"{label:<26} eventos={st['events']:<3} "
          f"quadros com a tecla baixa={st['frames_down']:<3} de {st['frames']}",
          flush=True)


def go():
    mouse = a.VirtualMouse()          # clique primeiro: precisa do cursor sobre
    run("Clicker 100 ms", a.Clicker(mouse, 100.0, a.BTN["Left"]))
    mouse.close()
    run("KeyMacro 200 ms", a.KeyMacro(kb, 200.0, a.KEYS["Space"]))
    run("KeyMacro 50 ms", a.KeyMacro(kb, 50.0, a.KEYS["Space"]))
    kb.close()
    ok = all(v[1] > 0 for v in R.values())
    print("RESULTADO:", "tudo ok" if ok else "FALHOU (0 quadros = jogo nao ve)")
    app.quit()


QTimer.singleShot(1200, go)
QTimer.singleShot(30000, app.quit)
app.exec()
