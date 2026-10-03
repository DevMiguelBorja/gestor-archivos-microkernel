"""Kernel (componente de control) del sistema MicroKernel.

Etapas: 1) inicialización  2) ejecución (enrutamiento de mensajes)  3) finalización.
Uso:  python kernel.py            (modo automático: lanza Gestor, Aplicaciones y GUI)
      python kernel.py --manual   (modo manual: solo el kernel; ejecuta los demás módulos tú mismo)
      python kernel.py --headless (no espera ni lanza la GUI; útil para pruebas automáticas)
"""
import os
import socket
import subprocess
import sys
import threading
import time

from common import (APP, ERR, GESTOR, GUI, HOST, KERNEL, MENSAJES_COD, MODULOS,
                    OCUPADO, PORT, PROCESADO, Canal)

DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = {GESTOR: "gestor_archivos.py", APP: "aplicaciones.py", GUI: "gui.py"}


class Kernel:
    def __init__(self, headless=False, manual=False):
        self.headless = headless
        self.manual = manual
        self.canales = {}
        self.estados = {m: "cerrado" for m in MODULOS}
        self.procesos = {}
        self.candado = threading.RLock()
        self.terminando = False
        self.fin = threading.Event()
        self.servidor = None

    # ---------------------------------------------------------- utilidades
    def enviar(self, destino, mensaje):
        with self.candado:
            canal = self.canales.get(destino)
        return canal.enviar(mensaje) if canal else False

    def registrar_log(self, texto):
        self.enviar(GESTOR, {"cmd": "send", "src": KERNEL, "dst": GESTOR, "msg": "Log: " + texto})

    def difundir_estado(self):
        with self.candado:
            estado = dict(self.estados)
        estado[KERNEL] = "activo"
        self.enviar(GUI, {"cmd": "status", "src": KERNEL, "dst": GUI, "msg": estado})

    def lanzar(self, nombre):
        ruta = os.path.join(DIR, SCRIPTS[nombre])
        self.procesos[nombre] = subprocess.Popen([sys.executable, ruta], cwd=DIR)

    # ------------------------------------------------ Etapa 1: inicialización
    def iniciar(self):
        self.servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.servidor.bind((HOST, PORT))
        self.servidor.listen(8)
        threading.Thread(target=self._aceptar, daemon=True).start()

        esperados = [GESTOR, APP] + ([] if self.headless else [GUI])
        if self.manual:
            print("[kernel] Modo manual: esperando que ejecutes cada módulo en otra terminal:", flush=True)
            for nombre in esperados:
                print(f"           python {SCRIPTS[nombre]}", flush=True)
            while not self.terminando:
                with self.candado:
                    if all(n in self.canales for n in esperados):
                        break
                time.sleep(0.2)
        else:
            for nombre in esperados:
                self.lanzar(nombre)
            limite = time.time() + 20
            while time.time() < limite:
                with self.candado:
                    if all(n in self.canales for n in esperados):
                        break
                time.sleep(0.1)
            else:
                print("[kernel] ERROR: no todos los módulos quedaron listos", flush=True)
                self.terminar()
                return
        print("[kernel] Inicialización completa: módulos listos ->", ", ".join(esperados), flush=True)
        self.registrar_log("Kernel: sistema iniciado, módulos listos")

    def _aceptar(self):
        while not self.terminando:
            try:
                conn, _ = self.servidor.accept()
            except OSError:
                break
            threading.Thread(target=self._cliente, args=(conn,), daemon=True).start()

    def _cliente(self, conn):
        canal = Canal(conn)
        hola = canal.recibir()
        if not hola or hola.get("cmd") != "ready" or hola.get("src") not in MODULOS:
            canal.cerrar()
            return
        nombre = hola["src"]
        with self.candado:
            duplicado = nombre in self.canales
            if not duplicado:
                self.canales[nombre] = canal
                self.estados[nombre] = "listo"
        if duplicado:
            print(f"[kernel] {nombre} ya está conectado; se rechaza la segunda instancia", flush=True)
            canal.cerrar()
            return
        print(f"[kernel] {nombre} listo", flush=True)
        self.difundir_estado()
        try:
            while True:
                msg = canal.recibir()
                if msg is None:
                    break
                self.enrutar(nombre, msg)
        finally:
            with self.candado:
                if self.canales.get(nombre) is canal:
                    del self.canales[nombre]
                    self.estados[nombre] = "cerrado"
            canal.cerrar()
            if not self.terminando:
                print(f"[kernel] {nombre} se desconectó", flush=True)
                self.registrar_log(f"Kernel: módulo {nombre} desconectado")
                self.difundir_estado()

    # -------------------------------------------------- Etapa 2: ejecución
    def enrutar(self, origen, msg):
        if "codterm" in msg:
            self._resultado(origen, msg)
            return
        cmd, dst = msg.get("cmd"), msg.get("dst")
        msg["src"] = origen
        if cmd == "stop":
            self.registrar_log(f"{origen} solicitó detener el sistema: {msg.get('msg', '')}")
            threading.Thread(target=self.terminar, daemon=True).start()
        elif dst == KERNEL:
            self._comando_kernel(origen, msg)
        elif cmd in ("send", "info"):
            if dst not in self.canales:
                self._error(origen, f"Destino '{dst}' no disponible")
                return
            self._log_transaccion(origen, msg)
            self.enviar(dst, msg)
        elif cmd not in (None, "ready"):
            self._error(origen, f"Comando desconocido: {cmd}")

    def _error(self, origen, detalle):
        self.enviar(origen, {"cmd": "result", "codterm": ERR, "msg": MENSAJES_COD[ERR],
                             "src": KERNEL, "dst": origen, "detalle": detalle})

    def _log_transaccion(self, origen, msg):
        texto = msg.get("msg")
        if msg.get("auto") or msg.get("dst") == GUI or not isinstance(texto, str):
            return
        if texto.startswith("Log:") or origen == GESTOR:
            return
        self.registrar_log(f"{origen}->{msg.get('dst')} [{msg.get('cmd')}] {texto}")

    def _resultado(self, origen, msg):
        cod = msg["codterm"]
        with self.candado:
            self.estados[origen] = "ocupado" if cod == OCUPADO else "listo"
        salida = dict(msg)
        salida.update({"cmd": "result", "src": origen, "dst": GUI})
        self.enviar(GUI, salida)
        self.difundir_estado()

    def _comando_kernel(self, origen, msg):
        cmd, objetivo = msg.get("cmd"), msg.get("msg")
        if cmd == "launch" and objetivo in (GESTOR, APP):
            if self.manual:
                self._error(origen, f"Modo manual: ejecuta 'python {SCRIPTS[objetivo]}' en otra terminal")
                return
            if objetivo in self.canales:
                self._error(origen, f"{objetivo} ya está activo")
                return
            self.lanzar(objetivo)
            self.registrar_log(f"Kernel: {objetivo} lanzado desde {origen}")
            self.enviar(origen, {"cmd": "result", "codterm": PROCESADO, "msg": "OK",
                                 "src": KERNEL, "dst": origen, "detalle": f"{objetivo} lanzado"})
        elif cmd == "info" and objetivo == "estado":
            self.difundir_estado()
        else:
            self._error(origen, f"Solicitud no válida al kernel: {cmd} {objetivo}")

    # ------------------------------------------------ Etapa 3: finalización
    def terminar(self):
        with self.candado:
            if self.terminando:
                return
            self.terminando = True
        self.registrar_log("Kernel: sistema detenido, cerrando módulos")
        time.sleep(0.4)
        with self.candado:
            canales = list(self.canales.items())
        for nombre, canal in canales:
            canal.enviar({"cmd": "close", "src": KERNEL, "dst": nombre, "msg": "cerrar"})
        limite = time.time() + 5
        for proc in self.procesos.values():
            try:
                proc.wait(timeout=max(0.1, limite - time.time()))
            except subprocess.TimeoutExpired:
                proc.terminate()
        try:
            self.servidor.close()
        except OSError:
            pass
        print("[kernel] Sistema finalizado", flush=True)
        self.fin.set()


def main():
    kernel = Kernel(headless="--headless" in sys.argv, manual="--manual" in sys.argv)
    try:
        kernel.iniciar()
        kernel.fin.wait()
    except KeyboardInterrupt:
        kernel.terminar()


if __name__ == "__main__":
    main()