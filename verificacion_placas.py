import cv2
from ultralytics import YOLO
import easyocr

# 1. Cargar TU modelo YOLO recién entrenado
print("Cargando tu modelo YOLO personalizado...")
modelo = YOLO('best.pt') # Asegúrate de que el archivo se llame así y esté en esta carpeta

# 2. Inicializar EasyOCR (leerá caracteres en español e inglés)
print("Iniciando lector óptico EasyOCR...")
lector = easyocr.Reader(['es', 'en'], gpu=True)

# 3. Conectar la cámara (Ajusta la URL de IP Webcam si tu IP cambió)
url = "http://192.168.1.8:8080/video"
cap = cv2.VideoCapture(url)

print("Sistema Zentry iniciado. Apunta a una placa y presiona 'q' para salir.")

while True:
    ret, frame = cap.read()
    if not ret:
        print("Error al leer la transmisión de la cámara.")
        break

    # 4. YOLO busca exclusivamente la caja de la placa en el fotograma
    resultados = modelo(frame)

    for r in resultados:
        cajas = r.boxes
        for caja in cajas:
            # Obtener coordenadas exactas del recorte de la placa
            x1, y1, x2, y2 = map(int, caja.xyxy[0])

            # Dibujar un rectángulo verde en la interfaz
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)

            # 5. RECORTAR la imagen aislando solo la placa para dársela al OCR
            placa_recorte = frame[y1:y2, x1:x2]

            # 6. Extraer el texto del recorte con EasyOCR
            texto_detectado = lector.readtext(placa_recorte, detail=0, paragraph=True)

            if texto_detectado:
                # Limpiar el texto (forzar mayúsculas y quitar espacios en blanco)
                placa_texto = texto_detectado[0].upper().replace(" ", "")

                # Mostrar el texto leído en la pantalla sobre el recuadro verde
                cv2.putText(frame, placa_texto, (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)
                print(f"Placa leída por Zentry: {placa_texto}")

    # Mostrar la ventana con el video en vivo
    cv2.imshow("Zentry - Reconocimiento de Placas", frame)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()