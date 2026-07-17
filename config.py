"""
Interfaz gráfica del Sistema Inteligente de Acceso Residencial.

Menú principal con 7 opciones:
  1. Registrar nueva persona
  2. Iniciar reconocimiento en vivo
  3. Agregar fotos a usuario existente
  4. Borrar usuario
  5. Usuarios enrolados (ver / editar)
  6. Reporte (.xlsx en la carpeta reportes/)
  7. Configuración

Ejecutar: python gui.py
"""
import os
import time
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

import cv2
from PIL import Image, ImageTk

import config
import settings
import database
import notificaciones
from recognizer import FaceEngine, DetectorDeParpadeo
import reportes_excel


CATEGORIAS = ["administrador", "propietario", "inquilino", "visita_frecuente"]
TIPOS_ACCESO = ["permanente", "temporal", "residente"]

# --- Tema de colores (punto 4: fondo verde claro, botones verde oscuro) ---
COLOR_FONDO = "#E8F5E9"
COLOR_BOTON = "#1B5E20"
COLOR_BOTON_TEXTO = "#FFFFFF"
COLOR_BOTON_HOVER = "#2E7D32"
COLOR_TITULO = "#1B5E20"


def boton_estilizado(parent, texto, comando, **kwargs):
    opciones = dict(bg=COLOR_BOTON, fg=COLOR_BOTON_TEXTO,
                     activebackground=COLOR_BOTON_HOVER, activeforeground=COLOR_BOTON_TEXTO,
                     relief="flat", cursor="hand2", font=("Segoe UI", 10, "bold"))
    opciones.update(kwargs)
    return tk.Button(parent, text=texto, command=comando, **opciones)


_imagen_puerta_cache = None


def mostrar_puerta_abierta(parent, mensaje=""):
    """Punto 3: ventana emergente con la ilustración de la puerta de vidrio
    abriéndose, cada vez que se concede un acceso (Caminos A, B o C)."""
    global _imagen_puerta_cache
    top = tk.Toplevel(parent)
    top.title("Acceso concedido")
    top.configure(bg=COLOR_FONDO)
    top.resizable(False, False)
    top.attributes("-topmost", True)

    try:
        img = Image.open(config.IMAGEN_PUERTA_ABIERTA)
        _imagen_puerta_cache = ImageTk.PhotoImage(img)  # referencia fuerte, evita el garbage collector
        tk.Label(top, image=_imagen_puerta_cache, bg=COLOR_FONDO).pack(padx=12, pady=(12, 0))
    except Exception:
        tk.Label(top, text="🚪", font=("Segoe UI", 60), bg=COLOR_FONDO).pack(padx=40, pady=40)

    tk.Label(top, text="✅ ACCESO CONCEDIDO", font=("Segoe UI", 14, "bold"),
             fg=COLOR_TITULO, bg=COLOR_FONDO).pack(pady=(10, 0))
    if mensaje:
        tk.Label(top, text=mensaje, font=("Segoe UI", 10), bg=COLOR_FONDO).pack(pady=(0, 12))

    top.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() // 2) - (top.winfo_width() // 2)
    y = parent.winfo_rooty() + (parent.winfo_height() // 2) - (top.winfo_height() // 2)
    top.geometry(f"+{max(x,0)}+{max(y,0)}")

    top.after(3500, top.destroy)  # se cierra sola


# ==========================================================================
# Ventana principal
# ==========================================================================

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Sistema de Acceso Facial - Edificio")
        self.geometry("480x580")
        self.resizable(False, False)
        self.configure(bg=COLOR_FONDO)

        database.init_db()
        os.makedirs(config.ROSTROS_DIR, exist_ok=True)
        os.makedirs(config.REPORTES_DIR, exist_ok=True)

        self.engine = FaceEngine()

        tk.Label(self, text="Sistema Inteligente de Acceso",
                 font=("Segoe UI", 16, "bold"), bg=COLOR_FONDO, fg=COLOR_TITULO).pack(pady=(20, 0))
        tk.Label(self, text="Reconocimiento facial - Edificio",
                 font=("Segoe UI", 10), bg=COLOR_FONDO, fg="#33691E").pack(pady=(0, 20))

        botones = [
            ("1. Registrar nueva persona", self.abrir_registro),
            ("2. Iniciar reconocimiento en vivo", self.abrir_reconocimiento),
            ("3. Agregar fotos a usuario existente", self.abrir_agregar_fotos),
            ("4. Borrar usuario", self.abrir_borrar_usuario),
            ("5. Usuarios enrolados", self.abrir_usuarios),
            ("6. Generar reporte (.xlsx)", self.generar_reporte),
            ("7. Configuración", self.abrir_configuracion),
        ]
        for texto, comando in botones:
            boton_estilizado(self, texto, comando, width=34, height=2).pack(pady=6)

        self.status = tk.Label(self, text=self._resumen_estado(), fg="#33691E", bg=COLOR_FONDO,
                                font=("Segoe UI", 9))
        self.status.pack(side="bottom", pady=10)

    def _resumen_estado(self):
        n = len(database.listar_personas())
        modelo = "modelo entrenado" if self.engine.modelo_disponible() else "sin modelo entrenado todavía"
        return f"{n} persona(s) registrada(s) — {modelo}"

    def refrescar_status(self):
        self.status.config(text=self._resumen_estado())

    # ---- Handlers de los 7 botones ----

    def abrir_registro(self):
        RegistroDialog(self, self.engine, on_close=self.refrescar_status)

    def abrir_reconocimiento(self):
        if not self.engine.modelo_disponible():
            if not messagebox.askyesno(
                "Sin personas enroladas",
                "Todavía no enrolaste a nadie, así que cualquier rostro va a "
                "caer en el Camino B/C. ¿Querés continuar igual?"
            ):
                return
        ReconocimientoWindow(self, self.engine)

    def abrir_agregar_fotos(self):
        personas = database.listar_personas()
        if not personas:
            messagebox.showinfo("Sin usuarios", "Todavía no hay personas registradas.")
            return
        SeleccionarUsuarioDialog(
            self, personas, titulo="Agregar fotos a usuario existente",
            on_seleccionar=lambda p: self._agregar_fotos_a(p)
        )

    def _agregar_fotos_a(self, persona):
        CapturaFotosWindow(
            self, self.engine, persona["label_lbph"],
            f"{persona['nombre']} {persona['apellido']}",
            on_finish=self.refrescar_status
        )

    def abrir_borrar_usuario(self):
        personas = database.listar_personas()
        if not personas:
            messagebox.showinfo("Sin usuarios", "Todavía no hay personas registradas.")
            return
        SeleccionarUsuarioDialog(
            self, personas, titulo="Borrar usuario",
            on_seleccionar=self._confirmar_borrado
        )

    def _confirmar_borrado(self, persona):
        nombre = f"{persona['nombre']} {persona['apellido']}"
        if not messagebox.askyesno(
            "Confirmar borrado",
            f"¿Seguro que querés borrar a {nombre} (depto {persona['depto']})?\n"
            f"Se eliminan sus datos y sus fotos. Esta acción no se puede deshacer."
        ):
            return

        label = persona["label_lbph"]
        database.eliminar_persona(label)

        carpeta = os.path.join(config.ROSTROS_DIR, str(label))
        if os.path.isdir(carpeta):
            import shutil
            shutil.rmtree(carpeta, ignore_errors=True)

        if not self.engine.entrenar_desde_disco():
            # no quedan fotos de nadie -> borrar el modelo viejo
            if os.path.exists(config.MODELO_PATH):
                os.remove(config.MODELO_PATH)
            self.engine._model_cargado = False

        messagebox.showinfo("Listo", f"{nombre} fue eliminado del sistema.")
        self.refrescar_status()

    def abrir_usuarios(self):
        UsuariosWindow(self, self.engine, on_change=self.refrescar_status)

    def generar_reporte(self):
        try:
            ruta = reportes_excel.generar_reporte_xlsx()
        except Exception as e:
            messagebox.showerror("Error al generar reporte", str(e))
            return
        if messagebox.askyesno("Reporte generado", f"Se guardó en:\n{ruta}\n\n¿Abrir la carpeta?"):
            self._abrir_carpeta(config.REPORTES_DIR)

    @staticmethod
    def _abrir_carpeta(ruta):
        try:
            if os.name == "nt":
                os.startfile(ruta)  # Windows
            elif os.uname().sysname == "Darwin":
                os.system(f'open "{ruta}"')
            else:
                os.system(f'xdg-open "{ruta}"')
        except Exception:
            pass

    def abrir_configuracion(self):
        ConfiguracionDialog(self)


# ==========================================================================
# Registrar nueva persona
# ==========================================================================

class RegistroDialog(tk.Toplevel):
    def __init__(self, master, engine, on_close=None):
        super().__init__(master)
        self.engine = engine
        self.on_close = on_close
        self.title("Registrar nueva persona")
        self.geometry("380x480")
        self.resizable(False, False)
        self.configure(bg=COLOR_FONDO)
        self.grab_set()

        campos = [
            ("DNI", "dni"), ("Nombre", "nombre"), ("Apellido", "apellido"),
            ("Departamento (ej: 4B)", "depto"), ("PIN alternativo (opcional)", "pin"),
            ("Email (opcional)", "email"),
        ]
        self.vars = {}
        for etiqueta, clave in campos:
            tk.Label(self, text=etiqueta, anchor="w", bg=COLOR_FONDO).pack(fill="x", padx=20, pady=(10, 0))
            var = tk.StringVar()
            tk.Entry(self, textvariable=var).pack(fill="x", padx=20)
            self.vars[clave] = var

        tk.Label(self, text="Categoría", anchor="w", bg=COLOR_FONDO).pack(fill="x", padx=20, pady=(10, 0))
        self.categoria = ttk.Combobox(self, values=CATEGORIAS, state="readonly")
        self.categoria.current(2)
        self.categoria.pack(fill="x", padx=20)

        tk.Label(self, text="Tipo de acceso", anchor="w", bg=COLOR_FONDO).pack(fill="x", padx=20, pady=(10, 0))
        self.tipo_acceso = ttk.Combobox(self, values=TIPOS_ACCESO, state="readonly")
        self.tipo_acceso.current(0)
        self.tipo_acceso.pack(fill="x", padx=20)

        tk.Button(self, text="Guardar y capturar fotos", bg=COLOR_BOTON, fg=COLOR_BOTON_TEXTO,
                  font=("Segoe UI", 10, "bold"), command=self.guardar).pack(pady=20, fill="x", padx=20)

    def guardar(self):
        dni = self.vars["dni"].get().strip()
        nombre = self.vars["nombre"].get().strip()
        apellido = self.vars["apellido"].get().strip()
        if not (dni and nombre and apellido):
            messagebox.showwarning("Faltan datos", "DNI, nombre y apellido son obligatorios.")
            return

        try:
            label = database.alta_persona(
                dni=dni, nombre=nombre, apellido=apellido,
                categoria=self.categoria.get(), depto=self.vars["depto"].get().strip(),
                tipo_acceso=self.tipo_acceso.get(),
                pin=self.vars["pin"].get().strip() or None,
                email=self.vars["email"].get().strip() or None,
            )
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo registrar: {e}")
            return

        nombre_completo = f"{nombre} {apellido}"
        self.destroy()
        CapturaFotosWindow(self.master, self.engine, label, nombre_completo, on_finish=self.on_close)


# ==========================================================================
# Selección genérica de usuario (usada por Borrar / Agregar fotos)
# ==========================================================================

class SeleccionarUsuarioDialog(tk.Toplevel):
    def __init__(self, master, personas, titulo, on_seleccionar):
        super().__init__(master)
        self.title(titulo)
        self.geometry("420x360")
        self.configure(bg=COLOR_FONDO)
        self.grab_set()
        self.on_seleccionar = on_seleccionar
        self.personas = personas

        tk.Label(self, text=titulo, font=("Segoe UI", 12, "bold"), bg=COLOR_FONDO, fg=COLOR_TITULO).pack(pady=10)

        cont = tk.Frame(self, bg=COLOR_FONDO)
        cont.pack(fill="both", expand=True, padx=10)
        self.listbox = tk.Listbox(cont, font=("Consolas", 10))
        self.listbox.pack(side="left", fill="both", expand=True)
        scroll = tk.Scrollbar(cont, command=self.listbox.yview)
        scroll.pack(side="right", fill="y")
        self.listbox.config(yscrollcommand=scroll.set)

        for p in personas:
            self.listbox.insert(
                "end",
                f"#{p['label_lbph']:>3}  {p['apellido']}, {p['nombre']}  -  depto {p['depto']}"
            )

        tk.Button(self, text="Seleccionar", bg=COLOR_BOTON, fg=COLOR_BOTON_TEXTO,
                  command=self._confirmar).pack(pady=10, fill="x", padx=10)

    def _confirmar(self):
        sel = self.listbox.curselection()
        if not sel:
            messagebox.showwarning("Nada seleccionado", "Elegí una persona de la lista.")
            return
        persona = self.personas[sel[0]]
        self.destroy()
        self.on_seleccionar(persona)


# ==========================================================================
# Captura de fotos por webcam (usada en alta y en "agregar fotos")
# ==========================================================================

class CapturaFotosWindow(tk.Toplevel):
    def __init__(self, master, engine, label, nombre_completo, on_finish=None):
        super().__init__(master)
        self.engine = engine
        self.label_persona = label
        self.on_finish = on_finish
        self.title(f"Capturando fotos - {nombre_completo}")
        self.geometry("680x560")
        self.configure(bg=COLOR_FONDO)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.cerrar)

        self.total_fotos = int(settings.get("FOTOS_POR_ENROLAMIENTO"))
        self.capturas = 0
        self.carpeta = os.path.join(config.ROSTROS_DIR, str(label))
        os.makedirs(self.carpeta, exist_ok=True)
        # continuar la numeración si ya había fotos (caso "agregar fotos")
        existentes = [f for f in os.listdir(self.carpeta) if f.lower().endswith(".jpg")]
        self.offset = len(existentes)

        tk.Label(self, text=f"Mirá a la cámara, {nombre_completo}",
                 font=("Segoe UI", 12, "bold"), bg=COLOR_FONDO, fg=COLOR_TITULO).pack(pady=8)
        self.video_label = tk.Label(self, bg=COLOR_FONDO)
        self.video_label.pack()
        self.progreso = ttk.Progressbar(self, maximum=self.total_fotos, length=500)
        self.progreso.pack(pady=10)
        self.estado_label = tk.Label(self, text=f"Capturas: 0/{self.total_fotos}", bg=COLOR_FONDO)
        self.estado_label.pack()
        boton_estilizado(self, "Cancelar", self.cerrar).pack(pady=8)

        self.cap = cv2.VideoCapture(int(settings.get("CAMERA_INDEX")))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.FRAME_WIDTH)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.FRAME_HEIGHT)
        self.activo = True
        self._ultimo_guardado = 0

        if not self.cap.isOpened():
            messagebox.showerror("Cámara", "No se pudo abrir la cámara.")
            self.cerrar()
            return

        self._loop()

    def _loop(self):
        if not self.activo:
            return
        ok, frame = self.cap.read()
        if ok:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            rostros = self.engine.detectar_rostros(gray)
            for (x, y, w, h) in rostros:
                cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
                ahora = time.time()
                if self.capturas < self.total_fotos and (ahora - self._ultimo_guardado) > 0.25:
                    recorte = cv2.resize(gray[y:y + h, x:x + w], (200, 200))
                    idx = self.offset + self.capturas
                    cv2.imwrite(os.path.join(self.carpeta, f"{idx:03d}.jpg"), recorte)
                    self.capturas += 1
                    self._ultimo_guardado = ahora
                break

            self._mostrar_frame(frame)
            self.progreso["value"] = self.capturas
            self.estado_label.config(text=f"Capturas: {self.capturas}/{self.total_fotos}")

        if self.capturas >= self.total_fotos:
            self._finalizar()
            return

        self.after(30, self._loop)

    def _mostrar_frame(self, frame):
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb)
        imgtk = ImageTk.PhotoImage(image=img)
        self.video_label.imgtk = imgtk
        self.video_label.config(image=imgtk)

    def _finalizar(self):
        self.activo = False
        if self.cap.isOpened():
            self.cap.release()
        self.estado_label.config(text="Reentrenando modelo...")
        self.update()
        self.engine.entrenar_desde_disco()
        messagebox.showinfo("Listo", f"Se capturaron {self.capturas} fotos y se actualizó el modelo.")
        if self.on_finish:
            self.on_finish()
        self.destroy()

    def cerrar(self):
        self.activo = False
        if self.cap.isOpened():
            self.cap.release()
        self.destroy()


# ==========================================================================
# Usuarios enrolados (ver / editar)
# ==========================================================================

class UsuariosWindow(tk.Toplevel):
    def __init__(self, master, engine, on_change=None):
        super().__init__(master)
        self.engine = engine
        self.on_change = on_change
        self.title("Usuarios enrolados")
        self.geometry("860x460")
        self.configure(bg=COLOR_FONDO)
        self.grab_set()

        tk.Label(self, text="Usuarios enrolados", font=("Segoe UI", 13, "bold"),
                 bg=COLOR_FONDO, fg=COLOR_TITULO).pack(pady=(12, 0))
        tk.Label(self, text="Doble clic sobre un usuario (o el botón de abajo) para editar y guardar cambios",
                 font=("Segoe UI", 9), bg=COLOR_FONDO, fg="#33691E").pack(pady=(0, 8))

        columnas = ("id", "nombre", "dni", "categoria", "depto", "email", "lista_negra")
        self.tree = ttk.Treeview(self, columns=columnas, show="headings", height=15)
        titulos = {"id": "#", "nombre": "Nombre", "dni": "DNI", "categoria": "Categoría",
                   "depto": "Depto", "email": "Email", "lista_negra": "Lista negra"}
        anchos = {"id": 40, "nombre": 160, "dni": 90, "categoria": 110,
                  "depto": 70, "email": 190, "lista_negra": 90}
        for c in columnas:
            self.tree.heading(c, text=titulos[c])
            self.tree.column(c, width=anchos[c], anchor="w")
        self.tree.pack(fill="both", expand=True, padx=10, pady=10)
        self.tree.bind("<Double-1>", lambda e: self.editar_seleccionado())

        botonera = tk.Frame(self, bg=COLOR_FONDO)
        botonera.pack(pady=(0, 12))
        boton_estilizado(botonera, "✏️  Editar y guardar cambios", self.editar_seleccionado,
                          width=24).pack(side="left", padx=5)
        boton_estilizado(botonera, "Cerrar", self.destroy, bg="#555", width=12).pack(side="left", padx=5)

        self._cargar()

    def _cargar(self):
        self.tree.delete(*self.tree.get_children())
        for p in database.listar_personas():
            self.tree.insert("", "end", iid=p["label_lbph"], values=(
                p["label_lbph"], f"{p['nombre']} {p['apellido']}", p["dni"],
                p["categoria"], p["depto"], p["email"] or "-",
                "Sí" if p["lista_negra"] else "No",
            ))

    def editar_seleccionado(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Nada seleccionado", "Elegí un usuario de la tabla.")
            return
        label = int(sel[0])
        persona = database.get_persona_by_label(label)
        EditarUsuarioDialog(self, persona, on_guardado=self._on_guardado)

    def _on_guardado(self):
        self._cargar()
        if self.on_change:
            self.on_change()


class EditarUsuarioDialog(tk.Toplevel):
    """Permite agregarle/editar información a un propietario ya registrado
    (depto, email, PIN, categoría, tipo de acceso, lista negra)."""

    def __init__(self, master, persona, on_guardado=None):
        super().__init__(master)
        self.persona = persona
        self.on_guardado = on_guardado
        self.title(f"Editar - {persona['nombre']} {persona['apellido']}")
        self.geometry("360x480")
        self.configure(bg=COLOR_FONDO)
        self.grab_set()

        campos = [
            ("DNI", "dni", persona["dni"]),
            ("Nombre", "nombre", persona["nombre"]),
            ("Apellido", "apellido", persona["apellido"]),
            ("Departamento", "depto", persona["depto"]),
            ("PIN alternativo", "pin", persona["pin"] or ""),
            ("Email", "email", persona["email"] or ""),
        ]
        self.vars = {}
        for etiqueta, clave, valor in campos:
            tk.Label(self, text=etiqueta, anchor="w", bg=COLOR_FONDO).pack(fill="x", padx=20, pady=(8, 0))
            var = tk.StringVar(value=valor)
            tk.Entry(self, textvariable=var).pack(fill="x", padx=20)
            self.vars[clave] = var

        tk.Label(self, text="Categoría", anchor="w", bg=COLOR_FONDO).pack(fill="x", padx=20, pady=(8, 0))
        self.categoria = ttk.Combobox(self, values=CATEGORIAS, state="readonly")
        self.categoria.set(persona["categoria"])
        self.categoria.pack(fill="x", padx=20)

        tk.Label(self, text="Tipo de acceso", anchor="w", bg=COLOR_FONDO).pack(fill="x", padx=20, pady=(8, 0))
        self.tipo_acceso = ttk.Combobox(self, values=TIPOS_ACCESO, state="readonly")
        self.tipo_acceso.set(persona["tipo_acceso"])
        self.tipo_acceso.pack(fill="x", padx=20)

        self.lista_negra_var = tk.BooleanVar(value=bool(persona["lista_negra"]))
        tk.Checkbutton(self, text="Marcar en lista negra (acceso prohibido)",
                        variable=self.lista_negra_var, bg=COLOR_FONDO).pack(pady=10, anchor="w", padx=20)

        boton_estilizado(self, "💾  Guardar cambios", self.guardar).pack(pady=15, fill="x", padx=20)

    def guardar(self):
        database.actualizar_persona(
            self.persona["label_lbph"],
            dni=self.vars["dni"].get().strip(),
            nombre=self.vars["nombre"].get().strip(),
            apellido=self.vars["apellido"].get().strip(),
            depto=self.vars["depto"].get().strip(),
            pin=self.vars["pin"].get().strip() or None,
            email=self.vars["email"].get().strip() or None,
            categoria=self.categoria.get(),
            tipo_acceso=self.tipo_acceso.get(),
        )
        with database.get_conn() as conn:
            conn.execute("UPDATE personas SET lista_negra = ? WHERE label_lbph = ?",
                         (1 if self.lista_negra_var.get() else 0, self.persona["label_lbph"]))

        messagebox.showinfo("Listo", "Los datos se actualizaron correctamente.")
        self.destroy()
        if self.on_guardado:
            self.on_guardado()


# ==========================================================================
# Reconocimiento en vivo
# ==========================================================================

class ReconocimientoWindow(tk.Toplevel):
    def __init__(self, master, engine):
        super().__init__(master)
        self.engine = engine
        self.title("Reconocimiento en vivo")
        self.geometry("760x660")
        self.configure(bg=COLOR_FONDO)
        self.protocol("WM_DELETE_WINDOW", self.cerrar)

        self.video_label = tk.Label(self, bg=COLOR_FONDO)
        self.video_label.pack(pady=8)
        self.estado_label = tk.Label(self, text="Estado: ESPERA", font=("Segoe UI", 12, "bold"),
                                      bg=COLOR_FONDO, fg=COLOR_TITULO)
        self.estado_label.pack()

        tk.Label(self, text="Registro de eventos:", anchor="w", bg=COLOR_FONDO).pack(
            fill="x", padx=10, pady=(10, 0))
        self.log = tk.Text(self, height=8, state="disabled", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        boton_estilizado(self, "Detener", self.cerrar, bg="#555").pack(pady=(0, 10))

        self.cap = cv2.VideoCapture(int(settings.get("CAMERA_INDEX")))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.FRAME_WIDTH)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.FRAME_HEIGHT)
        if not self.cap.isOpened():
            messagebox.showerror("Cámara", "No se pudo abrir la cámara.")
            self.destroy()
            return

        self.liveness = DetectorDeParpadeo()
        self.frames_estable = 0
        self.activo = True
        self.evaluando = False
        self._loop()

    def _log(self, texto):
        self.log.config(state="normal")
        self.log.insert("end", f"{time.strftime('%H:%M:%S')}  {texto}\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def _loop(self):
        if not self.activo:
            return
        ok, frame = self.cap.read()
        if not ok:
            self.after(30, self._loop)
            return

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        rostros = self.engine.detectar_rostros(gray)

        if len(rostros) == 0 or self.evaluando:
            self.frames_estable = 0
            self.liveness.reset()
            self.estado_label.config(text="Estado: ESPERA")
        else:
            (x, y, w, h) = max(rostros, key=lambda r: r[2] * r[3])
            cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 255), 2)
            self.frames_estable += 1

            roi_gray = gray[y:y + h, x:x + w]
            ojos = self.engine.detectar_ojos(roi_gray)
            self.liveness.actualizar(hay_ojos=len(ojos) > 0)
            self.estado_label.config(text="Estado: ANALIZANDO (prueba de vida)...")

            listo = (
                self.frames_estable >= config.FRAMES_MINIMOS_ROSTRO_ESTABLE
                and (self.liveness.confirmado()
                     or not settings.get("LIVENESS_REQUIERE_PARPADEO")
                     or self.liveness.expirado())
            )
            if listo:
                self.evaluando = True
                self._mostrar_frame(frame)
                self.after(10, lambda: self._evaluar(roi_gray))
                return

        self._mostrar_frame(frame)
        self.after(30, self._loop)

    def _mostrar_frame(self, frame):
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb)
        imgtk = ImageTk.PhotoImage(image=img)
        self.video_label.imgtk = imgtk
        self.video_label.config(image=imgtk)

    def _evaluar(self, roi_gray):
        t0 = time.time()
        label, score = self.engine.predecir(roi_gray)
        latencia = round(time.time() - t0, 3)
        umbral = float(settings.get("UMBRAL_CONFIANZA_RESIDENTE"))

        if not self.liveness.confirmado() and settings.get("LIVENESS_REQUIERE_PARPADEO"):
            self._log("⛔ Denegado: no se pudo confirmar prueba de vida (posible foto/pantalla).")
            database.log_evento("A", "denegado", score=score, detalle="Liveness no confirmado")
        elif label is not None and score >= umbral:
            persona = database.get_persona_by_label(label)
            if persona is None:
                self._camino_b_o_c()
            elif persona["lista_negra"]:
                self._log("⛔ Persona en lista negra detectada. Acceso denegado automáticamente.")
                database.log_evento("A", "denegado", persona_id=persona["id"], score=score, detalle="Lista negra")
            else:
                self._log(f"✅ Acceso permitido: {persona['nombre']} {persona['apellido']} "
                          f"(score {score}%, depto {persona['depto']})")
                database.log_evento("A", "permitido", persona_id=persona["id"], score=score,
                                     detalle="Reconocimiento directo")
                self._acceso_concedido(
                    depto=persona["depto"],
                    nombre_persona=f"{persona['nombre']} {persona['apellido']}",
                    metodo="Reconocimiento facial",
                    mensaje=f"{persona['nombre']} {persona['apellido']} - depto {persona['depto']}",
                )
        else:
            self._camino_b_o_c()

        self._log(f"(latencia de reconocimiento: {latencia}s)")
        self.evaluando = False
        self.frames_estable = 0
        self.liveness.reset()
        if self.activo:
            self.after(30, self._loop)

    def _acceso_concedido(self, depto, nombre_persona, metodo, mensaje=""):
        """Centraliza lo que pasa cada vez que se abre la puerta (puntos 1, 2 y 3):
        muestra la imagen de la puerta y notifica por email al propietario del depto."""
        mostrar_puerta_abierta(self, mensaje)
        emails = database.emails_por_depto(depto) if depto else []
        if emails:
            if notificaciones.enviar_notificacion_ingreso(depto, emails, nombre_persona, metodo):
                self._log(f"📧 Se notificó por email al propietario del depto {depto}.")
        elif depto:
            self._log(f"(Depto {depto} no tiene email cargado, no se notificó por correo)")

    def _camino_b_o_c(self):
        es_residente = messagebox.askyesno("No reconocido", "No te reconozco. ¿Sos residente del edificio?")
        if es_residente:
            self._camino_b()
        else:
            self._camino_c()

    def _camino_b(self):
        depto = simpledialog.askstring("Camino B", "Ingresá tu número de depto:", parent=self)
        if depto is None:
            self._log("Camino B: se canceló el ingreso.")
            return
        pin = simpledialog.askstring("Camino B", "Ingresá tu código/PIN:", parent=self, show="*")
        persona = database.get_persona_by_pin(depto.strip(), (pin or "").strip())

        if persona and not persona["lista_negra"]:
            self._log(f"✅ Acceso concedido por PIN: {persona['nombre']} {persona['apellido']} - depto {depto}")
            database.log_evento("B", "permitido", persona_id=persona["id"], depto_destino=depto,
                                 detalle="Validado por PIN")
            self._acceso_concedido(
                depto=depto, nombre_persona=f"{persona['nombre']} {persona['apellido']}",
                metodo="PIN (sin reconocimiento facial)",
                mensaje=f"{persona['nombre']} {persona['apellido']} - depto {depto} (vía PIN)",
            )
        else:
            self._log(f"⛔ PIN inválido para depto {depto}. Se deriva a guardia/administración.")
            database.log_evento("B", "denegado", depto_destino=depto, detalle="PIN inválido")

    def _camino_c(self):
        depto = simpledialog.askstring("Camino C - Visita", "¿A qué depto te dirigís? (ej: 4B)", parent=self)
        if depto is None:
            self._log("Camino C: se canceló el ingreso.")
            return
        depto = depto.strip()

        if database.rechazos_recientes_por_depto(depto) >= config.RECHAZOS_PARA_LISTA_NEGRA:
            self._log(f"⛔ Depto {depto}: bloqueo automático por rechazos repetidos recientes.")
            database.log_evento("C", "denegado", depto_destino=depto, detalle="Bloqueo por rechazos repetidos")
            return

        emails = database.emails_por_depto(depto)
        if notificaciones.enviar_notificacion_visita(depto, emails, detalle="Videollamada simulada desde el tótem."):
            self._log(f"📧 Notificación enviada a los residentes del depto {depto}.")

        resultado = self._preguntar_autorizacion(depto)
        if resultado == "s":
            self._log(f"✅ Acceso autorizado por depto {depto}.")
            database.log_evento("C", "permitido", depto_destino=depto, detalle="Autorizado por videollamada")
            self._acceso_concedido(
                depto=depto, nombre_persona="Visita (sin registro facial)",
                metodo="Visita autorizada por videollamada",
                mensaje=f"Visita autorizada - depto {depto}",
            )
        else:
            motivo = "Rechazado por residente" if resultado == "n" else "No atendió la llamada"
            self._log(f"⛔ Acceso denegado - depto {depto} ({motivo}).")
            database.registrar_rechazo_visita(depto)
            database.log_evento("C", "denegado", depto_destino=depto, detalle=motivo)

    def _preguntar_autorizacion(self, depto):
        resultado = {"valor": None}
        top = tk.Toplevel(self)
        top.title("Videollamada simulada")
        top.configure(bg=COLOR_FONDO)
        top.grab_set()
        tk.Label(top, text=f"Simulando videollamada al depto {depto}.\n¿Autoriza el ingreso?",
                 padx=20, pady=15, font=("Segoe UI", 11), bg=COLOR_FONDO).pack()
        frame = tk.Frame(top, bg=COLOR_FONDO)
        frame.pack(pady=(0, 15))

        def elegir(v):
            resultado["valor"] = v
            top.destroy()

        tk.Button(frame, text="Sí, autorizo", bg="#4CAF50", fg="white", width=12,
                  command=lambda: elegir("s")).pack(side="left", padx=5)
        tk.Button(frame, text="No autorizo", bg="#f44336", fg="white", width=12,
                  command=lambda: elegir("n")).pack(side="left", padx=5)
        tk.Button(frame, text="No atiende", width=12,
                  command=lambda: elegir(None)).pack(side="left", padx=5)

        self.wait_window(top)
        return resultado["valor"]

    def cerrar(self):
        self.activo = False
        if self.cap.isOpened():
            self.cap.release()
        self.destroy()


# ==========================================================================
# Configuración
# ==========================================================================

class ConfiguracionDialog(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title("Configuración")
        self.geometry("380x620")
        self.configure(bg=COLOR_FONDO)
        self.grab_set()

        actuales = settings.get_all()
        self.vars = {}

        tk.Label(self, text="Cámara y reconocimiento", font=("Segoe UI", 11, "bold"),
                 bg=COLOR_FONDO, fg=COLOR_TITULO).pack(pady=(15, 5), anchor="w", padx=20)

        self._campo_texto("Índice de cámara (0, 1, 2...)", "CAMERA_INDEX", actuales)
        self._campo_texto("Umbral de confianza (%) para acceso directo", "UMBRAL_CONFIANZA_RESIDENTE", actuales)
        self._campo_texto("Fotos a capturar por enrolamiento", "FOTOS_POR_ENROLAMIENTO", actuales)

        self.liveness_var = tk.BooleanVar(value=actuales["LIVENESS_REQUIERE_PARPADEO"])
        tk.Checkbutton(self, text="Exigir prueba de vida (parpadeo)",
                        variable=self.liveness_var, bg=COLOR_FONDO).pack(anchor="w", padx=20, pady=5)

        tk.Label(self, text="Notificaciones por email", font=("Segoe UI", 11, "bold"),
                 bg=COLOR_FONDO, fg=COLOR_TITULO).pack(pady=(15, 5), anchor="w", padx=20)

        self.notificar_var = tk.BooleanVar(value=actuales["NOTIFICAR_VISITAS_POR_EMAIL"])
        tk.Checkbutton(self, text="Notificar accesos e ingresos por email",
                        variable=self.notificar_var, bg=COLOR_FONDO).pack(anchor="w", padx=20, pady=5)

        self._campo_texto("Servidor SMTP", "SMTP_HOST", actuales)
        self._campo_texto("Puerto SMTP", "SMTP_PORT", actuales)
        self._campo_texto("Usuario / email remitente", "SMTP_USER", actuales)
        self._campo_texto("Contraseña de aplicación", "SMTP_PASSWORD", actuales, oculto=True)
        self._campo_texto("Nombre del remitente", "SMTP_FROM_NAME", actuales)

        tk.Label(self, text="IA del Portero Virtual (Vía C)", font=("Segoe UI", 11, "bold"),
                 bg=COLOR_FONDO, fg=COLOR_TITULO).pack(pady=(15, 5), anchor="w", padx=20)
        tk.Label(self, text="Se usa para el agente conversacional del intercomunicador (STT + LLM + TTS).",
                 font=("Segoe UI", 8), bg=COLOR_FONDO, fg="#33691E", wraplength=330, justify="left").pack(
                     anchor="w", padx=20)

        self._campo_texto("Anthropic API Key", "ANTHROPIC_API_KEY", actuales, oculto=True)

        boton_estilizado(self, "Guardar configuración", self.guardar).pack(pady=20, fill="x", padx=20)

    def _campo_texto(self, etiqueta, clave, actuales, oculto=False):
        tk.Label(self, text=etiqueta, anchor="w", bg=COLOR_FONDO).pack(fill="x", padx=20, pady=(6, 0))
        var = tk.StringVar(value=str(actuales.get(clave, "")))
        tk.Entry(self, textvariable=var, show="*" if oculto else "").pack(fill="x", padx=20)
        self.vars[clave] = var

    def guardar(self):
        try:
            nuevos = {
                "CAMERA_INDEX": int(self.vars["CAMERA_INDEX"].get()),
                "UMBRAL_CONFIANZA_RESIDENTE": float(self.vars["UMBRAL_CONFIANZA_RESIDENTE"].get()),
                "FOTOS_POR_ENROLAMIENTO": int(self.vars["FOTOS_POR_ENROLAMIENTO"].get()),
                "SMTP_PORT": int(self.vars["SMTP_PORT"].get()),
            }
        except ValueError:
            messagebox.showerror("Error", "Índice de cámara, umbral, fotos y puerto deben ser números.")
            return

        nuevos["LIVENESS_REQUIERE_PARPADEO"] = self.liveness_var.get()
        nuevos["NOTIFICAR_VISITAS_POR_EMAIL"] = self.notificar_var.get()
        for clave in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM_NAME", "ANTHROPIC_API_KEY"):
            nuevos[clave] = self.vars[clave].get()

        settings.guardar(nuevos)
        messagebox.showinfo("Listo", "Configuración guardada.")
        self.destroy()


if __name__ == "__main__":
    App().mainloop()