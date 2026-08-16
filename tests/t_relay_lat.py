"""Latência do relay do mouse enquanto o WayClick roda a 10 kHz.

Mede no nível do evdev: escreve no mouse falso e cronometra até o evento sair
no nosso device virtual. É esse atraso que o usuário sentiria como travada do
ponteiro enquanto segura o botão.
"""
import fcntl, glob, os, select, struct, sys, time
import sys as _sys, os as _o
_sys.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
import wayclick as a
import os as _os                       # config isolada e limpa por teste
a.CFG = f"/tmp/autoclick-test-{_os.path.basename(__file__)}.json"
_os.path.exists(a.CFG) and _os.remove(a.CFG)

FAKE = b"wayclick-fake-physical-mouse"

fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
fcntl.ioctl(fd, a.UI_SET_EVBIT, a.EV_KEY); fcntl.ioctl(fd, a.UI_SET_EVBIT, a.EV_REL)
for c in a.BTN.values():
    fcntl.ioctl(fd, a.UI_SET_KEYBIT, c)
for r in (0, 1, 8):
    fcntl.ioctl(fd, a.UI_SET_RELBIT, r)
os.write(fd, struct.pack("80s", FAKE) + struct.pack("HHHH", 3, 0xAAA, 0xBBB, 1)
         + struct.pack("i", 0) + b"\x00" * 1024)
fcntl.ioctl(fd, a.UI_DEV_CREATE)
time.sleep(0.5)

a.is_mouse = a.is_mouse_any = lambda f: FAKE in a.dev_name(f.fileno())
vm = a.VirtualMouse()

# nó de leitura do NOSSO device virtual, pra ver o que sai do relay
out = None
for p in sorted(glob.glob("/dev/input/event*")):
    f = open(p, "rb", buffering=0)
    if a.DEV_NAME in a.dev_name(f.fileno()):
        out = f
        break
    f.close()
assert out, "não achei o device virtual"
os.set_blocking(out.fileno(), False)

hold = a.MouseHold(vm, 0x110)
assert hold.start(), "grab falhou"
time.sleep(0.3)


def measure(label, n=300):
    lat = []
    while select.select([out], [], [], 0)[0]:
        out.read(4096)
    for _ in range(n):
        t0 = time.perf_counter()
        os.write(fd, struct.pack("llHHi", 0, 0, a.EV_REL, 0, 3)
                 + struct.pack("llHHi", 0, 0, a.EV_SYN, 0, 0))
        while True:
            if select.select([out], [], [], 0.5)[0]:
                data = out.read(4096)
                if any(struct.unpack_from("llHHi", data, i)[2] == a.EV_REL
                       for i in range(0, len(data) - 23, 24)):
                    break
            else:
                break
        lat.append((time.perf_counter() - t0) * 1000)
        time.sleep(0.001)
    lat.sort()
    print(f"{label}: mediana {lat[len(lat)//2]:.2f} ms | "
          f"p99 {lat[int(len(lat)*0.99)]:.2f} ms | max {lat[-1]:.2f} ms")


measure("relay parado          ")
clicker = a.Clicker(vm, 0.1, 0x110)
clicker.start()
time.sleep(0.2)
measure("relay + 10.000 c/s    ")
print(f"cliques emitidos durante a medição: {clicker.count:,}")
clicker.stop()
hold.stop(); vm.close(); out.close()
fcntl.ioctl(fd, a.UI_DEV_DESTROY); os.close(fd)
