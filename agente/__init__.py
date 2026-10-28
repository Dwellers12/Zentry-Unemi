"""
Agente local de captura de Zentry.

Programa separado del servidor web (Tarea 5, capa de captura): lee las cámaras
del parqueadero, reconoce vehículos y placas, y envía el resultado a la API de
Zentry. No usa la base de datos; si el servidor no responde, guarda las
capturas en una cola local y las reenvía después (RNF-05).

Uso:  python -m agente --ayuda
"""
