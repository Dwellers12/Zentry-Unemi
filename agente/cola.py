"""
Cola local de capturas (RNF-05: si se cae la conexión no se pierden lecturas).

Cada captura pendiente se guarda en disco como <instante>_<id>.json (+ .jpg).
Se reenvían en orden cronológico porque el servidor descarta las capturas más
antiguas que la última recibida de esa cámara. Para no llenar el disco se
guardan como máximo MAX_PENDIENTES capturas y solo las MAX_CON_IMAGEN más
recientes conservan su imagen (las lecturas viejas siguen sirviendo para el
historial aunque lleguen sin evidencia).
"""
import json
import logging
import os
from pathlib import Path

log = logging.getLogger('zentry.agente')

MAX_PENDIENTES = 2000
MAX_CON_IMAGEN = 30


class Cola:
    def __init__(self, carpeta):
        self.carpeta = Path(carpeta)

    def _pendientes(self):
        if not self.carpeta.is_dir():
            return []
        return sorted(self.carpeta.glob('*.json'))

    def __len__(self):
        return len(self._pendientes())

    def guardar(self, captura):
        self.carpeta.mkdir(parents=True, exist_ok=True)
        base = self.carpeta / f'{captura["instante_ms"]:015d}_{captura["captura"]}'
        datos = {k: v for k, v in captura.items() if k != 'jpeg'}
        if captura.get('jpeg'):
            base.with_suffix('.jpg').write_bytes(captura['jpeg'])
        temporal = base.with_suffix('.tmp')
        temporal.write_text(json.dumps(datos), encoding='utf-8')
        os.replace(temporal, base.with_suffix('.json'))
        self._recortar()

    def _recortar(self):
        pendientes = self._pendientes()
        for viejo in pendientes[:-MAX_PENDIENTES]:
            self._borrar(viejo)
        for sin_imagen in pendientes[-MAX_PENDIENTES:-MAX_CON_IMAGEN]:
            sin_imagen.with_suffix('.jpg').unlink(missing_ok=True)

    def _borrar(self, ruta):
        ruta.unlink(missing_ok=True)
        ruta.with_suffix('.jpg').unlink(missing_ok=True)

    def vaciar(self, enviar):
        """
        Reenvía las capturas pendientes con `enviar(captura)`. Se detiene en el primer
        error de conexión (la excepción se propaga) y borra cada captura ya entregada.
        Devuelve cuántas se entregaron.
        """
        entregadas = 0
        for ruta in self._pendientes():
            try:
                captura = json.loads(ruta.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                log.warning('Captura dañada en la cola, se descarta: %s', ruta.name)
                self._borrar(ruta)
                continue
            imagen = ruta.with_suffix('.jpg')
            captura['jpeg'] = imagen.read_bytes() if imagen.exists() else None
            enviar(captura)
            self._borrar(ruta)
            entregadas += 1
        return entregadas
