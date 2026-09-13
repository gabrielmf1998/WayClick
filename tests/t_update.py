"""Aviso de versão nova: comparação, cache e a barra.

A parte que decide (comparar tag com a versão local, avisar toda abertura,
guardar o resultado) não depende de rede e é o que este teste garante. A
consulta de verdade aos dois repositórios é feita no fim e apenas relatada —
CI sem rede não deve reprovar por isso.
"""
import json, os, sys, time
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
import sys as _sys, os as _o
_sys.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
import wayclick as a
a.AUTO_UPDATE_CHECK = False   # sem rede nos testes

TMP = "/tmp/autoclick-test-update"
os.makedirs(TMP, exist_ok=True)
app = QApplication(sys.argv)
R = {}

# --- comparação de versões -------------------------------------------------
R["same"] = a.newer_version(f"v{a.VERSION}")        # igual: não avisa
R["newer"] = a.newer_version("v99.0.0")             # nova: avisa
R["older"] = a.newer_version("v0.1.0")              # repo atrás: não avisa
R["junk"] = a.newer_version("")                     # sem resposta: não avisa


def app_with(cfg_name, cfg):
    """Cada App abre uma janela; quem chama fecha, senao elas se acumulam."""
    a.CFG = os.path.join(TMP, cfg_name)
    json.dump(cfg, open(a.CFG, "w"))
    return a.App()


# --- nada novo: a barra fica escondida ------------------------------------
w = app_with("same.json", {"update_tag": f"v{a.VERSION}",
                           "update_last": time.time()})
w.show()
app.processEvents()
R["bar_same"] = w.update_bar.isVisible()
w.cleanup(); w.close()

# --- versão nova em cache: avisa na abertura, sem tocar na rede -----------
w = app_with("new.json", {"update_tag": "v99.0.0", "update_last": time.time(),
                          "update_url": "https://example.invalid/r"})
w.show()
app.processEvents()
R["bar_new"] = w.update_bar.isVisible()
R["text"] = w.update_lbl.text()
R["y_with"] = w.tabs.y()
w._dismiss_update()
app.processEvents()
R["bar_dismissed"] = w.update_bar.isVisible()
R["y_without"] = w.tabs.y()
w.cleanup(); w.close()
saved = json.load(open(a.CFG))
R["kept_tag"] = saved.get("update_tag")
R["kept_flag"] = saved.get("update_check")

# --- o × vale só para a sessão: reabrir volta a avisar --------------------
w = a.App()
w.show()
app.processEvents()
R["bar_reopened"] = w.update_bar.isVisible()
w.cleanup(); w.close()

# --- desligado nas configurações: não consulta ----------------------------
w = app_with("off.json", {"update_check": False})
R["auto_off"] = w.update_auto
w.cleanup(); w.close()

print(f"compara: igual={R['same']!r} nova={R['newer']!r} "
      f"antiga={R['older']!r} vazia={R['junk']!r}")
print(f"barra: sem novidade={R['bar_same']} com novidade={R['bar_new']} "
      f"apos x={R['bar_dismissed']} reabrindo={R['bar_reopened']}")
print(f"       texto {R['text']!r}")
print(f"layout: tabs em y={R['y_with']} com a barra, y={R['y_without']} sem")
print(f"config: tag guardada {R['kept_tag']!r}, "
      f"checagem automatica {R['kept_flag']} / desligavel {not R['auto_off']}")
ok = (R["same"] == "" and R["newer"] == "v99.0.0" and R["older"] == ""
      and R["junk"] == ""
      and R["bar_same"] is False and R["bar_new"] is True
      and R["bar_dismissed"] is False and R["bar_reopened"] is True
      and R["y_without"] < R["y_with"]          # esconder colapsa o espaco
      and R["kept_tag"] == "v99.0.0" and R["kept_flag"] is True
      and R["auto_off"] is False)
print("RESULTADO:", "tudo ok" if ok else "FALHOU")

# --- consulta real, so relatada -------------------------------------------
live = {}
u = a.UpdateCheck()
u.done.connect(lambda t, l: (live.update(tag=t, link=l), app.quit()))
u.start()
QTimer.singleShot(12000, app.quit)
app.exec()
print("rede:", f"ultima release {live.get('tag')!r}" if live.get("tag")
      else "sem resposta (offline?) — nao reprova o teste")
