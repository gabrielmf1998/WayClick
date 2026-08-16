"""O compositor entrega cliques injetados enquanto OUTRO device segura o botão?"""
import fcntl, os, struct, sys, threading, time
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QLabel


def make_mouse(name):
    fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
    fcntl.ioctl(fd, 0x40045564, 1); fcntl.ioctl(fd, 0x40045564, 2)
    for c in (0x110, 0x111, 0x112):
        fcntl.ioctl(fd, 0x40045565, c)
    for r in (0, 1):
        fcntl.ioctl(fd, 0x40045566, r)
    os.write(fd, struct.pack("80s", name.encode())
             + struct.pack("HHHH", 3, 0x1234, 0x5678, 1) + struct.pack("i", 0)
             + b"\x00" * 1024)
    fcntl.ioctl(fd, 0x5501)
    return fd


def ev(fd, t, c, v):
    os.write(fd, struct.pack("llHHi", 0, 0, t, c, v))


def btn(fd, v):
    ev(fd, 1, 0x110, v); ev(fd, 0, 0, 0)


phys = make_mouse("fake-physical-mouse")
auto = make_mouse("wayclick-virtual-mouse")
time.sleep(0.6)

app = QApplication(sys.argv)
w = QLabel("probe"); w.setAlignment(Qt.AlignCenter)
w.setStyleSheet("background:#203;color:#fff;font-size:24px;")
log = {"press": 0, "release": 0}
w.mousePressEvent = lambda e: log.__setitem__("press", log["press"] + 1)
w.mouseReleaseEvent = lambda e: log.__setitem__("release", log["release"] + 1)
w.showFullScreen()


def run():
    time.sleep(0.8)
    btn(phys, 1)                       # usuário SEGURA o botão físico
    time.sleep(0.2)
    base = dict(log)
    for _ in range(20):                # WayClick injeta durante o hold
        btn(auto, 1); btn(auto, 0)
        time.sleep(0.02)
    time.sleep(0.2)
    during = {k: log[k] - base[k] for k in log}
    btn(phys, 0)                       # usuário solta
    time.sleep(0.3)
    print(f"antes do hold: {base}")
    print(f"durante o hold (20 cliques injetados): {during}")
    print(f"total no fim: {log}")
    QTimer.singleShot(0, app.quit)


threading.Thread(target=run, daemon=True).start()
QTimer.singleShot(6000, app.quit)
app.exec()
for fd in (phys, auto):
    fcntl.ioctl(fd, 0x5502); os.close(fd)
