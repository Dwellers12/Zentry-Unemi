"""
Señales de verificacion. Así el módulo de alertas reacciona a los cambios sin
que verificacion dependa de él (las dependencias van alertas -> verificacion).
"""
from django.dispatch import Signal

# Se emite cuando cambia lo que hay en una plaza o el resultado de verificarlo.
# Argumentos: plaza, estado (EstadoPlaza), lectura (Lectura o None),
# imagen (bytes JPEG de la captura o None), motivo ('lectura' | 'registro' | 'correccion'),
# placa_anterior (str), usuario (quien corrigió, o None).
estado_plaza_cambiado = Signal()
