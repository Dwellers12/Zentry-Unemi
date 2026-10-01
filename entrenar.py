from ultralytics import YOLO

# 1. Cargar el modelo base ligero
modelo = YOLO('yolov8n.pt')

# 2. Iniciar el entrenamiento (device=0 fuerza el uso de tu GPU)
print("Iniciando entrenamiento en GPU...")
resultados = modelo.train(data='/home/frankex/Downloads/YOLOv8 number plate detection.v1i.yolov8/data.yaml', epochs=50, imgsz=640)

print("¡Listo! Tu nuevo modelo preentrenado está en: runs/detect/train/weights/best.pt")