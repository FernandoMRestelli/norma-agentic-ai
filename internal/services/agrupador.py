"""Junta los mensajes seguidos de un cliente en un solo turno (RF-74, docs/spec/toma-de-pedidos-por-texto.md).

Un pedido largo suele llegar en varios mensajes, uno atrás del otro. Sin esto, cada mensaje abre un turno: el
agente contesta el primero antes de leer el segundo. El agrupador espera una ventana corta después de cada mensaje;
si en ese tiempo llega otro del mismo cliente, vuelve a esperar, hasta un máximo. Después entrega todos juntos.

Es de los canales: cada uno decide si lo usa y cómo responde. Con la ventana en 0 no agrupa nada.
"""

import logging
import threading
import time
from collections.abc import Callable, Hashable
from dataclasses import dataclass, field
from typing import Any

from internal.schemas.order_schema import IncomingMessage

logger = logging.getLogger(__name__)

MAX_ESPERA_SEGUNDOS = 20.0  # desde el primer mensaje: el cliente no espera más que esto, aunque siga escribiendo

Entregar = Callable[[IncomingMessage], None]


@dataclass
class _Grupo:
    entregar: Entregar
    desde: float
    mensajes: list[IncomingMessage] = field(default_factory=list)
    temporizador: Any = None


def juntar(mensajes: list[IncomingMessage]) -> IncomingMessage:
    """Un solo mensaje con el texto de todos, un renglón debajo del otro. Los demás datos son los del último."""
    texto = "\n".join(mensaje.text for mensaje in mensajes if mensaje.text)
    return mensajes[-1].model_copy(update={"text": texto})


class Agrupador:
    """Espera `ventana` segundos desde el último mensaje de cada cliente antes de entregarlos juntos."""

    def __init__(
        self,
        ventana: float,
        maximo: float = MAX_ESPERA_SEGUNDOS,
        temporizador: Callable[..., Any] = threading.Timer,
        reloj: Callable[[], float] = time.monotonic,
    ):
        self.ventana = ventana
        self.maximo = maximo
        self._temporizador = temporizador
        self._reloj = reloj
        self._grupos: dict[Hashable, _Grupo] = {}
        self._lock = threading.Lock()

    def recibir(self, clave: Hashable, mensaje: IncomingMessage, entregar: Entregar) -> None:
        """Suma el mensaje al grupo del cliente y reinicia la espera. `entregar` recibe el mensaje ya juntado."""
        with self._lock:
            grupo = self._grupos.get(clave)
            if grupo is None:
                grupo = self._grupos[clave] = _Grupo(entregar=entregar, desde=self._reloj())
            elif grupo.temporizador is not None:
                grupo.temporizador.cancel()
            grupo.mensajes.append(mensaje)
            grupo.entregar = entregar
            espera = max(0.0, min(self.ventana, self.maximo - (self._reloj() - grupo.desde)))
            grupo.temporizador = self._temporizador(espera, self._entregar, args=(clave, grupo))
            grupo.temporizador.daemon = True
            grupo.temporizador.start()

    def _entregar(self, clave: Hashable, grupo: _Grupo) -> None:
        with self._lock:
            if self._grupos.get(clave) is not grupo:  # llegó otro justo cuando vencía: lo entrega su temporizador
                return
            del self._grupos[clave]
        try:
            grupo.entregar(juntar(grupo.mensajes))
        except Exception:
            # Corre en el hilo del temporizador: sin esto el error se pierde. Sin el texto de los mensajes (RNF-03)
            logger.exception("Agrupador: falló la entrega de %d mensajes juntos", len(grupo.mensajes))

    def pendientes(self) -> int:
        with self._lock:
            return len(self._grupos)
