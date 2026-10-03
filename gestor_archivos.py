"""Módulo Gestor de Archivos: log de transacciones y manejo físico de carpetas.

Mensajes que entiende (siempre a través del kernel):
  send  "crear|ruta/relativa"   -> crea la carpeta (el padre debe existir)
  send  "borrar|ruta/relativa"  -> elimina la carpeta y su contenido
  send  "Log: texto"            -> agrega una línea al log de transacciones
  info  "log"                   -> devuelve el log
  info  "arbol"                 -> devuelve todas las carpetas (rutas relativas)
  info  "listar|ruta/relativa"  -> devuelve el contenido de una carpeta
Todas las rutas son relativas a `workspace/` y se validan para no salir de ella.
"""
import datetime
import json
import os
import re
import shutil
import threading

from common import GESTOR, GUI, ahora, atender, conectar

DIR = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.environ.get("MK_WORKSPACE", os.path.join(DIR, "workspace")))
LOGDIR = os.path.abspath(os.environ.get("MK_LOGDIR", os.path.join(DIR, "logs")))
LOGFILE = os.path.join(LOGDIR, "transacciones.log")
_candado_log = threading.Lock()


# ------------------------------------------------------------------ log
def escribir_log(origen, texto):
    os.makedirs(LOGDIR, exist_ok=True)
    with _candado_log, open(LOGFILE, "a", encoding="utf-8") as f:
        f.write(f"[{ahora()}] ({origen}) {texto}\n")


def leer_log(max_lineas=300):
    if not os.path.exists(LOGFILE):
        return ""
    with _candado_log, open(LOGFILE, encoding="utf-8") as f:
        return "".join(f.readlines()[-max_lineas:])


# ------------------------------------------------------------- rutas seguras
def normalizar(rel):
    """Limpia una ruta relativa ('a/b') y valida cada componente. '' = raíz del workspace."""
    rel = (rel or "").strip().replace("\\", "/").strip("/")
    partes = rel.split("/") if rel else []
    for p in partes:
        if p.strip(". ") == "" or not re.fullmatch(r"[\w\-. ]+", p):
            raise ValueError("Nombre de carpeta no válido")
    return "/".join(partes)


def ruta_segura(rel):
    """Ruta absoluta dentro de BASE; lanza ValueError si intenta salirse."""
    rel = normalizar(rel)
    base = os.path.realpath(BASE)
    ruta = os.path.realpath(os.path.join(base, *rel.split("/"))) if rel else base
    if os.path.commonpath([ruta, base]) != base:
        raise ValueError("La carpeta debe estar dentro del área de trabajo")
    return ruta


# ------------------------------------------------------- operaciones físicas
def crear(rel):
    try:
        rel = normalizar(rel)
        ruta = ruta_segura(rel)
    except ValueError as e:
        return False, str(e)
    if not rel:
        return False, "Nombre de carpeta no válido"
    if os.path.exists(ruta):
        return False, f"La carpeta '{rel}' ya existe"
    if not os.path.isdir(os.path.dirname(ruta)):
        return False, "La carpeta padre no existe"
    os.mkdir(ruta)
    return True, f"Carpeta creada: {ruta}"


def borrar(rel):
    try:
        rel = normalizar(rel)
        ruta = ruta_segura(rel)
    except ValueError as e:
        return False, str(e)
    if not rel:
        return False, "Nombre de carpeta no válido"
    if not os.path.exists(ruta):
        return False, f"La carpeta '{rel}' no existe"
    if not os.path.isdir(ruta):
        return False, f"'{rel}' no es una carpeta"
    shutil.rmtree(ruta)
    return True, f"Carpeta eliminada: {ruta}"


def listar_dir(rel):
    """Contenido de una carpeta (carpetas primero) o None si no existe."""
    ruta = ruta_segura(rel)
    if not os.path.isdir(ruta):
        return None
    items = []
    with os.scandir(ruta) as it:
        for e in it:
            try:
                st = e.stat()
                carpeta = e.is_dir()
            except OSError:
                continue
            items.append({
                "nombre": e.name,
                "tipo": "carpeta" if carpeta else "archivo",
                "modificado": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%d/%m/%Y %H:%M"),
                "tamano": None if carpeta else st.st_size,
            })
    items.sort(key=lambda i: (i["tipo"] != "carpeta", i["nombre"].lower()))
    return items


def listar_arbol():
    """Todas las carpetas del workspace como rutas relativas ordenadas."""
    base = os.path.realpath(BASE)
    res = []
    for raiz, dirs, _ in os.walk(base):
        dirs.sort()
        for d in dirs:
            res.append(os.path.relpath(os.path.join(raiz, d), base).replace(os.sep, "/"))
    return sorted(res)


# --------------------------------------------------------------- mensajería
def enviar_datos(canal, destino, tipo, contenido):
    canal.enviar({"cmd": "send", "src": GESTOR, "dst": destino, "tipo": tipo, "msg": contenido})


def manejar(canal, msg):
    cmd, texto, origen = msg.get("cmd"), msg.get("msg", ""), msg.get("src", GUI)
    if not isinstance(texto, str):
        return
    if cmd == "send":
        if texto.startswith("Log:"):  # el registro de transacciones no se somete al sorteo
            escribir_log(origen, texto[4:].strip())
            return
        operador, _, ruta = texto.partition("|")
        if operador in ("crear", "borrar"):
            def accion():
                ok, detalle = (crear if operador == "crear" else borrar)(ruta)
                escribir_log(GESTOR, f"{operador} '{ruta.strip()}' -> {'OK' if ok else 'ERROR'}: {detalle}")
                return ok, detalle
            atender(canal, GESTOR, msg, accion)
    elif cmd == "info":
        que, _, arg = texto.partition("|")

        def accion():
            if que == "log":
                enviar_datos(canal, origen, "log", leer_log())
            elif que == "arbol":
                enviar_datos(canal, origen, "arbol", json.dumps(listar_arbol()))
            elif que == "listar":
                try:
                    rel = normalizar(arg)
                    items = listar_dir(rel)
                except ValueError as e:
                    return False, str(e)
                enviar_datos(canal, origen, "listado", json.dumps({"ruta": rel, "items": items}))
            else:
                return False, f"Información desconocida: {texto}"
            return True, f"Información '{que}' enviada"
        atender(canal, GESTOR, msg, accion)


def main():
    os.makedirs(BASE, exist_ok=True)
    canal = conectar(GESTOR)
    escribir_log(GESTOR, "Módulo Gestor de Archivos iniciado")
    while True:
        msg = canal.recibir()
        if msg is None or msg.get("cmd") == "close":
            break
        manejar(canal, msg)
    escribir_log(GESTOR, "Módulo Gestor de Archivos finalizado")
    canal.cerrar()


if __name__ == "__main__":
    main()