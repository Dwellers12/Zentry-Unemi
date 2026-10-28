"""
Detección automática de plazas de estacionamiento.

Busca las líneas amarillas pintadas en el suelo que separan las plazas y
construye un polígono por plaza. Está pensado para cámaras que ven una fila de
plazas de frente (como CAM 01 y CAM 02):

  1. Máscara del color amarillo (HSV), quitando los vehículos detectados y las
     zonas amarillas "gruesas" (columnas con franjas, carros amarillos...).
  2. Segmentos de recta con Hough; se descartan los casi horizontales.
  3. Se estima el borde delantero (y_frente) y el fondo (y_fondo) de la fila.
  4. Los segmentos se agrupan en líneas divisorias según dónde cortan y_frente.
  5. Las divisorias tapadas por vehículos se interpolan (espaciado regular).
  6. Cada par de divisorias consecutivas forma una plaza.
  7. Si se pasa un lector OCR, se leen los carteles "P1", "P2"... de la pared
     para ponerle a cada plaza su nombre real.
"""
import re
from collections import Counter, defaultdict

import cv2
import numpy as np

# Rango HSV del amarillo de las líneas de parqueo.
AMARILLO_BAJO = np.array((15, 70, 70))
AMARILLO_ALTO = np.array((40, 255, 255))

# Ángulo mínimo (respecto a la horizontal) de una línea divisoria.
ANGULO_MIN = 15

PATRON_ETIQUETA = re.compile(r'P(\d{1,3})')


def _mascara_lineas(frame, vehiculos):
    """Máscara binaria con las líneas amarillas del suelo."""
    alto = frame.shape[0]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mascara = cv2.inRange(hsv, AMARILLO_BAJO, AMARILLO_ALTO)

    # Los vehículos tapan las líneas y pueden ser amarillos: se ignoran.
    for v in vehiculos:
        x1, y1, x2, y2 = v['bbox']
        mascara[y1:y2, x1:x2] = 0

    # Una línea pintada es delgada: lo que sobrevive a una apertura con un
    # kernel mayor que su grosor (columnas con franjas, etc.) no es una línea.
    k_cierre = np.ones((max(3, int(alto * 0.01)),) * 2, np.uint8)
    k_grueso = np.ones((max(5, int(alto * 0.025)),) * 2, np.uint8)
    cerrada = cv2.morphologyEx(mascara, cv2.MORPH_CLOSE, k_cierre)
    gruesa = cv2.morphologyEx(cerrada, cv2.MORPH_OPEN, k_grueso)
    mascara[cv2.dilate(gruesa, k_cierre) > 0] = 0
    return mascara


def _segmentos(mascara):
    """Segmentos no horizontales, orientados de arriba hacia abajo."""
    alto = mascara.shape[0]
    lineas = cv2.HoughLinesP(mascara, 1, np.pi / 180, threshold=40,
                             minLineLength=int(alto * 0.015),
                             maxLineGap=int(alto * 0.01))
    segmentos = []
    if lineas is None:
        return segmentos

    for x1, y1, x2, y2 in lineas.reshape(-1, 4).astype(float):
        if y1 > y2:
            x1, y1, x2, y2 = x2, y2, x1, y1
        angulo = np.degrees(np.arctan2(y2 - y1, abs(x2 - x1)))
        if angulo < ANGULO_MIN:
            continue
        segmentos.append({
            'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2,
            'pendiente': (x2 - x1) / (y2 - y1),  # dx/dy
            'largo': float(np.hypot(x2 - x1, y2 - y1)),
        })
    return segmentos


def _x_en(seg, y):
    """X del segmento (prolongado) a la altura y."""
    return seg['x1'] + seg['pendiente'] * (y - seg['y1'])


def _moda_ponderada(valores, pesos, ancho_bin):
    """Valor más frecuente (ponderado) usando bins de ancho `ancho_bin`."""
    valores = np.asarray(valores, dtype=float)
    pesos = np.asarray(pesos, dtype=float)
    bins = np.round(valores / ancho_bin).astype(int)
    acumulado = defaultdict(float)
    for b, p in zip(bins, pesos):
        acumulado[b] += p
    mejor = max(acumulado, key=acumulado.get)
    sel = np.abs(bins - mejor) <= 1
    return float(np.average(valores[sel], weights=pesos[sel]))


def _agrupar_por_x(segmentos, y_frente, tolerancia):
    """Agrupa los segmentos que pertenecen a la misma línea divisoria."""
    for s in segmentos:
        s['x_frente'] = _x_en(s, y_frente)
    grupos = []
    for s in sorted(segmentos, key=lambda s: s['x_frente']):
        if grupos and s['x_frente'] - grupos[-1][-1]['x_frente'] <= tolerancia:
            grupos[-1].append(s)
        else:
            grupos.append([s])
    return grupos


def _estimar_fondo(grupos, y_frente, alto):
    """
    Altura del fondo de las plazas (unión suelo-pared).

    Es la altura más alta en la que empiezan varias divisorias a la vez. Así
    no confunde el fondo con el extremo de una divisoria tapada por un carro.
    """
    ancho_bin = alto * 0.01
    tope = y_frente - alto * 0.03
    clusters_por_bin = defaultdict(set)
    ys_por_bin = defaultdict(list)
    for gid, grupo in enumerate(grupos):
        for s in grupo:
            if s['y1'] < tope:
                b = int(round(s['y1'] / ancho_bin))
                clusters_por_bin[b].add(gid)
                ys_por_bin[b].append(s['y1'])

    if not ys_por_bin:
        return None

    minimo = max(2, int(np.ceil(len(grupos) * 0.34)))
    candidatos = [b for b in sorted(clusters_por_bin)
                  if len(clusters_por_bin[b] | clusters_por_bin.get(b + 1, set())) >= minimo]
    if not candidatos:
        return float(min(y for ys in ys_por_bin.values() for y in ys))
    b = candidatos[0]
    return float(np.median(ys_por_bin[b] + ys_por_bin.get(b + 1, [])))


def _completar_divisorias(xs):
    """Inserta las divisorias que faltan (tapadas) suponiendo espaciado regular."""
    xs = sorted(xs)
    if len(xs) < 2:
        return xs
    huecos = np.diff(xs)
    paso = float(np.median(huecos))
    for _ in range(3):
        k = np.maximum(1, np.round(huecos / paso))
        paso = float(huecos.sum() / k.sum())

    completas = [xs[0]]
    for x_ant, x_sig in zip(xs, xs[1:]):
        n = int(max(1, round((x_sig - x_ant) / paso)))
        for i in range(1, n):
            completas.append(x_ant + (x_sig - x_ant) * i / n)
        completas.append(x_sig)
    return completas


def _divisorias(frame, vehiculos):
    """
    Devuelve (y_fondo, y_frente, [(x_fondo, x_frente), ...]) con las líneas
    divisorias ordenadas de izquierda a derecha, o None si no se encuentran.
    """
    alto, ancho = frame.shape[:2]
    segmentos = _segmentos(_mascara_lineas(frame, vehiculos))
    if len(segmentos) < 2:
        return None

    # Borde delantero: donde terminan (abajo) la mayoría de las divisorias.
    y_frente = _moda_ponderada([s['y2'] for s in segmentos],
                               [s['largo'] for s in segmentos], alto * 0.01)
    grupos = _agrupar_por_x(segmentos, y_frente, ancho * 0.015)
    y_fondo = _estimar_fondo(grupos, y_frente, alto)
    if y_fondo is None or y_frente - y_fondo < alto * 0.03:
        return None
    profundidad = y_frente - y_fondo

    # Solo cuentan las divisorias que llegan hasta el borde delantero (el
    # extremo de abajo casi nunca lo tapa un vehículo).
    x_frentes, fiables = [], []
    for grupo in grupos:
        if max(s['y2'] for s in grupo) < y_frente - profundidad * 0.2:
            continue
        pesos = [s['largo'] for s in grupo]
        x_frente = float(np.average([s['x_frente'] for s in grupo], weights=pesos))
        x_frentes.append(x_frente)

        # Si la divisoria se ve en al menos la mitad de la profundidad, su
        # inclinación es fiable para saber dónde corta el fondo.
        ys, xs, ws = [], [], []
        for s in grupo:
            y_a, y_b = max(s['y1'], y_fondo), min(s['y2'], y_frente)
            if y_b <= y_a:
                continue
            ys += [y_a, y_b]
            xs += [_x_en(s, y_a), _x_en(s, y_b)]
            ws += [y_b - y_a] * 2
        cobertura = max(ys) - min(ys) if ys else 0
        if cobertura >= profundidad * 0.5:
            a, b = np.polyfit(ys, xs, 1, w=np.sqrt(ws))
            fiables.append((x_frente, a * y_fondo + b))

    if len(x_frentes) < 2:
        return None

    # Todas las divisorias apuntan al mismo punto de fuga, así que la x en el
    # fondo es función lineal de la x en el frente: x_fondo = alfa*x_frente + beta.
    if len(fiables) >= 2:
        alfa, beta = np.polyfit([f[0] for f in fiables], [f[1] for f in fiables], 1)
    elif len(fiables) == 1:
        alfa, beta = 1.0, fiables[0][1] - fiables[0][0]
    else:
        alfa, beta = 1.0, 0.0

    x_frentes = _completar_divisorias(x_frentes)
    return y_fondo, y_frente, [(alfa * x + beta, x) for x in x_frentes]


def _numerar(frame, lector, y_fondo, divisorias, primer_numero):
    """
    Nombres de las plazas (de izquierda a derecha). Si hay lector OCR, lee los
    carteles "P<n>" de la pared, encima del fondo de cada plaza; si no, numera
    desde `primer_numero`.
    """
    n_plazas = len(divisorias) - 1
    base = primer_numero

    if lector is not None:
        alto, ancho = frame.shape[:2]
        y0 = max(0, int(y_fondo - alto * 0.25))
        franja = frame[y0:int(y_fondo)]
        escala = min(1.0, 1920 / ancho)
        if escala < 1.0:
            franja = cv2.resize(franja, None, fx=escala, fy=escala,
                                interpolation=cv2.INTER_AREA)

        bases = []
        for caja, texto, conf in lector.readtext(franja, allowlist='P0123456789', detail=1):
            m = PATRON_ETIQUETA.fullmatch(texto)
            if not m or conf < 0.4:
                continue
            xc = (caja[0][0] + caja[2][0]) / 2 / escala
            for i in range(n_plazas):
                if divisorias[i][0] <= xc < divisorias[i + 1][0]:
                    bases.append(int(m.group(1)) - i)
                    break
        if bases:
            base = Counter(bases).most_common(1)[0][0]

    return [f'P{base + i}' for i in range(n_plazas)]


def detectar_plazas(frame, vehiculos, lector=None, primer_numero=1):
    """
    Detecta automáticamente las plazas de la imagen.

    Devuelve una lista [{'id': 'P1', 'poligono': [[x, y], ...]}, ...] (vacía
    si no se encuentran líneas de parqueo).
    """
    resultado = _divisorias(frame, vehiculos)
    if resultado is None:
        return []
    y_fondo, y_frente, divisorias = resultado

    alto, ancho = frame.shape[:2]
    nombres = _numerar(frame, lector, y_fondo, divisorias, primer_numero)

    def punto(x, y):
        return [int(np.clip(round(x), 0, ancho - 1)), int(np.clip(round(y), 0, alto - 1))]

    plazas = []
    for nombre, (izq, der) in zip(nombres, zip(divisorias, divisorias[1:])):
        plazas.append({
            'id': nombre,
            'poligono': [
                punto(izq[0], y_fondo), punto(der[0], y_fondo),
                punto(der[1], y_frente), punto(izq[1], y_frente),
            ],
        })
    return plazas
