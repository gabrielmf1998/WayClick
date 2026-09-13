"""Prova de que o clone herda a config de ponteiro: quem responde é o KWin."""
import json, os, subprocess, sys, time
import sys as _sys, os as _o
_sys.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
import wayclick as a
a.AUTO_UPDATE_CHECK = False   # sem rede nos testes
import os as _os                       # config isolada e limpa por teste
a.CFG = f"/tmp/autoclick-test-{_os.path.basename(__file__)}.json"
_os.path.exists(a.CFG) and _os.remove(a.CFG)


def kwin(sysname, prop):
    r = subprocess.run(
        ["gdbus", "call", "--session", "-d", "org.kde.KWin",
         "-o", f"/org/kde/KWin/InputDevice/{sysname}",
         "-m", "org.freedesktop.DBus.Properties.Get",
         "org.kde.KWin.InputDevice", prop],
        capture_output=True, text=True)
    return r.stdout.strip() or r.stderr.strip()


def probe(label, mouse):
    sysname = os.path.basename(mouse.node) if mouse.node else "?"
    time.sleep(0.8)                       # KWin precisa enxergar o device novo
    print(f"{label}")
    print(f"   nome     : {mouse.name.decode(errors='replace')}")
    print(f"   sysname  : {sysname}")
    for p in ("name", "pointerAcceleration", "pointerAccelerationProfileFlat"):
        print(f"   {p:<32}: {kwin(sysname, p)}")


print("== mouse real (event3) ==")
for p in ("name", "pointerAcceleration", "pointerAccelerationProfileFlat"):
    print(f"   {p:<32}: {kwin('event3', p)}")

mice, _ = a.open_devices(a.is_mouse)
print("\nmouses vistos:", [a.dev_name(f.fileno()).decode() for f in mice])

clone = a.VirtualMouse(mice[0].fileno())
probe("\n== nosso device CLONADO ==", clone)
print("   nós nos excluímos do scan?",
      clone.node not in [f.name for f in a.open_devices(a.is_mouse)[0]])
clone.close()

plain = a.VirtualMouse()
probe("\n== device GENÉRICO (como era antes) ==", plain)
plain.close()
for f in mice:
    f.close()
