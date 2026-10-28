"""
Lectura del estado del simulador (parqueadero-cctv.html → window.parqueadero.estado()).

El simulador exporta su "verdad de terreno": plazas proyectadas en cada cámara,
vehículos (huella, alto, plaza real) y placas (polígono en la imagen, oclusión).
Este módulo convierte ese JSON en:
  - camaras(estado): las cámaras virtuales con sus plazas, para registrarlas en Zentry.
  - detecciones(estado, codigo): lo que vería un reconocedor perfecto en esa cámara
    (modo --reconocimiento simulador, útil para probar el sistema sin cargar la IA).

Solo usa la biblioteca estándar: lo importa también el puente camaras_virtuales.py.
"""

# Una placa se considera legible si se ve completa, de frente, casi sin taparse y
# con un ancho mínimo en píxeles (a 1920 px de ancho de imagen).
OCLUSION_MAX = 0.25
ANCHO_PLACA_MIN_PX = 28


def _ref(estado):
    ref = estado.get('resolucionReferencia') or {}
    return float(ref.get('ancho') or 1920), float(ref.get('alto') or 1080)


def _limitar(valor):
    return round(min(1.0, max(0.0, valor)), 5)


def _relativo(puntos, ancho, alto):
    return [[_limitar(x / ancho), _limitar(y / alto)] for x, y in puntos]


def _caja_relativa(puntos, ancho, alto):
    xs = [p[0] for p in puntos]
    ys = [p[1] for p in puntos]
    return [_limitar(min(xs) / ancho), _limitar(min(ys) / alto), _limitar(max(xs) / ancho), _limitar(max(ys) / alto)]


def _info_camaras(estado):
    return {c['id']: c for c in estado.get('camaras') or []}


def camaras(estado):
    """[{codigo, nombre, ancho, alto, plazas_sugeridas: [{nombre, poligono relativo}]}]."""
    ancho, alto = _ref(estado)
    plazas = {p['id']: p for p in estado.get('plazas') or []}
    salida = []
    for codigo, camara in _info_camaras(estado).items():
        sugeridas = []
        for nombre in camara.get('plazasObjetivo') or []:
            proyeccion = (plazas.get(nombre) or {}).get('proyecciones', {}).get(codigo) or {}
            if proyeccion.get('poligono_px'):
                sugeridas.append({'nombre': nombre, 'poligono': _relativo(proyeccion['poligono_px'], ancho, alto)})
        salida.append({
            'codigo': codigo,
            'nombre': camara.get('nombre') or codigo,
            'ancho': int(ancho), 'alto': int(alto),
            'plazas_sugeridas': sugeridas,
        })
    return salida


# ---------------------------------------------------------------------- proyección

def _multiplicar(matriz, vector):
    """Matriz 4x4 de three.js (orden column-major) por un vector de 4 componentes."""
    return [sum(matriz[col * 4 + fila] * vector[col] for col in range(4)) for fila in range(4)]


def proyectar(camara, punto, ancho, alto):
    """Punto del mundo (x, y, z) → píxel (x, y), o None si queda detrás de la cámara."""
    en_camara = _multiplicar(camara['matriz_mundo_a_camara'], [*punto, 1.0])
    recorte = _multiplicar(camara['matriz_proyeccion'], en_camara)
    if recorte[3] <= 1e-6:
        return None
    x, y = recorte[0] / recorte[3], recorte[1] / recorte[3]
    return (x + 1) / 2 * ancho, (1 - y) / 2 * alto


def caja_vehiculo(camara, vehiculo, ancho, alto):
    """Caja relativa [x1, y1, x2, y2] del vehículo visto por la cámara, o None si no se ve."""
    altura = (vehiculo.get('dimensiones_m') or {}).get('alto') or 1.5
    puntos = []
    for x, z in vehiculo.get('huella') or []:
        for y in (0.0, altura):
            p = proyectar(camara, (x, y, z), ancho, alto)
            if p is None:
                return None
            puntos.append(p)
    if not puntos:
        return None
    xs, ys = [p[0] for p in puntos], [p[1] for p in puntos]
    area = (max(xs) - min(xs)) * (max(ys) - min(ys))
    visible = (min(max(xs), ancho) - max(min(xs), 0)) * (min(max(ys), alto) - max(min(ys), 0))
    if area <= 0 or max(xs) < 0 or min(xs) > ancho or max(ys) < 0 or min(ys) > alto or visible / area < 0.15:
        return None
    return _caja_relativa(puntos, ancho, alto)


def _mejor_placa(vehiculo, codigo, ancho):
    """(texto, confianza 0-100, caja relativa) de la placa más legible, o ('', 0, caja|None)."""
    mejor = None
    for placa in vehiculo.get('placas') or []:
        vista = (placa.get('camaras') or {}).get(codigo) or {}
        if not vista.get('poligono_px') or vista.get('en_imagen') == 'fuera':
            continue
        ancho_px = (vista.get('ancho_px') or 0) * 1920 / ancho
        legible = (vista.get('en_imagen') == 'completa' and vista.get('de_frente_a_camara')
                   and vista.get('fraccion_sin_oclusion', 0) >= 1 - OCLUSION_MAX
                   and ancho_px >= ANCHO_PLACA_MIN_PX)
        puntaje = (2 if legible else 0) + vista.get('fraccion_sin_oclusion', 0)
        if mejor is None or puntaje > mejor[0]:
            mejor = (puntaje, legible, placa, vista, ancho_px)
    if mejor is None:
        return '', 0, None
    _, legible, placa, vista, ancho_px = mejor
    if not legible:
        return '', 0, None
    # Más confianza cuanto más grande y menos tapada se vea la placa (entre 75 y 98).
    confianza = 75 + 23 * min(1.0, ancho_px / 80) * vista.get('fraccion_sin_oclusion', 1)
    return placa.get('texto') or '', int(round(confianza)), vista['poligono_px']


def detecciones(estado, codigo):
    """Detecciones ideales de una cámara, con el mismo formato que envía el reconocimiento real."""
    ancho, alto = _ref(estado)
    camara = _info_camaras(estado).get(codigo)
    if camara is None:
        return []
    vehiculos = estado.get('vehiculos') or []
    salida = []
    for nombre in camara.get('plazasObjetivo') or []:
        # El vehículo cuya plaza real es esta; si no, alguno que la ocupe a medias.
        vehiculo = next((v for v in vehiculos if v.get('plazaReal') == nombre), None)
        if vehiculo is None:
            vehiculo = next((v for v in vehiculos if nombre in (v.get('plazasOcupadas') or [])), None)
        if vehiculo is None:
            salida.append({'plaza': nombre, 'ocupada': False})
            continue
        texto, confianza, poligono_placa = _mejor_placa(vehiculo, codigo, ancho)
        deteccion = {
            'plaza': nombre, 'ocupada': True, 'placa': texto, 'confianza': confianza,
            'vehiculo_caja': caja_vehiculo(camara, vehiculo, ancho, alto),
            'placa_caja': _caja_relativa(poligono_placa, ancho, alto) if poligono_placa else None,
        }
        salida.append(deteccion)
    return salida
