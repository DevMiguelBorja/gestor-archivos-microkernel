"""Casos de prueba automáticos (sin GUI). Ejecutar:  python test_casos.py"""
import json
import os
import queue
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

from common import Canal

DIR = os.path.dirname(os.path.abspath(__file__))
resultados = []


def puerto_libre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Sistema:
    """Arranca un kernel headless y se conecta como si fuera el módulo GUI."""

    def __init__(self, forzar="procesado"):
        self.tmp = tempfile.mkdtemp(prefix="mk_")
        self.puerto = puerto_libre()
        env = dict(os.environ, MK_PORT=str(self.puerto), MK_FORZAR=forzar,
                   MK_WORKSPACE=os.path.join(self.tmp, "ws"), MK_LOGDIR=os.path.join(self.tmp, "logs"))
        self.ws = env["MK_WORKSPACE"]
        self.proc = subprocess.Popen([sys.executable, os.path.join(DIR, "kernel.py"), "--headless"],
                                     cwd=DIR, env=env, stdout=subprocess.DEVNULL)
        for _ in range(100):
            try:
                self.sock = socket.create_connection(("127.0.0.1", self.puerto), timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        self.sock.settimeout(None)
        self.canal = Canal(self.sock)
        self.q, self.atras = queue.Queue(), []
        import threading
        threading.Thread(target=self._leer, daemon=True).start()
        self.canal.enviar({"cmd": "ready", "src": "GUI", "dst": "kernel", "msg": "listo"})

    def _leer(self):
        while True:
            m = self.canal.recibir()
            if m is None:
                break
            self.q.put(m)

    def esperar(self, pred, t=10):
        for m in self.atras:
            if pred(m):
                self.atras.remove(m)
                return m
        fin = time.time() + t
        while time.time() < fin:
            try:
                m = self.q.get(timeout=0.2)
            except queue.Empty:
                continue
            if pred(m):
                return m
            self.atras.append(m)
        raise TimeoutError("No llegó el mensaje esperado")

    def pedir(self, dst, cmd, texto, **extra):
        self.atras.clear()
        self.canal.enviar(dict({"cmd": cmd, "src": "GUI", "dst": dst, "msg": texto}, **extra))

    def resultado(self, src, t=10):
        return self.esperar(lambda m: "codterm" in m and m.get("src") == src and m["codterm"] != 1, t)

    def log(self):
        self.pedir("GestorArc", "info", "log", auto=True)
        return self.esperar(lambda m: m.get("tipo") == "log")["msg"]

    def cerrar(self):
        self.canal.enviar({"cmd": "stop", "src": "GUI", "dst": "kernel", "msg": "fin de pruebas"})
        try:
            self.proc.wait(timeout=12)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        shutil.rmtree(self.tmp, ignore_errors=True)


def viva(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def caso(numero, nombre):
    def deco(fn):
        try:
            fn()
            resultados.append((numero, nombre, True, ""))
        except Exception as e:  # noqa: BLE001
            resultados.append((numero, nombre, False, repr(e)))
        return fn
    return deco


def apps_estado(s):
    s.pedir("APP", "info", "todas", auto=True)
    return json.loads(s.esperar(lambda m: m.get("tipo") == "apps")["msg"])


def main():
    s = Sistema("procesado")

    @caso(1, "Inicialización: Gestor y Aplicaciones quedan 'listo'")
    def _():
        m = s.esperar(lambda m: m.get("cmd") == "status" and m["msg"]["GestorArc"] == "listo"
                      and m["msg"]["APP"] == "listo")
        assert m["msg"]["kernel"] == "activo"

    @caso(2, "Crear carpeta físicamente")
    def _():
        s.pedir("GestorArc", "send", "crear|documentos")
        r = s.resultado("GestorArc")
        assert r["codterm"] == 0 and r["msg"] == "OK", r
        assert os.path.isdir(os.path.join(s.ws, "documentos"))

    @caso(3, "Crear carpeta duplicada -> Err")
    def _():
        s.pedir("GestorArc", "send", "crear|documentos")
        r = s.resultado("GestorArc")
        assert r["codterm"] == 2 and r["msg"] == "Err", r

    @caso(4, "Nombre inválido / path traversal -> Err y no crea nada")
    def _():
        s.pedir("GestorArc", "send", "crear|../fuera")
        r = s.resultado("GestorArc")
        assert r["codterm"] == 2, r
        assert not os.path.exists(os.path.join(s.tmp, "fuera"))

    @caso(5, "Borrar carpeta (con contenido) físicamente")
    def _():
        with open(os.path.join(s.ws, "documentos", "a.txt"), "w") as f:
            f.write("x")
        s.pedir("GestorArc", "send", "borrar|documentos")
        r = s.resultado("GestorArc")
        assert r["codterm"] == 0, r
        assert not os.path.exists(os.path.join(s.ws, "documentos"))

    @caso(6, "Borrar carpeta inexistente -> Err")
    def _():
        s.pedir("GestorArc", "send", "borrar|noexiste")
        assert s.resultado("GestorArc")["codterm"] == 2

    @caso(7, "Info de una instancia devuelve el PID de un proceso vivo")
    def _():
        s.pedir("APP", "info", "APP1")
        r = s.resultado("APP")
        pid = int(re.search(r"PID=(\d+)", r["detalle"]).group(1))
        assert r["codterm"] == 0 and viva(pid)

    @caso(8, "Cerrar una instancia desde GUI: el proceso muere y queda en el log")
    def _():
        pid = apps_estado(s)["APP2"]["pid"]
        s.pedir("APP", "send", "cerrar:APP2")
        assert s.resultado("APP")["codterm"] == 0
        time.sleep(0.5)
        assert not viva(pid)
        assert re.search(r"APP2 .*cerrada por solicitud del usuario", s.log())

    @caso(9, "Cierre manual/externo de una instancia se detecta y se registra")
    def _():
        pid = apps_estado(s)["APP3"]["pid"]
        os.kill(pid, signal.SIGTERM)
        time.sleep(1.5)
        assert "APP3" in s.log() and "cerrada de forma manual" in s.log()
        assert apps_estado(s)["APP3"]["estado"] == "cerrada"

    @caso(10, "Log de transacciones con fecha/hora, módulos y operación")
    def _():
        s.pedir("GestorArc", "send", "crear|reportes")
        s.resultado("GestorArc")
        log = s.log()
        assert re.search(r"\[\d{2}/\d{2}/\d{4}-\d{2}:\d{2}:\d{2}\]", log)
        assert "GUI->GestorArc [send] crear|reportes" in log

    @caso(11, "Cerrar módulo Aplicaciones cierra hijos y se relanza desde la GUI")
    def _():
        pids = [v["pid"] for v in apps_estado(s).values()]
        s.pedir("APP", "send", "cerrar:padre")
        assert s.resultado("APP")["codterm"] == 0
        s.esperar(lambda m: m.get("cmd") == "status" and m["msg"]["APP"] == "cerrado")
        time.sleep(0.5)
        assert not any(viva(p) for p in pids)
        s.pedir("kernel", "launch", "APP")
        assert s.esperar(lambda m: "codterm" in m and m.get("src") == "kernel")["codterm"] == 0
        s.esperar(lambda m: m.get("cmd") == "status" and m["msg"]["APP"] == "listo")
        assert all(v["estado"] == "activa" for v in apps_estado(s).values())

    @caso(12, "Destino inexistente -> el kernel responde Err")
    def _():
        s.pedir("Fantasma", "send", "hola")
        assert s.resultado("kernel")["codterm"] == 2

    s.cerrar()

    @caso(13, "Detener sistema: kernel y módulos terminan (código 0)")
    def _():
        assert s.proc.returncode == 0

    # --- Estados forzados
    s2 = Sistema("ocupado")
    s2.esperar(lambda m: m.get("cmd") == "status" and m["msg"]["GestorArc"] == "listo")

    @caso(14, "Estado Ocupado (codterm 1) y luego Procesado tras 1-3 s")
    def _():
        t0 = time.time()
        s2.pedir("GestorArc", "send", "crear|lento")
        oc = s2.esperar(lambda m: m.get("codterm") == 1 and m.get("src") == "GestorArc")
        assert oc["msg"] == "0"
        fin = s2.resultado("GestorArc")
        dur = time.time() - t0
        assert fin["codterm"] == 0 and 0.9 <= dur <= 4, dur
        assert os.path.isdir(os.path.join(s2.ws, "lento"))

    s2.cerrar()
    s3 = Sistema("err")
    s3.esperar(lambda m: m.get("cmd") == "status" and m["msg"]["GestorArc"] == "listo")

    @caso(15, "Estado Err (codterm 2): la operación no se ejecuta")
    def _():
        s3.pedir("GestorArc", "send", "crear|fallida")
        r = s3.resultado("GestorArc")
        assert r["codterm"] == 2 and r["msg"] == "Err"
        assert not os.path.exists(os.path.join(s3.ws, "fallida"))

    s3.cerrar()

    print("\n=== Resultados ===")
    for n, nombre, ok, err in resultados:
        print(f"Caso {n:>2}: {'PASA ' if ok else 'FALLA'} - {nombre} {err}")
    fallos = [r for r in resultados if not r[2]]
    print(f"\n{len(resultados) - len(fallos)}/{len(resultados)} casos aprobados")
    sys.exit(1 if fallos else 0)


if __name__ == "__main__":
    main()
