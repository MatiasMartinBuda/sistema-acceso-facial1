"""
Memoria persistente del sistema (Sección 6 del TP).

Implementa, sobre SQLite:
- Historial de usuarios (personas registradas, roles, lista negra)
- Decisiones anteriores / logs de acceso (Historial_Accesos)
- Resultados obtenidos (para el módulo de reportes)
- Reglas simples derivadas del historial (ej: 3 rechazos -> lista negra)
- Conversaciones del portero virtual con IA (vía C)
"""
import sqlite3
import datetime
from contextlib import contextmanager

import config


def _connect():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


@contextmanager
def get_conn():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS personas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label_lbph INTEGER UNIQUE,        -- id numérico usado por el modelo LBPH
            dni TEXT UNIQUE,
            nombre TEXT NOT NULL,
            apellido TEXT NOT NULL,
            categoria TEXT NOT NULL,          -- administrador | propietario | inquilino | visita_frecuente
            depto TEXT,
            email TEXT,                       -- para notificar visitas (Camino C)
            tipo_acceso TEXT DEFAULT 'permanente',  -- temporal | permanente | residente
            pin TEXT,                         -- código alternativo (Camino B)
            lista_negra INTEGER DEFAULT 0,
            fecha_alta TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # Migración: si la DB ya existía de una versión anterior sin "email",
        # se agrega la columna sin perder los datos cargados.
        columnas = [row["name"] for row in conn.execute("PRAGMA table_info(personas)")]
        if "email" not in columnas:
            conn.execute("ALTER TABLE personas ADD COLUMN email TEXT")

        conn.execute("""
        CREATE TABLE IF NOT EXISTS logs_acceso (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            persona_id INTEGER,
            dni_declarado TEXT,
            depto_destino TEXT,
            camino TEXT,               -- A | B | C
            resultado TEXT,            -- permitido | denegado | abandono
            score REAL,
            detalle TEXT,
            FOREIGN KEY(persona_id) REFERENCES personas(id)
        );
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS rechazos_visita (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            depto_destino TEXT,
            foto_path TEXT
        );
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS conversaciones_visitantes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha_hora TEXT DEFAULT CURRENT_TIMESTAMP,
            depto_destino TEXT,
            motivo_visita TEXT,
            nivel_riesgo TEXT,             -- BAJO | MEDIO | ALTO
            recomendacion TEXT,            -- AUTORIZAR | RECHAZAR | DERIVAR
            justificacion TEXT,
            transcripcion TEXT,
            decision_final_residente TEXT  -- para la etapa de Evaluación/Aprendizaje
        );
        """)


# ---------------- Personas ----------------

def siguiente_label_lbph():
    with get_conn() as conn:
        row = conn.execute("SELECT MAX(label_lbph) AS m FROM personas").fetchone()
        return 1 if row["m"] is None else row["m"] + 1


def alta_persona(dni, nombre, apellido, categoria, depto, tipo_acceso="permanente", pin=None, email=None):
    label = siguiente_label_lbph()
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO personas (label_lbph, dni, nombre, apellido, categoria, depto, tipo_acceso, pin, email)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (label, dni, nombre, apellido, categoria, depto, tipo_acceso, pin, email))
    return label


def get_persona_by_label(label):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM personas WHERE label_lbph = ?", (label,)).fetchone()


def get_persona_by_pin(depto, pin):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM personas WHERE depto = ? AND pin = ?", (depto, pin)
        ).fetchone()


def esta_en_lista_negra(label):
    with get_conn() as conn:
        row = conn.execute("SELECT lista_negra FROM personas WHERE label_lbph = ?", (label,)).fetchone()
        return bool(row and row["lista_negra"])


def marcar_lista_negra(label):
    with get_conn() as conn:
        conn.execute("UPDATE personas SET lista_negra = 1 WHERE label_lbph = ?", (label,))


def emails_por_depto(depto):
    """Devuelve la lista de emails de todas las personas asociadas a un
    depto (puede haber propietario + inquilino, por ejemplo). Ignora
    registros sin email cargado."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT email FROM personas WHERE depto = ? AND email IS NOT NULL AND email != ''",
            (depto,)
        ).fetchall()
        return [r["email"] for r in rows]


def actualizar_persona(label, **campos):
    """Actualiza uno o más campos de una persona ya registrada
    (ej: depto, email, pin, categoria, tipo_acceso). No toca la biometría."""
    permitidos = {"dni", "nombre", "apellido", "categoria", "depto",
                  "tipo_acceso", "pin", "email"}
    sets = {k: v for k, v in campos.items() if k in permitidos}
    if not sets:
        return False
    columnas = ", ".join(f"{k} = ?" for k in sets)
    valores = list(sets.values()) + [label]
    with get_conn() as conn:
        conn.execute(f"UPDATE personas SET {columnas} WHERE label_lbph = ?", valores)
    return True


def eliminar_persona(label):
    """Borra a la persona de la base. Los logs históricos se conservan,
    pero quedan sin el vínculo a la persona (persona_id = NULL)."""
    persona = get_persona_by_label(label)
    if persona is None:
        return False
    with get_conn() as conn:
        conn.execute("UPDATE logs_acceso SET persona_id = NULL WHERE persona_id = ?", (persona["id"],))
        conn.execute("DELETE FROM personas WHERE id = ?", (persona["id"],))
    return True


def listar_logs_recientes(limite=500):
    with get_conn() as conn:
        return conn.execute("""
            SELECT l.timestamp, l.camino, l.resultado, l.score, l.depto_destino,
                   l.dni_declarado, l.detalle,
                   p.nombre, p.apellido
            FROM logs_acceso l
            LEFT JOIN personas p ON p.id = l.persona_id
            ORDER BY l.timestamp DESC
            LIMIT ?
        """, (limite,)).fetchall()


def listar_personas():
    with get_conn() as conn:
        return conn.execute("SELECT * FROM personas ORDER BY apellido").fetchall()


# ---------------- Logs / auditoría ----------------

def log_evento(camino, resultado, persona_id=None, dni_declarado=None,
               depto_destino=None, score=None, detalle=""):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO logs_acceso (persona_id, dni_declarado, depto_destino, camino, resultado, score, detalle)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (persona_id, dni_declarado, depto_destino, camino, resultado, score, detalle))


def registrar_rechazo_visita(depto_destino, foto_path=None):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO rechazos_visita (depto_destino, foto_path) VALUES (?, ?)",
            (depto_destino, foto_path)
        )


def rechazos_recientes_por_depto(depto_destino, ventana_horas=24):
    """Cuenta cuántas veces fue rechazada una visita hacia distintos deptos
    en la última ventana de tiempo (aproximación al 'rechazado 3 veces' del PDF)."""
    limite = (datetime.datetime.now() - datetime.timedelta(hours=ventana_horas)).isoformat()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM rechazos_visita WHERE timestamp >= ?",
            (limite,)
        ).fetchone()
        return row["c"]


def reporte_semanal():
    """Módulo de Reportes (Sección 6): totales de la última semana."""
    limite = (datetime.datetime.now() - datetime.timedelta(days=7)).isoformat()
    with get_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) c FROM logs_acceso WHERE timestamp >= ?", (limite,)
        ).fetchone()["c"]
        permitidos = conn.execute(
            "SELECT COUNT(*) c FROM logs_acceso WHERE timestamp >= ? AND resultado='permitido'", (limite,)
        ).fetchone()["c"]
        denegados = conn.execute(
            "SELECT COUNT(*) c FROM logs_acceso WHERE timestamp >= ? AND resultado='denegado'", (limite,)
        ).fetchone()["c"]
        por_camino = conn.execute(
            "SELECT camino, COUNT(*) c FROM logs_acceso WHERE timestamp >= ? GROUP BY camino", (limite,)
        ).fetchall()
        return {
            "total": total,
            "permitidos": permitidos,
            "denegados": denegados,
            "por_camino": {r["camino"]: r["c"] for r in por_camino},
        }


# ---------------- Conversaciones del portero con IA (Vía C) ----------------

def guardar_conversacion_visitante(depto_destino, resultado_ia, decision_final_residente=None):
    """
    Guarda el intercambio completo entre el visitante y el portero con IA.

    `resultado_ia` es el objeto ResultadoEvaluacion que devuelve
    ia_portero.ejecutar_via_c_con_ia() (tiene .motivo_visita, .nivel_riesgo,
    .recomendacion, .justificacion, .transcripcion_completa).

    `decision_final_residente` es opcional en el momento de guardar (podés
    completarla después con actualizar_decision_conversacion) para la etapa
    de Evaluación/Aprendizaje: comparar lo que sugirió la IA contra lo que
    finalmente decidió el residente.
    """
    with get_conn() as conn:
        cursor = conn.execute("""
            INSERT INTO conversaciones_visitantes
                (depto_destino, motivo_visita, nivel_riesgo, recomendacion,
                 justificacion, transcripcion, decision_final_residente)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            depto_destino,
            resultado_ia.motivo_visita,
            resultado_ia.nivel_riesgo,
            resultado_ia.recomendacion,
            resultado_ia.justificacion,
            resultado_ia.transcripcion_completa,
            decision_final_residente,
        ))
        return cursor.lastrowid


def actualizar_decision_conversacion(id_conversacion, decision_final_residente):
    with get_conn() as conn:
        conn.execute(
            "UPDATE conversaciones_visitantes SET decision_final_residente = ? WHERE id = ?",
            (decision_final_residente, id_conversacion)
        )


def listar_conversaciones_visitante(depto_destino=None, limite=100):
    """Lista conversaciones recientes del portero con IA. Si se pasa
    `depto_destino`, filtra solo las de ese depto."""
    with get_conn() as conn:
        if depto_destino:
            return conn.execute("""
                SELECT * FROM conversaciones_visitantes
                WHERE depto_destino = ?
                ORDER BY fecha_hora DESC LIMIT ?
            """, (depto_destino, limite)).fetchall()
        return conn.execute("""
            SELECT * FROM conversaciones_visitantes
            ORDER BY fecha_hora DESC LIMIT ?
        """, (limite,)).fetchall()
