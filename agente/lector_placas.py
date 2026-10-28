"""
Lector de placas propio: una red CRNN pequeña (convoluciones + LSTM) entrenada con CTC.

EasyOCR primero busca dónde hay texto (CRAFT) y luego lo lee; con placas de pocos
píxeles el primer paso falla. Este lector recibe directamente el recorte de la placa
que encontró YOLO (best.pt) y devuelve el texto completo, por ejemplo 'GBP-3579'.

  - Entrada: recorte en gris de ALTO x ANCHO píxeles (se estira, no se recorta).
  - Salida: una columna de probabilidades por cada franja vertical; CTC las convierte
    en texto (quita repeticiones y el símbolo "vacío").
  - Confianza: la probabilidad del carácter más dudoso (si uno está en duda, toda la
    lectura lo está).

Se entrena con entrenamiento/ocr_placas/entrenar.py y se guarda en modelos/placas_ocr.pt.
"""
import numpy as np

ALFABETO = '-0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'   # índice 0 de la red = vacío (CTC)
ALTO, ANCHO = 32, 96


def preparar(recorte):
    """Recorte BGR o gris (uint8) → arreglo float32 (1, ALTO, ANCHO) normalizado."""
    import cv2
    gris = recorte if recorte.ndim == 2 else cv2.cvtColor(recorte, cv2.COLOR_BGR2GRAY)
    interp = cv2.INTER_AREA if gris.shape[1] > ANCHO else cv2.INTER_CUBIC
    x = cv2.resize(gris, (ANCHO, ALTO), interpolation=interp).astype(np.float32)
    # Normalización por recorte: la placa queda igual con poca luz o a contraluz.
    x = (x - x.mean()) / (x.std() + 8.0)
    return x[None]


def construir_red(clases=len(ALFABETO) + 1):
    import torch.nn as nn

    def bloque(entrada, salida):
        return [nn.Conv2d(entrada, salida, 3, padding=1, bias=False), nn.BatchNorm2d(salida), nn.ReLU(inplace=True)]

    class CRNN(nn.Module):
        def __init__(self):
            super().__init__()
            self.cnn = nn.Sequential(
                *bloque(1, 32), nn.MaxPool2d(2),                                  # 16 x 48
                *bloque(32, 64), nn.MaxPool2d(2),                                 # 8 x 24
                *bloque(64, 96), *bloque(96, 96), nn.MaxPool2d((2, 1)),           # 4 x 24
                *bloque(96, 128), *bloque(128, 128), nn.MaxPool2d((2, 1)),        # 2 x 24
                nn.Conv2d(128, 128, (2, 1), bias=False), nn.BatchNorm2d(128), nn.ReLU(inplace=True),  # 1 x 24
                nn.Dropout(0.1),
            )
            self.rnn = nn.LSTM(128, 96, bidirectional=True, batch_first=True)
            self.salida = nn.Linear(192, clases)

        def forward(self, x):                      # x: (N, 1, ALTO, ANCHO)
            f = self.cnn(x).squeeze(2).permute(0, 2, 1)   # (N, 24, 128)
            f, _ = self.rnn(f)
            return self.salida(f)                  # (N, 24, clases) sin softmax

    return CRNN()


def decodificar(probabilidades):
    """
    probabilidades: (T, clases) ya con softmax. Decodificación voraz de CTC.
    Devuelve (texto, confianza 0-1 = probabilidad del carácter más dudoso).
    """
    mejores = probabilidades.argmax(1)
    texto, confs, anterior = [], [], 0
    for t, k in enumerate(mejores):
        if k != 0 and k != anterior:
            texto.append(ALFABETO[k - 1])
            confs.append(float(probabilidades[t, k]))
        elif k != 0 and confs:
            confs[-1] = max(confs[-1], float(probabilidades[t, k]))
        anterior = k
    return ''.join(texto), (min(confs) if confs else 0.0)


class LectorPlacas:
    """Carga placas_ocr.pt una vez y lee recortes de placas (en CPU tarda ~2 ms por recorte)."""

    def __init__(self, ruta, dispositivo='cpu'):
        import torch
        datos = torch.load(str(ruta), map_location='cpu', weights_only=True)
        if datos.get('alfabeto') != ALFABETO or tuple(datos.get('entrada', ())) != (ALTO, ANCHO):
            raise ValueError(f'{ruta} fue entrenado con otro alfabeto o tamaño de entrada')
        self.red = construir_red()
        self.red.load_state_dict(datos['pesos'])
        self.red.eval().to(dispositivo)
        self.dispositivo = dispositivo
        self.version = datos.get('version', '')

    def leer(self, recortes):
        """Lista de recortes (BGR o gris) → [(texto, confianza), ...] en el mismo orden."""
        import torch
        if not recortes:
            return []
        lote = torch.from_numpy(np.stack([preparar(r) for r in recortes])).to(self.dispositivo)
        # La red es tan pequeña que repartirla en varios hilos tarda más que usar uno
        # (sobre todo con la CPU ocupada): se usa un hilo y se deja como estaba para YOLO.
        hilos = torch.get_num_threads()
        if hilos > 1:
            torch.set_num_threads(1)
        try:
            with torch.inference_mode():
                probs = self.red(lote).softmax(2).cpu().numpy()
        finally:
            if hilos > 1:
                torch.set_num_threads(hilos)
        return [decodificar(p) for p in probs]
