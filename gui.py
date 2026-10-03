"""Módulo GUI (tkinter puro): dashboard con barra de navegación y una vista por módulo.

Vistas:  Panel (resumen) · Explorador (Gestor de Archivos) · Aplicaciones · Registro (log)
Tema oscuro con bordes redondeados dibujados sobre Canvas (tkinter no los ofrece de forma nativa).
La GUI no toca disco ni procesos: solo envía mensajes al kernel y muestra lo que recibe.
"""
import json
import queue
import re
import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from common import APP, ERR, GESTOR, GUI, KERNEL, OCUPADO, PROCESADO, conectar

# ------------------------------------------------------------------ paleta
BG = "#0d0f17"
SIDEBAR = "#0a0c13"
CARD = "#171a26"
CARD2 = "#20243a"
INPUT = "#0f121c"
BORDE = "#2a2f47"
TEXTO = "#e8eaf2"
APAGADO = "#8b91ab"
ACENTO = "#7c5cff"
VERDE = "#3ddc97"
NARANJA = "#ffb454"
ROJO = "#ff5c7a"
AZUL = "#4cc9f0"

COLORES_ESTADO = {"listo": VERDE, "activo": VERDE, "ocupado": NARANJA, "cerrado": APAGADO}

_FUENTES = {}


def fuente(size=10, bold=False):
    clave = (size, bold)
    if clave not in _FUENTES:
        _FUENTES[clave] = tkfont.Font(size=size, weight="bold" if bold else "normal")
    return _FUENTES[clave]


def aclarar(color, f=0.15):
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    r, g, b = (int(c + (255 - c) * f) for c in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


def rect_redondo(canvas, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


def unir(ruta, nombre):
    return f"{ruta}/{nombre}" if ruta else nombre


def padre_de(ruta):
    return ruta.rsplit("/", 1)[0] if "/" in ruta else ""


def formato_tamano(n):
    if n is None:
        return ""
    for unidad in ("B", "KB", "MB", "GB"):
        if n < 1024 or unidad == "GB":
            return f"{n} B" if unidad == "B" else f"{n:.1f} {unidad}"
        n /= 1024


# ------------------------------------------------------- widgets redondeados
class Panel(tk.Canvas):
    """Contenedor redondeado. Los widgets hijos van en `.interior`."""

    def __init__(self, master, fill=CARD, parent_bg=BG, outline=BORDE, radius=16, pad=14, ancho=10, alto=10):
        super().__init__(master, width=ancho, height=alto, bg=parent_bg, highlightthickness=0, bd=0)
        self.fill, self.outline, self.radius, self.pad = fill, outline, radius, pad
        self.interior = tk.Frame(self, bg=fill)
        self._win = self.create_window(pad, pad, window=self.interior, anchor="nw")
        self.bind("<Configure>", self._dibujar)

    def _dibujar(self, e):
        self.delete("fondo")
        rect_redondo(self, 1, 1, e.width - 1, e.height - 1, self.radius,
                     fill=self.fill, outline=self.outline, tags="fondo")
        self.tag_lower("fondo")
        self.itemconfigure(self._win, width=max(1, e.width - 2 * self.pad),
                           height=max(1, e.height - 2 * self.pad))


class Boton(tk.Canvas):
    def __init__(self, master, texto, comando, fill=CARD2, fg=TEXTO, bg=CARD, padx=16, alto=34):
        self.fuente = fuente(10, True)
        ancho = self.fuente.measure(texto) + 2 * padx
        super().__init__(master, width=ancho, height=alto, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self.fill, self.comando = fill, comando
        self.forma = rect_redondo(self, 1, 1, ancho - 1, alto - 1, 12, fill=fill, outline="")
        self.create_text(ancho // 2, alto // 2, text=texto, fill=fg, font=self.fuente)
        self.bind("<Enter>", lambda _e: self.itemconfigure(self.forma, fill=aclarar(self.fill)))
        self.bind("<Leave>", lambda _e: self.itemconfigure(self.forma, fill=self.fill))
        self.bind("<ButtonRelease-1>", lambda _e: self.comando())


class Pildora(tk.Canvas):
    """Estado de un módulo: punto de color + nombre + estado."""

    def __init__(self, master, nombre, bg=BG):
        self.fuente = fuente(9, True)
        self.nombre = nombre
        self.w = self.fuente.measure(nombre) + self.fuente.measure("ocupado") + 64
        super().__init__(master, width=self.w, height=30, bg=bg, highlightthickness=0, bd=0)
        self.set("cerrado")

    def set(self, estado):
        color = COLORES_ESTADO.get(estado, APAGADO)
        self.delete("all")
        rect_redondo(self, 1, 1, self.w - 1, 29, 14, fill=CARD2, outline=BORDE)
        self.create_oval(12, 10, 22, 20, fill=color, outline="")
        self.create_text(30, 15, anchor="w", text=self.nombre, fill=TEXTO, font=self.fuente)
        self.create_text(self.w - 12, 15, anchor="e", text=estado, fill=color, font=self.fuente)


class Entrada(tk.Canvas):
    """Campo de texto redondeado que se ilumina al enfocarlo."""

    def __init__(self, master, bg=CARD, alto=38):
        super().__init__(master, width=10, height=alto, bg=bg, highlightthickness=0, bd=0)
        self.alto, self.foco = alto, False
        self.entry = tk.Entry(self, bg=INPUT, fg=TEXTO, insertbackground=TEXTO, relief="flat", bd=0,
                              highlightthickness=0, font=fuente(11))
        self._win = self.create_window(14, alto // 2, window=self.entry, anchor="w")
        self.entry.bind("<FocusIn>", lambda _e: self._foco(True))
        self.entry.bind("<FocusOut>", lambda _e: self._foco(False))
        self.bind("<Configure>", self._dibujar)

    def _foco(self, valor):
        self.foco = valor
        self._dibujar()

    def _dibujar(self, e=None):
        ancho = e.width if e else self.winfo_width()
        self.delete("fondo")
        rect_redondo(self, 1, 1, ancho - 1, self.alto - 1, 12, fill=INPUT,
                     outline=ACENTO if self.foco else BORDE, tags="fondo")
        self.tag_lower("fondo")
        self.itemconfigure(self._win, width=max(1, ancho - 28))

    def get(self):
        return self.entry.get()

    def delete(self, a, b):
        self.entry.delete(a, b)

    def insert(self, i, texto):
        self.entry.insert(i, texto)


class Etiqueta(tk.Canvas):
    """Barra redondeada de solo lectura (barra de dirección)."""

    def __init__(self, master, bg=CARD, alto=34):
        super().__init__(master, width=10, height=alto, bg=bg, highlightthickness=0, bd=0)
        self.alto, self.texto, self.fuente = alto, "", fuente(10)
        self.bind("<Configure>", lambda _e: self._dibujar())

    def set(self, texto):
        self.texto = texto
        self._dibujar()

    def _dibujar(self):
        self.delete("all")
        rect_redondo(self, 1, 1, self.winfo_width() - 1, self.alto - 1, 12, fill=INPUT, outline=BORDE)
        self.create_text(14, self.alto // 2, anchor="w", text=self.texto, fill=TEXTO, font=self.fuente)


class Aviso(tk.Canvas):
    """Barra de mensajes; cambia de color según el resultado."""
    TONOS = {PROCESADO: ("#12352a", VERDE), OCUPADO: ("#39290f", NARANJA),
             ERR: ("#3a1822", ROJO), None: (CARD, APAGADO)}

    def __init__(self, master):
        super().__init__(master, width=10, height=44, bg=BG, highlightthickness=0, bd=0)
        self.fuente = fuente(10)
        self.texto, self.cod = "Listo", None
        self.bind("<Configure>", lambda _e: self._dibujar())

    def set(self, texto, cod=None):
        self.texto, self.cod = texto, cod
        self._dibujar()

    def _dibujar(self):
        fondo, color = self.TONOS.get(self.cod, self.TONOS[None])
        ancho = self.winfo_width()
        self.delete("all")
        rect_redondo(self, 1, 1, ancho - 1, 43, 14, fill=fondo, outline=BORDE)
        texto = self.texto if len(self.texto) < 150 else self.texto[:147] + "..."
        self.create_text(18, 22, anchor="w", text=texto, fill=color, font=self.fuente, width=max(50, ancho - 36))


class NavItem(tk.Canvas):
    """Elemento de la barra de navegación lateral con punto de estado del módulo."""

    def __init__(self, master, texto, glyph, comando):
        super().__init__(master, width=210, height=46, bg=SIDEBAR, highlightthickness=0, bd=0, cursor="hand2")
        self.texto, self.glyph, self.comando = texto, glyph, comando
        self.activo, self.hover, self.color_estado = False, False, None
        self.fuente = fuente(11, True)
        self.bind("<Enter>", lambda _e: self._hover(True))
        self.bind("<Leave>", lambda _e: self._hover(False))
        self.bind("<ButtonRelease-1>", lambda _e: self.comando())
        self._dibujar()

    def _hover(self, valor):
        self.hover = valor
        self._dibujar()

    def set_activo(self, valor):
        self.activo = valor
        self._dibujar()

    def set_estado(self, color):
        self.color_estado = color
        self._dibujar()

    def _dibujar(self):
        self.delete("all")
        if self.activo or self.hover:
            rect_redondo(self, 2, 2, 208, 44, 12, fill="#251f4d" if self.activo else CARD, outline="")
        color = "white" if self.activo else (TEXTO if self.hover else APAGADO)
        self.create_text(22, 23, text=self.glyph, fill=ACENTO if self.activo else APAGADO, font=self.fuente)
        self.create_text(44, 23, anchor="w", text=self.texto, fill=color, font=self.fuente)
        if self.color_estado:
            self.create_oval(188, 18, 198, 28, fill=self.color_estado, outline="")


class Dialogo(tk.Toplevel):
    """Diálogo modal oscuro: confirmar, avisar o pedir un texto."""

    def __init__(self, padre, titulo, mensaje, entrada=False, ok="Aceptar", cancelar=True, peligro=False):
        super().__init__(padre)
        self.title(titulo)
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(padre)
        self.resultado = None
        w, h = 440, 250 if entrada else 205
        panel = Panel(self, pad=20, ancho=w - 16, alto=h - 16)
        panel.pack(padx=8, pady=8)
        c = panel.interior
        fila = tk.Frame(c, bg=CARD)
        fila.pack(side="bottom", fill="x")
        Boton(fila, ok, self._aceptar, fill=ROJO if peligro else ACENTO, fg="white").pack(side="right")
        if cancelar:
            Boton(fila, "Cancelar", self._cancelar, fill=CARD2).pack(side="right", padx=(0, 8))
        tk.Label(c, text=titulo, fg=TEXTO, bg=CARD, font=fuente(13, True)).pack(anchor="w")
        tk.Label(c, text=mensaje, fg=APAGADO, bg=CARD, font=fuente(10), wraplength=w - 80,
                 justify="left").pack(anchor="w", pady=(8, 10))
        self.entrada = None
        if entrada:
            self.entrada = Entrada(c)
            self.entrada.pack(fill="x")
        self.update_idletasks()
        x = padre.winfo_rootx() + (padre.winfo_width() - w) // 2
        y = padre.winfo_rooty() + (padre.winfo_height() - h) // 2
        self.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")
        self.bind("<Escape>", lambda _e: self._cancelar())
        self.bind("<Return>", lambda _e: self._aceptar())
        self.protocol("WM_DELETE_WINDOW", self._cancelar)
        self.wait_visibility()
        self.grab_set()
        (self.entrada.entry if self.entrada else self).focus_set()
        self.wait_window(self)

    def _aceptar(self):
        self.resultado = (self.entrada.get().strip() or None) if self.entrada else True
        self.destroy()

    def _cancelar(self):
        self.resultado = None
        self.destroy()


# ------------------------------------------------------------ log coloreado
def configurar_tags(texto):
    for tag, color in (("fecha", APAGADO), ("origen", AZUL), ("error", ROJO), ("aviso", NARANJA),
                       ("normal", TEXTO)):
        texto.tag_configure(tag, foreground=color)


def pintar_lineas(texto, lineas):
    al_final = texto.yview()[1] >= 0.99
    texto.config(state="normal")
    texto.delete("1.0", "end")
    for linea in lineas:
        m = re.match(r"^(\[[^\]]+\])\s*(\([^)]*\))?\s*(.*)$", linea)
        if not m:
            texto.insert("end", linea + "\n", "normal")
            continue
        fecha, origen, resto = m.groups()
        tag = "normal"
        if "ERROR" in resto or "Err" in resto:
            tag = "error"
        elif any(p in resto for p in ("cerrada", "desconect", "detenido", "finalizado")):
            tag = "aviso"
        texto.insert("end", fecha + " ", "fecha")
        if origen:
            texto.insert("end", origen + " ", "origen")
        texto.insert("end", resto + "\n", tag)
    texto.config(state="disabled")
    if al_final:
        texto.see("end")


def nuevo_texto_log(padre, alto=None):
    """Text de solo lectura con scrollbar oscura, dentro de un contenedor redondeado."""
    cont = Panel(padre, fill=INPUT, parent_bg=CARD, radius=12, pad=8)
    barra = ttk.Scrollbar(cont.interior, style="Dark.Vertical.TScrollbar")
    barra.pack(side="right", fill="y")
    t = tk.Text(cont.interior, bg=INPUT, fg=TEXTO, bd=0, highlightthickness=0, relief="flat", wrap="word",
                state="disabled", padx=6, pady=4, font=tkfont.nametofont("TkFixedFont"),
                yscrollcommand=barra.set, **({"height": alto} if alto else {}))
    t.pack(fill="both", expand=True)
    barra.config(command=t.yview)
    configurar_tags(t)
    return cont, t


# ---------------------------------------------------------------- interfaz
class Interfaz(tk.Tk):
    VISTAS = (("panel", "Panel", "⌂", KERNEL), ("explorador", "Explorador", "▤", GESTOR),
              ("apps", "Aplicaciones", "⚙", APP), ("registro", "Registro", "☰", GESTOR))

    def __init__(self):
        super().__init__()
        self.title("MicroKernel · Gestor de archivos")
        self.geometry("1200x790")
        self.minsize(1020, 690)
        self.configure(bg=BG)
        self.canal = conectar(GUI)
        self.cola = queue.Queue()
        self.estado = {}
        self.pildoras = {}
        self.tarjetas = {}
        self.ruta, self.pila, self.tipos = "", [], {}
        self._cerrando = False
        self._ultimo_log = ""
        self._ultimo_arbol = self._ultimo_listado = self._ultimas_apps = None
        self._estilos()
        self._iconos()
        self._construir()
        self.mostrar("panel")
        threading.Thread(target=self._lector, daemon=True).start()
        self.protocol("WM_DELETE_WINDOW", self.detener_sistema)
        self.after(100, self._procesar)
        self.after(1500, self._refrescar)

    # ------------------------------------------------------ estilos e iconos
    def _estilos(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.layout("Dark.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        s.configure("Dark.Treeview", background=INPUT, fieldbackground=INPUT, foreground=TEXTO,
                    borderwidth=0, rowheight=30, font=fuente(10))
        s.map("Dark.Treeview", background=[("selected", ACENTO)], foreground=[("selected", "white")])
        s.configure("Dark.Treeview.Heading", background=CARD, foreground=APAGADO, relief="flat",
                    borderwidth=0, font=fuente(9, True))
        s.map("Dark.Treeview.Heading", background=[("active", CARD)])
        s.configure("Dark.Vertical.TScrollbar", background=BORDE, troughcolor=INPUT, bordercolor=INPUT,
                    arrowcolor=APAGADO, lightcolor=BORDE, darkcolor=BORDE, relief="flat", borderwidth=0)
        s.map("Dark.Vertical.TScrollbar", background=[("active", ACENTO)])

    def _iconos(self):
        def carpeta():
            img = tk.PhotoImage(master=self, width=20, height=16)
            img.put("#e0a53a", to=(0, 0, 8, 3))
            img.put(NARANJA, to=(0, 3, 20, 16))
            img.put(aclarar(NARANJA, 0.3), to=(0, 3, 20, 5))
            return img

        def archivo():
            img = tk.PhotoImage(master=self, width=20, height=16)
            img.put(APAGADO, to=(5, 0, 16, 16))
            img.put(aclarar(APAGADO, 0.4), to=(5, 0, 16, 2))
            return img
        self.ico_carpeta, self.ico_archivo = carpeta(), archivo()

    # --------------------------------------------------------- construcción
    def _construir(self):
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        # ---- barra de navegación lateral
        lateral = tk.Frame(self, bg=SIDEBAR)
        lateral.grid(row=0, column=0, sticky="ns")
        tk.Label(lateral, text="◆ MicroKernel", fg=ACENTO, bg=SIDEBAR, font=fuente(16, True)).pack(
            anchor="w", padx=22, pady=(26, 0))
        tk.Label(lateral, text="Gestor de archivos", fg=APAGADO, bg=SIDEBAR, font=fuente(10)).pack(
            anchor="w", padx=22, pady=(0, 24))
        self.navs = {}
        for clave, texto, glyph, _modulo in self.VISTAS:
            item = NavItem(lateral, texto, glyph, lambda k=clave: self.mostrar(k))
            item.pack(padx=15, pady=2)
            self.navs[clave] = item
        Boton(lateral, "Detener sistema", self.detener_sistema, fill=ROJO, fg="white", bg=SIDEBAR,
              alto=40).pack(side="bottom", padx=15, pady=20)

        # ---- contenido: vistas apiladas + barra de mensajes
        contenido = tk.Frame(self, bg=BG)
        contenido.grid(row=0, column=1, sticky="nsew")
        contenido.rowconfigure(0, weight=1)
        contenido.columnconfigure(0, weight=1)
        area = tk.Frame(contenido, bg=BG)
        area.grid(row=0, column=0, sticky="nsew")
        area.rowconfigure(0, weight=1)
        area.columnconfigure(0, weight=1)
        self.vistas = {}
        for clave, constructor in (("panel", self._vista_panel), ("explorador", self._vista_explorador),
                                   ("apps", self._vista_apps), ("registro", self._vista_registro)):
            marco = tk.Frame(area, bg=BG)
            marco.grid(row=0, column=0, sticky="nsew")
            constructor(marco)
            self.vistas[clave] = marco
        self.aviso = Aviso(contenido)
        self.aviso.grid(row=1, column=0, sticky="ew", padx=22, pady=(4, 18))

    def mostrar(self, clave):
        self.vistas[clave].tkraise()
        for k, nav in self.navs.items():
            nav.set_activo(k == clave)

    def _cabecera(self, padre, titulo, sub, modulo):
        marco = tk.Frame(padre, bg=BG)
        marco.pack(fill="x", padx=22, pady=(24, 14))
        izq = tk.Frame(marco, bg=BG)
        izq.pack(side="left")
        tk.Label(izq, text=titulo, fg=TEXTO, bg=BG, font=fuente(19, True)).pack(anchor="w")
        tk.Label(izq, text=sub, fg=APAGADO, bg=BG, font=fuente(10)).pack(anchor="w")
        p = Pildora(marco, modulo)
        p.pack(side="right")
        self.pildoras.setdefault(modulo, []).append(p)

    # ------------------------------------------------------------ vista: Panel
    def _vista_panel(self, f):
        self._cabecera(f, "Panel de control", "Resumen del sistema en tiempo real", KERNEL)
        fila = tk.Frame(f, bg=BG)
        fila.pack(fill="x", padx=22)
        fila.columnconfigure((0, 1, 2, 3), weight=1, uniform="m")
        datos = (
            (KERNEL, "Kernel", "Coordina: enruta mensajes y controla el ciclo de vida.", "Ver registro", "registro"),
            (GESTOR, "Gestor de Archivos", "Crea y borra carpetas físicas; lleva el log.", "Abrir explorador", "explorador"),
            (APP, "Aplicaciones", "Proceso padre que controla instancias hijas.", "Abrir aplicaciones", "apps"),
            (GUI, "Interfaz", "Esta ventana: solo envía y muestra mensajes.", None, None),
        )
        for i, (modulo, nombre, desc, boton, destino) in enumerate(datos):
            p = Panel(fila, alto=215)
            p.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 6, 0 if i == 3 else 6))
            c = p.interior
            tk.Label(c, text=nombre, fg=TEXTO, bg=CARD, font=fuente(12, True)).pack(anchor="w")
            estado = tk.Label(c, text="● cerrado", fg=APAGADO, bg=CARD, font=fuente(10, True))
            estado.pack(anchor="w", pady=(2, 6))
            tk.Label(c, text=desc, fg=APAGADO, bg=CARD, font=fuente(9), wraplength=170,
                     justify="left").pack(anchor="w")
            metrica = tk.Label(c, text="", fg=TEXTO, bg=CARD, font=fuente(11, True))
            metrica.pack(anchor="w", pady=(8, 0))
            if boton:
                Boton(c, boton, lambda d=destino: self.mostrar(d), fill=CARD2).pack(side="bottom", anchor="w")
            self.tarjetas[modulo] = {"estado": estado, "metrica": metrica}
        self.tarjetas[KERNEL]["metrica"].config(text="Coordinación central")
        self.tarjetas[GUI]["metrica"].config(text="Conectada al kernel")

        marco = Panel(f)
        marco.pack(fill="both", expand=True, padx=22, pady=(12, 8))
        tk.Label(marco.interior, text="Actividad reciente", fg=TEXTO, bg=CARD, font=fuente(12, True)).pack(anchor="w")
        cont, self.texto_reciente = nuevo_texto_log(marco.interior)
        cont.pack(fill="both", expand=True, pady=(10, 0))

    # ------------------------------------------------------ vista: Explorador
    def _vista_explorador(self, f):
        self._cabecera(f, "Explorador de archivos", "Módulo Gestor de Archivos · carpeta de trabajo «workspace»", GESTOR)
        barra = Panel(f, pad=8, alto=58)
        barra.pack(fill="x", padx=22, pady=(0, 8))
        b = barra.interior
        Boton(b, "‹ Atrás", self.atras).pack(side="left")
        Boton(b, "↑ Subir", self.subir).pack(side="left", padx=(6, 10))
        Boton(b, "Eliminar", self.eliminar, fill=ROJO, fg="white").pack(side="right")
        Boton(b, "+ Nueva carpeta", self.nueva_carpeta, fill=ACENTO, fg="white").pack(side="right", padx=(0, 6))
        Boton(b, "⟳", self.refrescar_ahora, padx=12).pack(side="right", padx=(0, 6))
        self.barra_ruta = Etiqueta(b)
        self.barra_ruta.pack(side="left", fill="x", expand=True, padx=(0, 10))

        cuerpo = Panel(f, pad=10)
        cuerpo.pack(fill="both", expand=True, padx=22, pady=(0, 8))
        c = cuerpo.interior
        c.rowconfigure(0, weight=1)
        c.columnconfigure(1, weight=1)

        izq = Panel(c, fill=INPUT, parent_bg=CARD, radius=12, pad=6, ancho=230)
        izq.grid(row=0, column=0, sticky="ns", padx=(0, 8))
        self.arbol_carp = ttk.Treeview(izq.interior, show="tree", selectmode="browse", style="Dark.Treeview")
        self.arbol_carp.column("#0", width=200)
        self.arbol_carp.pack(fill="both", expand=True)
        self.arbol_carp.bind("<<TreeviewSelect>>", self._al_elegir_arbol)

        der = Panel(c, fill=INPUT, parent_bg=CARD, radius=12, pad=6)
        der.grid(row=0, column=1, sticky="nsew")
        barra_v = ttk.Scrollbar(der.interior, style="Dark.Vertical.TScrollbar")
        barra_v.pack(side="right", fill="y")
        self.detalle = ttk.Treeview(der.interior, columns=("modificado", "tipo", "tamano"), selectmode="browse",
                                    style="Dark.Treeview", yscrollcommand=barra_v.set)
        barra_v.config(command=self.detalle.yview)
        for col, texto, ancho in (("#0", "NOMBRE", 260), ("modificado", "FECHA DE MODIFICACIÓN", 170),
                                  ("tipo", "TIPO", 140), ("tamano", "TAMAÑO", 90)):
            self.detalle.heading(col, text=texto, anchor="w")
            self.detalle.column(col, width=ancho, anchor="w")
        self.detalle.pack(fill="both", expand=True)
        self.detalle.bind("<Double-1>", self._doble_clic)
        for evento in ("<Button-3>", "<Button-2>"):
            self.detalle.bind(evento, self._menu_contextual)
        self.detalle.bind("<Return>", lambda _e: self.abrir())
        self.detalle.bind("<Delete>", lambda _e: self.eliminar())
        self.detalle.bind("<BackSpace>", lambda _e: self.subir())
        self.detalle.bind("<F5>", lambda _e: self.refrescar_ahora())

        self.lbl_items = tk.Label(c, text="0 elementos", fg=APAGADO, bg=CARD, font=fuente(9), anchor="w")
        self.lbl_items.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        self.menu = tk.Menu(self, tearoff=0, bg=CARD2, fg=TEXTO, activebackground=ACENTO,
                            activeforeground="white", bd=0, relief="flat", font=fuente(10))
        self.menu.add_command(label="Abrir", command=self.abrir)
        self.menu.add_command(label="Nueva carpeta", command=self.nueva_carpeta)
        self.menu.add_command(label="Eliminar", command=self.eliminar)
        self.menu.add_separator()
        self.menu.add_command(label="Actualizar", command=self.refrescar_ahora)
        self._actualizar_ruta_ui()

    # ---------------------------------------------------- vista: Aplicaciones
    def _vista_apps(self, f):
        self._cabecera(f, "Aplicaciones", "Módulo Aplicaciones · proceso padre con instancias hijas", APP)
        panel = Panel(f)
        panel.pack(fill="both", expand=True, padx=22, pady=(0, 8))
        a = panel.interior
        self.lbl_resumen = tk.Label(a, text="Sin información", fg=APAGADO, bg=CARD, font=fuente(10), anchor="w")
        self.lbl_resumen.pack(fill="x")
        fila_b = tk.Frame(a, bg=CARD)
        fila_b.pack(side="bottom", fill="x", pady=(12, 0))
        for texto, cmd, fill, fg in (
            ("Info (PID)", self.info_app, CARD2, TEXTO),
            ("Cerrar instancia", self.cerrar_instancia, NARANJA, "#1a1206"),
            ("Cerrar todas", lambda: self._cerrar_apps("todas"), NARANJA, "#1a1206"),
            ("Cerrar módulo", self.cerrar_modulo, ROJO, "white"),
            ("Lanzar módulo", self.lanzar_modulo, VERDE, "#06180f"),
        ):
            Boton(fila_b, texto, cmd, fill=fill, fg=fg).pack(side="left", padx=(0, 8))
        cont = Panel(a, fill=INPUT, parent_bg=CARD, radius=12, pad=6)
        cont.pack(fill="both", expand=True, pady=(10, 0))
        self.arbol = ttk.Treeview(cont.interior, columns=("pid", "estado"), style="Dark.Treeview")
        self.arbol.heading("#0", text="INSTANCIA", anchor="w")
        self.arbol.heading("pid", text="PID", anchor="w")
        self.arbol.heading("estado", text="ESTADO", anchor="w")
        self.arbol.column("#0", width=200)
        self.arbol.column("pid", width=120)
        self.arbol.column("estado", width=140)
        self.arbol.tag_configure("activa", foreground=VERDE)
        self.arbol.tag_configure("cerrada", foreground=APAGADO)
        self.arbol.pack(fill="both", expand=True)

    # ------------------------------------------------------- vista: Registro
    def _vista_registro(self, f):
        self._cabecera(f, "Registro de transacciones", "Lo escribe el módulo Gestor de Archivos", GESTOR)
        panel = Panel(f)
        panel.pack(fill="both", expand=True, padx=22, pady=(0, 8))
        p = panel.interior
        fila = tk.Frame(p, bg=CARD)
        fila.pack(fill="x", pady=(0, 10))
        tk.Label(fila, text="Filtrar:", fg=APAGADO, bg=CARD, font=fuente(10)).pack(side="left", padx=(0, 8))
        self.filtro = Entrada(fila)
        self.filtro.pack(side="left", fill="x", expand=True)
        self.filtro.entry.bind("<KeyRelease>", lambda _e: self._pintar_log())
        cont, self.texto_log = nuevo_texto_log(p)
        cont.pack(fill="both", expand=True)

    # ----------------------------------------------------------- mensajería
    def _lector(self):
        while True:
            msg = self.canal.recibir()
            self.cola.put(msg if msg is not None else {"cmd": "close"})
            if msg is None:
                break

    def _enviar(self, dst, cmd, texto, **extra):
        msg = {"cmd": cmd, "src": GUI, "dst": dst, "msg": texto}
        msg.update(extra)
        if not self.canal.enviar(msg):
            self.aviso.set("Sin conexión con el kernel", ERR)

    def _procesar(self):
        if self._cerrando:
            return
        try:
            while True:
                self._atender(self.cola.get_nowait())
        except queue.Empty:
            pass
        if not self._cerrando:
            self.after(100, self._procesar)

    def _atender(self, msg):
        tipo = msg.get("tipo")
        if msg.get("cmd") == "close":
            self._cerrando = True
            self.destroy()
        elif msg.get("cmd") == "status":
            self._estado_modulos(msg["msg"])
        elif "codterm" in msg:
            cod, origen = msg["codterm"], msg.get("src", "?")
            if cod == OCUPADO:
                self.aviso.set(f"[{origen}] Ocupado, atenderá tu pedido en 1-3 s…  ({msg.get('ref', '')})", cod)
            else:
                etiqueta = "OK" if cod == PROCESADO else "ERROR"
                self.aviso.set(f"[{origen}] {etiqueta} · {msg.get('detalle', '')}", cod)
                if cod == PROCESADO and origen == GESTOR:
                    self.refrescar_ahora()
        elif tipo == "log":
            self._ultimo_log = msg["msg"]
            self._pintar_log()
        elif tipo == "arbol":
            self._mostrar_arbol(json.loads(msg["msg"]))
        elif tipo == "listado":
            self._mostrar_listado(json.loads(msg["msg"]))
        elif tipo == "apps":
            self._mostrar_apps(json.loads(msg["msg"]))

    def _estado_modulos(self, estado):
        anterior = self.estado.get(GESTOR)
        self.estado = estado
        for modulo, lista in self.pildoras.items():
            for p in lista:
                p.set(estado.get(modulo, "cerrado"))
        for clave, _t, _g, modulo in self.VISTAS:
            valor = estado.get(modulo, "cerrado")
            self.navs[clave].set_estado(COLORES_ESTADO.get(valor, APAGADO))
        for modulo, t in self.tarjetas.items():
            valor = estado.get(modulo, "cerrado")
            t["estado"].config(text=f"● {valor}", fg=COLORES_ESTADO.get(valor, APAGADO))
        if estado.get(GESTOR) == "listo" and anterior != "listo":
            self.refrescar_ahora()

    # ------------------------------------------------------------- refrescos
    def refrescar_ahora(self):
        """Pide árbol y listado actuales (sin sorteo de estado: 'auto')."""
        if self.estado.get(GESTOR) in ("listo", "ocupado"):
            self._enviar(GESTOR, "info", "arbol", auto=True)
            self._enviar(GESTOR, "info", f"listar|{self.ruta}", auto=True)

    def _refrescar(self):
        if self._cerrando:
            return
        if self.estado.get(GESTOR) == "listo":
            self._enviar(GESTOR, "info", "log", auto=True)
            self.refrescar_ahora()
        if self.estado.get(APP) == "listo":
            self._enviar(APP, "info", "todas", auto=True)
        self.after(2000, self._refrescar)

    # ------------------------------------------------------------ registro
    def _pintar_log(self):
        lineas = self._ultimo_log.splitlines()
        filtro = self.filtro.get().strip().lower()
        pintar_lineas(self.texto_log, [l for l in lineas if filtro in l.lower()] if filtro else lineas)
        pintar_lineas(self.texto_reciente, lineas[-12:])

    # ----------------------------------------------------------- explorador
    def _actualizar_ruta_ui(self):
        partes = ["workspace"] + (self.ruta.split("/") if self.ruta else [])
        self.barra_ruta.set("▤  " + "  ›  ".join(partes))

    def ir_a(self, ruta, registrar=True):
        if registrar and ruta != self.ruta:
            self.pila.append(self.ruta)
        self.ruta = ruta
        self._ultimo_listado = None
        self._actualizar_ruta_ui()
        self._seleccionar_en_arbol()
        self.refrescar_ahora()

    def atras(self):
        if self.pila:
            self.ir_a(self.pila.pop(), registrar=False)

    def subir(self):
        if self.ruta:
            self.ir_a(padre_de(self.ruta))

    def _seleccionar_en_arbol(self):
        iid = self.ruta or "/"
        if self.arbol_carp.exists(iid):
            self.arbol_carp.selection_set(iid)
            self.arbol_carp.see(iid)

    def _al_elegir_arbol(self, _e):
        sel = self.arbol_carp.selection()
        if sel:
            ruta = "" if sel[0] == "/" else sel[0]
            if ruta != self.ruta:
                self.ir_a(ruta)

    def _nodos(self, padre=""):
        for hijo in self.arbol_carp.get_children(padre):
            yield hijo
            yield from self._nodos(hijo)

    def _mostrar_arbol(self, lista):
        if lista == self._ultimo_arbol:
            return
        self._ultimo_arbol = lista
        abiertos = {n for n in self._nodos() if self.arbol_carp.item(n, "open")}
        self.arbol_carp.delete(*self.arbol_carp.get_children())
        self.arbol_carp.insert("", "end", iid="/", text=" workspace", image=self.ico_carpeta, open=True)
        for ruta in lista:
            padre = padre_de(ruta) or "/"
            self.arbol_carp.insert(padre, "end", iid=ruta, text=" " + ruta.rsplit("/", 1)[-1],
                                   image=self.ico_carpeta, open=ruta in abiertos)
        self._seleccionar_en_arbol()
        self.tarjetas[GESTOR]["metrica"].config(text=f"{len(lista)} carpetas")

    def _mostrar_listado(self, datos):
        if datos["ruta"] != self.ruta:
            return
        if datos["items"] is None:  # la carpeta actual ya no existe
            if self.ruta:
                self.ir_a(padre_de(self.ruta), registrar=False)
            return
        if datos == self._ultimo_listado:
            return
        self._ultimo_listado = datos
        seleccion = self.detalle.selection()
        self.detalle.delete(*self.detalle.get_children())
        self.tipos = {}
        for it in datos["items"]:
            es_carpeta = it["tipo"] == "carpeta"
            self.tipos[it["nombre"]] = it["tipo"]
            self.detalle.insert("", "end", iid=it["nombre"], text=" " + it["nombre"],
                                image=self.ico_carpeta if es_carpeta else self.ico_archivo,
                                values=(it["modificado"], "Carpeta de archivos" if es_carpeta else "Archivo",
                                        "" if es_carpeta else formato_tamano(it["tamano"])))
        for s in seleccion:
            if self.detalle.exists(s):
                self.detalle.selection_set(s)
        n = len(datos["items"])
        self.lbl_items.config(text=f"{n} elemento" + ("" if n == 1 else "s"))

    def _seleccionado(self):
        sel = self.detalle.selection()
        return sel[0] if sel else None

    def _doble_clic(self, e):
        fila = self.detalle.identify_row(e.y)
        if fila:
            self.detalle.selection_set(fila)
            self.abrir()

    def _menu_contextual(self, e):
        fila = self.detalle.identify_row(e.y)
        if fila:
            self.detalle.selection_set(fila)
        else:
            self.detalle.selection_remove(*self.detalle.selection())
        estado = "normal" if fila else "disabled"
        self.menu.entryconfig("Abrir", state=estado)
        self.menu.entryconfig("Eliminar", state=estado)
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()

    def abrir(self):
        nombre = self._seleccionado()
        if not nombre:
            return
        if self.tipos.get(nombre) == "carpeta":
            self.ir_a(unir(self.ruta, nombre))
        else:
            self.aviso.set(f"'{nombre}' es un archivo; este gestor solo abre carpetas", None)

    def nueva_carpeta(self):
        nombre = self.pedir_texto("Nueva carpeta", "Nombre de la carpeta a crear en la ubicación actual.")
        if not nombre:
            return
        if "/" in nombre or "\\" in nombre:
            self.avisar("Nombre no válido", "El nombre no puede contener barras.")
            return
        self._enviar(GESTOR, "send", f"crear|{unir(self.ruta, nombre)}")

    def eliminar(self):
        nombre = self._seleccionado()
        if not nombre:
            self.avisar("Nada seleccionado", "Elige una carpeta en la lista.")
            return
        if self.tipos.get(nombre) != "carpeta":
            self.avisar("Solo carpetas", "Este gestor solo elimina carpetas.")
            return
        if self.confirmar("Eliminar carpeta", f"Se eliminará físicamente «{nombre}» y todo su contenido. "
                          "No va a la papelera.", ok="Eliminar", peligro=True):
            self._enviar(GESTOR, "send", f"borrar|{unir(self.ruta, nombre)}")

    # ----------------------------------------------------------- aplicaciones
    def _mostrar_apps(self, apps):
        if apps == self._ultimas_apps:
            return
        self._ultimas_apps = apps
        seleccion = self.arbol.selection()
        self.arbol.delete(*self.arbol.get_children())
        for nombre, d in apps.items():
            self.arbol.insert("", "end", iid=nombre, text=" " + nombre, values=(d["pid"], d["estado"]),
                              tags=(d["estado"],))
        for s in seleccion:
            if self.arbol.exists(s):
                self.arbol.selection_set(s)
        activas = sum(1 for d in apps.values() if d["estado"] == "activa")
        self.lbl_resumen.config(text=f"{len(apps)} instancias · {activas} activas")
        self.tarjetas[APP]["metrica"].config(text=f"{activas}/{len(apps)} activas")

    def _instancia_seleccionada(self):
        sel = self.arbol.selection()
        if not sel:
            self.avisar("Selecciona una instancia", "Elige una instancia en la tabla.")
            return None
        return sel[0]

    def info_app(self):
        nombre = self._instancia_seleccionada()
        if nombre:
            self._enviar(APP, "info", nombre)

    def cerrar_instancia(self):
        nombre = self._instancia_seleccionada()
        if nombre:
            self._cerrar_apps(nombre)

    def _cerrar_apps(self, objetivo):
        self._enviar(APP, "send", f"cerrar:{objetivo}")

    def cerrar_modulo(self):
        if self.confirmar("Cerrar módulo", "Se cerrará el módulo Aplicaciones y todas sus instancias.",
                          ok="Cerrar módulo", peligro=True):
            self._cerrar_apps("padre")

    def lanzar_modulo(self):
        self._enviar(KERNEL, "launch", APP)

    # ------------------------------------------------------------- diálogos
    def confirmar(self, titulo, mensaje, ok="Aceptar", peligro=False):
        return bool(Dialogo(self, titulo, mensaje, ok=ok, peligro=peligro).resultado)

    def pedir_texto(self, titulo, mensaje):
        return Dialogo(self, titulo, mensaje, entrada=True, ok="Crear").resultado

    def avisar(self, titulo, mensaje):
        Dialogo(self, titulo, mensaje, cancelar=False)

    def detener_sistema(self):
        if self.confirmar("Detener sistema", "Se detendrán el kernel y todos los módulos.",
                          ok="Detener", peligro=True):
            self._enviar(KERNEL, "stop", "Detenido por el usuario desde la GUI")


if __name__ == "__main__":
    Interfaz().mainloop()