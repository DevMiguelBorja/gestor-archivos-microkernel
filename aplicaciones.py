"""Módulo Aplicaciones: proceso padre que controla instancias (procesos hijos).

Modo hijo:  python aplicaciones.py --hijo APP1 --padre <pid>
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time

from common import APP, GESTOR, GUI, atender, conectar

NUM_INSTANCIAS = 3
ARCHIVO = os.path.abspath(__file__)


class Aplicaciones:
    def __init__(self):
        self.canal = conectar(APP)
        self.instancias = {}
        self.candado = threading.RLock()
        self.salir = False

    # ----------------------------------------------------------- utilidades
    def log(self, texto):
        self.canal.enviar({"cmd": "send", "src": APP, "dst": GESTOR, "msg": "Log: " + texto})

    def estado_json(self):
        with self.candado:
            return json.dumps({n: {"pid": i["proc"].pid, "estado": i["estado"]}
                               for n, i in self.instancias.items()})

    def notificar(self, destino=GUI):
        self.canal.enviar({"cmd": "send", "src": APP, "dst": destino, "tipo": "apps",
                           "msg": self.estado_json()})

    def lanzar_instancias(self):
        for i in range(1, NUM_INSTANCIAS + 1):
            nombre = f"APP{i}"
            proc = subprocess.Popen([sys.executable, ARCHIVO, "--hijo", nombre,
                                     "--padre", str(os.getpid())])
            self.instancias[nombre] = {"proc": proc, "estado": "activa"}
        pids = ", ".join(f"{n}={i['proc'].pid}" for n, i in self.instancias.items())
        self.log(f"Módulo Aplicaciones (PID {os.getpid()}) iniciado con instancias {pids}")
        self.notificar()

    def cerrar_instancia(self, nombre, motivo):
        with self.candado:
            inst = self.instancias.get(nombre)
            if inst is None:
                return False, f"La instancia '{nombre}' no existe"
            if inst["estado"] != "activa":
                return False, f"La instancia '{nombre}' ya estaba cerrada"
            inst["estado"] = "cerrada"
            proc = inst["proc"]
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        self.log(f"{nombre} (PID {proc.pid}) cerrada por {motivo}")
        return True, f"{nombre} cerrada"

    def cerrar_todas(self, motivo):
        cerradas = [n for n in list(self.instancias) if self.cerrar_instancia(n, motivo)[0]]
        return True, f"Instancias cerradas: {', '.join(cerradas) or 'ninguna'}"

    def monitor(self):
        """Detecta instancias cerradas de forma manual/externa y lo notifica al Gestor."""
        while not self.salir:
            cambio = False
            with self.candado:
                for nombre, inst in self.instancias.items():
                    if inst["estado"] == "activa" and inst["proc"].poll() is not None:
                        inst["estado"] = "cerrada"
                        self.log(f"{nombre} (PID {inst['proc'].pid}) cerrada de forma manual")
                        cambio = True
            if cambio:
                self.notificar()
            time.sleep(0.5)

    # ------------------------------------------------------------- mensajes
    def manejar(self, msg):
        cmd, texto, origen = msg.get("cmd"), msg.get("msg", ""), msg.get("src", GUI)
        if not isinstance(texto, str):
            return
        if cmd == "info":
            def accion():
                self.notificar(origen)
                with self.candado:
                    if texto in self.instancias:
                        i = self.instancias[texto]
                        return True, f"{texto}: PID={i['proc'].pid} estado={i['estado']}"
                    if texto == "halt":
                        det = [n for n, i in self.instancias.items() if i["estado"] != "activa"]
                        return True, f"Instancias detenidas: {', '.join(det) or 'ninguna'}"
                    if texto in ("todas", "all"):
                        return True, "; ".join(f"{n}: PID={i['proc'].pid} {i['estado']}"
                                               for n, i in self.instancias.items())
                return False, f"Información desconocida: {texto}"
            atender(self.canal, APP, msg, accion)
        elif cmd == "send" and texto.startswith("cerrar:"):
            objetivo = texto.split(":", 1)[1].strip()

            def accion():
                if objetivo == "padre":
                    ok, det = self.cerrar_todas("cierre del módulo Aplicaciones")
                    self.log("Módulo Aplicaciones cerrado (padre e instancias)")
                    self.salir = True
                    return ok, "Módulo Aplicaciones cerrado. " + det
                if objetivo == "todas":
                    ok, det = self.cerrar_todas("solicitud del usuario")
                else:
                    ok, det = self.cerrar_instancia(objetivo, "solicitud del usuario")
                self.notificar()
                return ok, det
            atender(self.canal, APP, msg, accion)

    def ejecutar(self):
        self.lanzar_instancias()
        threading.Thread(target=self.monitor, daemon=True).start()
        while not self.salir:
            msg = self.canal.recibir()
            if msg is None or msg.get("cmd") == "close":
                break
            self.manejar(msg)
        self.salir = True
        self.cerrar_todas("finalización del módulo")
        self.canal.cerrar()


def hijo(nombre, padre):
    """Instancia simulada de una aplicación: vive hasta que la cierren o muera el padre."""
    while True:
        time.sleep(0.5)
        if os.name != "nt" and os.getppid() != padre:
            break


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--hijo")
    ap.add_argument("--padre", type=int, default=0)
    args = ap.parse_args()
    if args.hijo:
        hijo(args.hijo, args.padre)
    else:
        Aplicaciones().ejecutar()
