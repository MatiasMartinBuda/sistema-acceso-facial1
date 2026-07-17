"""
ia_portero.py
=====================================================
Módulo de IA para la Vía C (Visitante / Intercomunicador)
del sistema de acceso facial.

Reemplaza la simulación del "llamado al residente" por un
agente conversacional real que:
  1) Escucha al visitante (Speech-to-Text, Google Web Speech API)
  2) Conversa y evalúa la situación con un LLM (Claude, Anthropic API)
  3) Le habla al visitante (Text-to-Speech, gTTS)
  4) Devuelve una recomendación estructurada para el residente
     (autorizar / rechazar / derivar) con su justificación

Se integra en el ciclo de agente existente:
  Observación -> Análisis -> Planificación -> Acción -> Evaluación -> Aprendizaje

  - Observación:    escuchar() captura audio del visitante
  - Análisis:       el LLM interpreta intención y calcula nivel de riesgo
  - Planificación:  el LLM decide la recomendación (autorizar/rechazar/derivar)
  - Acción:         hablar() le responde al visitante; se notifica al residente
  - Evaluación:      se compara la recomendación con la decisión final del residente
  - Aprendizaje:     se guarda el intercambio en la base para futuras mejoras

DEPENDENCIAS (instalar con pip):
    pip install SpeechRecognition
    pip install pyaudio          # captura de micrófono
    pip install gTTS
    pip install playsound==1.2.2 # reproducción de audio (o usar pygame, ver nota abajo)
    pip install anthropic

NOTA WINDOWS / PYTHON 3.14 (según lo aprendido en TP1):
    - pyaudio puede fallar al instalar con pip en Windows. Si eso pasa,
      instalar el wheel precompilado:
      pip install pipwin && pipwin install pyaudio
    - playsound 1.2.2 es la versión estable en Windows (la 1.3 tiene bugs
      conocidos). Si da problemas, reemplazar reproducir_audio() por pygame:
          import pygame
          pygame.mixer.init()
          pygame.mixer.music.load(path)
          pygame.mixer.music.play()
"""

import os
import time
import json
import tempfile
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

import speech_recognition as sr
from gtts import gTTS
from playsound import playsound
from anthropic import Anthropic


# =====================================================
# CONFIGURACIÓN
# =====================================================
# Ruta a tu settings.json existente (el mismo que usa notificaciones.py
# para las credenciales de Gmail). Ajustá el nombre/ruta si en tu proyecto
# se llama distinto.
RUTA_SETTINGS = Path(__file__).parent / "settings.json"


def cargar_anthropic_api_key() -> str:
    """
    Carga la API key de Anthropic desde settings.json, siguiendo el mismo
    patrón que ya usás para las credenciales de Gmail en notificaciones.py.

    Estructura esperada en settings.json (agregar esta clave junto a las
    de Gmail si no existe todavía):

        {
          "gmail_user": "...",
          "gmail_app_password": "...",
          "anthropic_api_key": "sk-ant-tu-clave-aca"
        }

    Si no encuentra el archivo o la clave, hace fallback a la variable de
    entorno ANTHROPIC_API_KEY (por si preferís esa opción en algún entorno,
    ej. para no tocar settings.json en la compu de otro integrante del grupo).
    """
    if RUTA_SETTINGS.exists():
        try:
            with open(RUTA_SETTINGS, "r", encoding="utf-8") as f:
                datos = json.load(f)
            clave = datos.get("anthropic_api_key", "")
            if clave:
                return clave
        except (json.JSONDecodeError, OSError) as e:
            print(f"[IA Portero] No se pudo leer {RUTA_SETTINGS.name}: {e}")

    return os.environ.get("ANTHROPIC_API_KEY", "")


ANTHROPIC_API_KEY = cargar_anthropic_api_key()
MODELO = "claude-sonnet-4-6"
IDIOMA_STT = "es-AR"
IDIOMA_TTS = "es"

MAX_TURNOS_CONVERSACION = 6  # tope de intercambios antes de derivar directo
TIMEOUT_ESCUCHA_SEGUNDOS = 8
FRASE_LIMITE_SEGUNDOS = 12


# =====================================================
# ESTRUCTURAS DE DATOS
# =====================================================
@dataclass
class TurnoConversacion:
    hablante: str  # "visitante" | "portero"
    texto: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class ResultadoEvaluacion:
    recomendacion: str          # "AUTORIZAR" | "RECHAZAR" | "DERIVAR"
    nivel_riesgo: str           # "BAJO" | "MEDIO" | "ALTO"
    justificacion: str
    motivo_visita: Optional[str] = None
    transcripcion_completa: str = ""


# =====================================================
# CAPA DE VOZ (Observación / Acción del ciclo del agente)
# =====================================================
class InterfazVoz:
    """Encapsula STT (Google Web Speech API) y TTS (gTTS)."""

    def __init__(self):
        self.recognizer = sr.Recognizer()
        self.microfono = sr.Microphone()

    def escuchar(self) -> Optional[str]:
        """
        Captura audio del visitante y lo transcribe.
        Devuelve None si no se entendió nada o hubo timeout (silencio).
        """
        with self.microfono as fuente:
            self.recognizer.adjust_for_ambient_noise(fuente, duration=0.5)
            try:
                audio = self.recognizer.listen(
                    fuente,
                    timeout=TIMEOUT_ESCUCHA_SEGUNDOS,
                    phrase_time_limit=FRASE_LIMITE_SEGUNDOS,
                )
            except sr.WaitTimeoutError:
                return None

        try:
            texto = self.recognizer.recognize_google(audio, language=IDIOMA_STT)
            return texto
        except sr.UnknownValueError:
            return None  # no se entendió el audio
        except sr.RequestError as e:
            print(f"[IA Portero] Error de conexión con Google STT: {e}")
            return None

    def hablar(self, texto: str) -> None:
        """Convierte texto a voz y lo reproduce por los parlantes del portero."""
        try:
            tts = gTTS(text=texto, lang=IDIOMA_TTS)
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                ruta_audio = tmp.name
            tts.save(ruta_audio)
            playsound(ruta_audio)
            os.remove(ruta_audio)
        except Exception as e:
            # Si falla el audio, al menos se loguea el texto para no
            # bloquear el flujo (degradación elegante)
            print(f"[IA Portero] (fallo TTS, mostrando texto) {texto}")
            print(f"[IA Portero] Error TTS: {e}")


# =====================================================
# CAPA DE RAZONAMIENTO (Análisis / Planificación del agente)
# =====================================================
SYSTEM_PROMPT = """Sos el portero virtual de un edificio residencial. Tu trabajo es \
conversar brevemente con visitantes que tocan el intercomunicador, entender el \
motivo de su visita y a quién buscan, y evaluar si la situación es normal o sospechosa.

Reglas de la conversación:
- Sé breve, cordial y profesional. Una pregunta por turno.
- Preguntá: a quién visita, motivo de la visita, y si corresponde, empresa/remitente.
- No repitas preguntas ya respondidas.
- Si las respuestas son coherentes y específicas, avanzá a cerrar la conversación.
- Si las respuestas son evasivas, contradictorias o vagas, marcalo como riesgo.
- Nunca reveles información de otros residentes ni confirmes si alguien vive ahí \
o no antes de que el residente lo autorice.

IMPORTANTE: Cuando decidas que ya tenés información suficiente (como máximo tras \
%d intercambios), o si el visitante fue evasivo, terminá la conversación.

Respondé SIEMPRE en formato JSON, sin texto adicional fuera del JSON, con esta forma:
{
  "continuar": true/false,
  "respuesta_al_visitante": "texto que el portero le dice al visitante (vacío si continuar=false)",
  "motivo_visita": "resumen breve o null si aún no se sabe",
  "nivel_riesgo": "BAJO" | "MEDIO" | "ALTO",
  "recomendacion": "AUTORIZAR" | "RECHAZAR" | "DERIVAR" | null,
  "justificacion": "por qué, en 1-2 oraciones, para mostrarle al residente"
}

"recomendacion" y "justificacion" solo se completan cuando continuar=false.
Mientras continuar=true, dejalos en null.
""" % MAX_TURNOS_CONVERSACION


class AgenteConversacionalPortero:
    """
    Orquesta la conversación con el visitante usando Claude como motor
    de razonamiento (etapas de Análisis y Planificación del agente).
    """

    def __init__(self, api_key: str = ANTHROPIC_API_KEY):
        if not api_key:
            raise ValueError(
                "Falta la API key de Anthropic. Agregá \"anthropic_api_key\": "
                "\"sk-ant-...\" a tu settings.json (o definí la variable de "
                "entorno ANTHROPIC_API_KEY como alternativa)."
            )
        self.client = Anthropic(api_key=api_key)
        self.historial: list[TurnoConversacion] = []

    def _armar_mensajes_para_llm(self) -> list[dict]:
        """Convierte el historial de la conversación al formato de la API."""
        # Le mandamos todo el historial como un único mensaje de usuario
        # describiendo el diálogo hasta ahora, para que el LLM tenga contexto
        # completo y responda en JSON con el siguiente paso.
        transcripcion = "\n".join(
            f"{t.hablante.upper()}: {t.texto}" for t in self.historial
        )
        contenido = (
            "Transcripción de la conversación hasta ahora:\n\n"
            f"{transcripcion}\n\n"
            "Generá el siguiente paso según las reglas del sistema."
        )
        return [{"role": "user", "content": contenido}]

    def procesar_turno(self, texto_visitante: Optional[str]) -> ResultadoEvaluacion | str:
        """
        Recibe lo que dijo el visitante (o None si no se entendió nada),
        consulta al LLM, y devuelve:
          - un str con la respuesta a decirle al visitante (si continúa), o
          - un ResultadoEvaluacion (si la conversación terminó)
        """
        if texto_visitante:
            self.historial.append(TurnoConversacion("visitante", texto_visitante))
        else:
            self.historial.append(
                TurnoConversacion("visitante", "[silencio / no se entendió el audio]")
            )

        respuesta = self.client.messages.create(
            model=MODELO,
            max_tokens=500,
            system=SYSTEM_PROMPT,
            messages=self._armar_mensajes_para_llm(),
        )

        texto_bruto = "".join(
            bloque.text for bloque in respuesta.content if bloque.type == "text"
        )

        try:
            datos = json.loads(texto_bruto)
        except json.JSONDecodeError:
            # Degradación elegante: si el LLM no devolvió JSON válido,
            # derivamos directo al residente en vez de fallar.
            return ResultadoEvaluacion(
                recomendacion="DERIVAR",
                nivel_riesgo="MEDIO",
                justificacion="No se pudo interpretar la respuesta del asistente; "
                               "se deriva al residente por precaución.",
                transcripcion_completa=self._transcripcion_texto(),
            )

        if datos.get("continuar"):
            respuesta_portero = datos.get("respuesta_al_visitante", "")
            self.historial.append(TurnoConversacion("portero", respuesta_portero))
            return respuesta_portero

        self.historial.append(
            TurnoConversacion("portero", datos.get("respuesta_al_visitante", "") or "")
        )
        return ResultadoEvaluacion(
            recomendacion=datos.get("recomendacion") or "DERIVAR",
            nivel_riesgo=datos.get("nivel_riesgo") or "MEDIO",
            justificacion=datos.get("justificacion") or "",
            motivo_visita=datos.get("motivo_visita"),
            transcripcion_completa=self._transcripcion_texto(),
        )

    def _transcripcion_texto(self) -> str:
        return "\n".join(f"{t.hablante.upper()}: {t.texto}" for t in self.historial)


# =====================================================
# FLUJO COMPLETO DE LA VÍA C (función de integración)
# =====================================================
def ejecutar_via_c_con_ia(api_key: str = ANTHROPIC_API_KEY) -> ResultadoEvaluacion:
    """
    Punto de entrada para reemplazar la simulación actual de la vía C.

    Uso sugerido en main.py:

        from ia_portero import ejecutar_via_c_con_ia

        resultado = ejecutar_via_c_con_ia()
        # guardar resultado en la base (ver database.py) y notificar
        # al residente con notificaciones.py, incluyendo:
        #   resultado.recomendacion, resultado.nivel_riesgo,
        #   resultado.justificacion, resultado.transcripcion_completa
    """
    voz = InterfazVoz()
    agente = AgenteConversacionalPortero(api_key=api_key)

    saludo = "Hola, bienvenido. ¿A quién viene a visitar?"
    voz.hablar(saludo)
    agente.historial.append(TurnoConversacion("portero", saludo))

    for _ in range(MAX_TURNOS_CONVERSACION):
        texto_visitante = voz.escuchar()
        resultado = agente.procesar_turno(texto_visitante)

        if isinstance(resultado, ResultadoEvaluacion):
            # Conversación terminada: le avisamos al visitante que esperamos
            # confirmación y devolvemos el resultado para el residente.
            voz.hablar("Gracias, dame un momento para confirmar con el residente.")
            return resultado

        # Si no terminó, seguimos conversando
        voz.hablar(resultado)

    # Si se llega al tope de turnos sin resolución clara, derivar por defecto
    voz.hablar("Voy a derivar tu visita al residente para que la confirme.")
    return ResultadoEvaluacion(
        recomendacion="DERIVAR",
        nivel_riesgo="MEDIO",
        justificacion="Se alcanzó el máximo de turnos de conversación sin resolución clara.",
        transcripcion_completa=agente._transcripcion_texto(),
    )


# =====================================================
# PRUEBA MANUAL (ejecutar este archivo directamente)
# =====================================================
if __name__ == "__main__":
    print("=== Prueba del Portero Virtual con IA (vía C) ===")
    print("Hablá cuando escuches el saludo. Ctrl+C para cancelar.\n")
    resultado = ejecutar_via_c_con_ia()
    print("\n--- RESULTADO PARA EL RESIDENTE ---")
    print(f"Recomendación : {resultado.recomendacion}")
    print(f"Nivel de riesgo: {resultado.nivel_riesgo}")
    print(f"Motivo visita  : {resultado.motivo_visita}")
    print(f"Justificación  : {resultado.justificacion}")
    print("\nTranscripción completa:")
    print(resultado.transcripcion_completa)