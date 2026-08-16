"""Universalidade: vários mouses, cada um clonado com a SUA identidade.

Cria dois mouses falsos com vendor/product/nome diferentes, confere que ambos
aparecem na lista, e que trocar a seleção recria o device virtual com a
identidade certa — que é o que faz o KDE aplicar a sensibilidade daquele mouse.
"""
import fcntl, os, struct, subprocess, sys, time
from PySide6.QtWidgets import QApplication
import sys as _sys, os as _o
_sys.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
import wayclick as a
import os as _os                       # config isolada e limpa por teste
a.CFG = f"/tmp/autoclick-test-{_os.path.basename(__file__)}.json"
_os.path.exists(a.CFG) and _os.remove(a.CFG)

FAKES = [(b"Fake Brand X Mouse", 0x1111, 0x2222),
         (b"Fake Brand Y Trackball", 0x3333, 0x4444)]
fds = []
for name, vid, pid in FAKES:
    fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
    fcntl.ioctl(fd, a.UI_SET_EVBIT, a.EV_KEY); fcntl.ioctl(fd, a.UI_SET_EVBIT, a.EV_REL)
    for c in a.BTN.values():
        fcntl.ioctl(fd, a.UI_SET_KEYBIT, c)
    for r in (0, 1, 8):
        fcntl.ioctl(fd, a.UI_SET_RELBIT, r)
    os.write(fd, struct.pack("80s", name) + struct.pack("HHHH", 3, vid, pid, 0x99)
             + struct.pack("i", 0) + b"\x00" * 1024)
    fcntl.ioctl(fd, a.UI_DEV_CREATE)
    fds.append(fd)
time.sleep(0.6)

# nesta máquina os "mouses" de teste são uinput; deixa o app enxergá-los
a.is_mouse = a.is_mouse_any

app = QApplication(sys.argv)
w = a.App()
listed = [w.dev.itemText(i) for i in range(w.dev.count())]
print("mouses na UI:")
for x in listed:
    print("   ", x)
missing = [n.decode() for n, _, _ in FAKES if not any(n.decode() in x for x in listed)]
print("todos os falsos apareceram?", not missing, missing or "")


def ident_of_clone():
    node = os.path.basename(w.mouse.node)
    base = f"/sys/class/input/{node}/device/id"
    return tuple(open(f"{base}/{k}").read().strip() for k in ("vendor", "product"))


ok = True
for i, (name, vid, pid) in enumerate(FAKES):
    idx = next(j for j, t in enumerate(listed) if name.decode() in t)
    w.dev.setCurrentIndex(idx)
    got = ident_of_clone()
    want = (f"{vid:04x}", f"{pid:04x}")
    match = got == want and name in w.mouse.name
    ok &= match
    print(f"selecionou {name.decode():<24} -> clone vendor/product {got} "
          f"(esperado {want}) nome ok={name in w.mouse.name} {'OK' if match else 'FALHOU'}")

w.cleanup()
for fd in fds:
    fcntl.ioctl(fd, a.UI_DEV_DESTROY); os.close(fd)
print("RESULTADO:", "tudo ok" if ok and not missing else "FALHOU")
