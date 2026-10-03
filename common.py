"""Utilidades compartidas por todos los módulos del sistema MicroKernel.

Protocolo: mensajes JSON, uno por línea, sobre sockets TCP locales.
  - Comandos (módulo -> kernel -> módulo):  {"cmd": "send|info|stop|launch", "src": ..., "dst": ..., "msg": ...}
  - Respuestas de estado (módulo -> kernel): {"codterm": 0|1|2, "msg": "OK"|"0"|"Err", ...}
"""
import datetime
import json
import os
import random
import socket
import threading
import time

HOST = "127.0.0.1"
PORT = int(os.environ.get("MK_PORT", "5050"))

# Nombres de los módulos
KERNEL = "kernel"
GUI = "GUI"
GESTOR = "GestorArc"
APP = "APP"
MODULOS = (GESTOR, APP, GUI)

# Códigos de terminación (sección 4 de la actividad)
PROCESADO, OCUPADO, ERR = 0, 1, 2
MENSAJES_COD = {PROCESADO: "OK", OCUPADO: "0", ERR: "Err"}

# Rangos del número aleatorio (1..100) que determinan el estado de respuesta
RANGOS = {PROCESADO: (1, 70), OCUPADO: (71, 85), ERR: (86, 100)}

# Duración del estado "ocupado" (segundos): entre 1 y 3
OCUPADO_MIN = float(os.environ.get("MK_OCUPADO_MIN", "1"))
OCUPADO_MAX = float(os.environ.get("MK_OCUPADO_MAX", "3"))


def ahora():
    return datetime.datetime.now().strftime("%d/%m/%Y-%H:%M:%S")


class Canal:
    """Envoltura de un socket para enviar/recibir mensajes JSON por línea."""

    def __init__(self, sock):
        self.sock = sock
        self._lector = sock.makefile("r", encoding="utf-8", newline="\n")
        self._candado = threading.Lock()

    def enviar(self, obj):
        datos = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            with self._candado:
                self.sock.sendall(datos)
            return True
        except OSError:
            return False

    def recibir(self):
        """Devuelve el siguiente mensaje (dict) o None si la conexión se cerró."""
        while True:
            try:
                linea = self._lector.readline()
            except (OSError, ValueError):
                return None
            if not linea:
                return None
            try:
                return json.loads(linea)
            except json.JSONDecodeError:
                continue  # línea corrupta: se ignora

    def cerrar(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def conectar(nombre, intentos=240, espera=0.5):
    """Conecta al kernel y anuncia que el módulo está listo (etapa de inicialización).

    Espera hasta ~2 minutos a que el kernel esté disponible, para poder abrir los módulos
    manualmente y en cualquier orden.
    """
    avisado = False
    for _ in range(intentos):
        try:
            sock = socket.create_connection((HOST, PORT), timeout=2)
            sock.settimeout(None)
            canal = Canal(sock)
            canal.enviar({"cmd": "ready", "src": nombre, "dst": KERNEL, "msg": "listo"})
            return canal
        except OSError:
            if not avisado:
                print(f"[{nombre}] Esperando al kernel en {HOST}:{PORT} ... (ejecuta: python kernel.py --manual)", flush=True)
                avisado = True
            time.sleep(espera)
    raise SystemExit(f"[{nombre}] No se pudo conectar al kernel en {HOST}:{PORT}. "
                     "Ejecuta primero: python kernel.py --manual")


def decidir_estado():
    """Sortea el estado de respuesta según los rangos. MK_FORZAR permite fijarlo (pruebas)."""
    forzado = os.environ.get("MK_FORZAR", "").lower()
    fijos = {"procesado": PROCESADO, "ocupado": OCUPADO, "err": ERR}
    if forzado in fijos:
        return fijos[forzado]
    n = random.randint(1, 100)
    for cod, (bajo, alto) in RANGOS.items():
        if bajo <= n <= alto:
            return cod
    return PROCESADO


def responder(canal, nombre, cod, detalle="", pedido=None):
    """Envía al kernel un mensaje de terminación {codterm, msg}."""
    canal.enviar({
        "codterm": cod,
        "msg": MENSAJES_COD[cod],
        "src": nombre,
        "detalle": detalle,
        "ref": (pedido or {}).get("msg", ""),
    })


def atender(canal, nombre, pedido, accion):
    """Atiende un pedido aplicando el sorteo de estado.

    accion() -> (ok: bool, detalle: str). Las peticiones automáticas de refresco
    ("auto": true) de la GUI se ejecutan sin sorteo ni respuesta de estado.
    """
    if pedido.get("auto"):
        accion()
        return
    cod = decidir_estado()
    if cod == ERR:
        responder(canal, nombre, ERR, "Error simulado por sorteo de estado", pedido)
        return
    if cod == OCUPADO:
        responder(canal, nombre, OCUPADO, "Módulo ocupado; se atenderá en 1-3 s", pedido)
        time.sleep(random.uniform(OCUPADO_MIN, OCUPADO_MAX))
    ok, detalle = accion()
    responder(canal, nombre, PROCESADO if ok else ERR, detalle, pedido)