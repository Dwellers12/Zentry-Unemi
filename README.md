# Zentry

Sistema web que verifica que cada plaza de un parqueadero residencial esté ocupada por el vehículo que le corresponde. Lee las placas con YOLOv8 y un lector de placas propio, y genera alertas con foto de evidencia cuando algo no cuadra.

Proyecto de Construcción de Software · UNEMI, 2026.

## Cómo ejecutarlo en Windows

1. Instala **Python 3.12 o superior** desde [python.org](https://www.python.org/downloads/) y marca **"Add python.exe to PATH"**.
2. Haz doble clic en **`iniciar_zentry.bat`**.

La primera vez crea el entorno `.venv` e instala las dependencias, y eso tarda varios minutos. Las siguientes veces arranca enseguida. El script:

- prepara la base de datos y, si es nueva, carga los datos de demostración;
- abre el servidor web en una ventana ("Zentry - Servidor web") y entra a http://127.0.0.1:8000/ en el navegador;
- conecta las cámaras en otra ventana (lo que se explica más abajo).

Para detener todo, cierra las ventanas "Zentry - ...".

**Usuarios de demostración:** `admin` / `Zentry2026` (administrador) y `seguridad` / `Zentry2026` (personal de seguridad).

## Cámaras

- **Simulador:** viene en la carpeta `simulador/` del proyecto (también se acepta en `..\Entorno virtual\` o en la ruta de la variable `ZENTRY_SIMULADOR`). El script conecta sus dos cámaras virtuales y abre el simulador en otra pestaña. Esa pestaña tiene que quedar abierta.
- **Celular con IP Webcam:** en la app pulsa *Iniciar servidor*. Luego entra en http://127.0.0.1:8000/admin/ → Cámaras → Agregar, elige el tipo "Cámara IP (IP Webcam)" y en *Fuente* pon la dirección que muestra la app (por ejemplo `http://192.168.1.8:8080`). El agente la toma solo, sin reiniciar nada.

## En Linux o macOS

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd Zentry && python manage.py migrate && python manage.py datos_demo && cd ..
python Zentry/manage.py runserver        # en una terminal
python camaras_virtuales.py              # en otra: simulador (o python -m agente para IP Webcam)
```

## Pruebas

```bash
cd Zentry && python manage.py test        # servidor web
python -m unittest discover -s agente/tests -t .   # agente (desde la raíz)
```

## Carpetas

| Carpeta | Qué tiene |
|---|---|
| `Zentry/` | Servidor web en Django: apps `cuentas`, `registro` (M1), `verificacion` (M2) y `alertas` (M3) |
| `agente/` | Agente local: captura de las cámaras, YOLO, lector de placas y envío a la API con token |
| `modelos/` | Modelos de IA: `best.pt` (placas), `yolov8n.pt` (vehículos) y `placas_ocr.pt` (lector propio) |
| `camaras_virtuales.py` | Conecta las cámaras del simulador con Zentry |
| `iniciar_zentry.bat` | Levanta todo el sistema en Windows con doble clic |
