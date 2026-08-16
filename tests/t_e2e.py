import os, fcntl, struct, time, sys, glob, subprocess, threading
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QLabel

before = set(glob.glob('/dev/input/event*'))
fd = os.open('/dev/uinput', os.O_WRONLY | os.O_NONBLOCK)
fcntl.ioctl(fd, 0x40045564, 1); fcntl.ioctl(fd, 0x40045564, 2)
for c in (0x110, 0x111, 0x112): fcntl.ioctl(fd, 0x40045565, c)
for r in (0, 1): fcntl.ioctl(fd, 0x40045566, r)
os.write(fd, struct.pack("80s", b"wayclick-virtual-mouse")
         + struct.pack("HHHH", 3, 0x1234, 0x5678, 1) + struct.pack("i", 0) + b"\x00" * 1024)
fcntl.ioctl(fd, 0x5501)
time.sleep(0.3)
new = set(glob.glob('/dev/input/event*')) - before
print("device:", new, flush=True)
for n in new:
    parent = os.path.dirname(os.path.realpath('/sys/class/input/' + os.path.basename(n)))
    print(subprocess.run(['udevadm', 'info', parent], capture_output=True, text=True
                         ).stdout.strip().split('\n')[-3:], flush=True)

def emit(t, c, v): os.write(fd, struct.pack("llHHi", 0, 0, t, c, v))
def click():
    emit(1, 0x110, 1); emit(0, 0, 0); emit(1, 0x110, 0); emit(0, 0, 0)
def move(dx, dy):
    emit(2, 0, dx); emit(2, 1, dy); emit(0, 0, 0)

app = QApplication(sys.argv)
w = QLabel("TESTE AUTOCLICK - fecha sozinho em 6s")
w.setAlignment(Qt.AlignCenter)
w.setStyleSheet("background:#222;color:#fff;font-size:28px;")
count = {"n": 0, "mv": 0}
w.mousePressEvent = lambda e: (count.__setitem__("n", count["n"] + 1), w.setText(f"cliques: {count['n']}"))
w.mouseMoveEvent = lambda e: count.__setitem__("mv", count["mv"] + 1)
w.setMouseTracking(True)
w.showFullScreen()

def fire():
    pass  # sem move
    for _ in range(5):
        click(); time.sleep(0.2)

QTimer.singleShot(50, lambda: threading.Thread(target=fire, daemon=True).start())
QTimer.singleShot(5000, app.quit)
app.exec()
print("CLIQUES RECEBIDOS:", count["n"], "MOVES:", count["mv"], flush=True)
fcntl.ioctl(fd, 0x5502); os.close(fd)
