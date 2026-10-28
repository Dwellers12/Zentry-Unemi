"""
Configuración de Django para Zentry.

Organización (ver docs/frontend-y-camaras-virtuales.md y la Tarea 5):
  - cuentas:      inicio de sesión, roles y token del agente (transversal).
  - registro:     M1 · residentes, vehículos, plazas y cámaras.
  - verificacion: M2 · lecturas del agente y estado de cada plaza.
  - alertas:      M3 · alertas, evidencia, notificaciones, dashboard y reportes.

Los valores sensibles o que cambian entre equipos se leen de variables de entorno.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
# Raíz del repositorio (agente/, modelos/, ejemplos/ y .agente_token).
RAIZ_PROYECTO = BASE_DIR.parent


def _env_bool(nombre, defecto):
    valor = os.environ.get(nombre)
    if valor is None:
        return defecto
    return valor.strip().lower() in ('1', 'true', 'si', 'sí', 'yes', 'on')


# Seguridad -------------------------------------------------------------------
# Se lee de la variable de entorno DJANGO_SECRET_KEY; el valor por defecto es solo para desarrollo.
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'django-insecure-solo-para-desarrollo-local')

DEBUG = _env_bool('ZENTRY_DEBUG', True)

ALLOWED_HOSTS = [h.strip() for h in os.environ.get('ZENTRY_ALLOWED_HOSTS', '*').split(',') if h.strip()]

# En producción (ZENTRY_DEBUG=0) el sitio va detrás de HTTPS (balanceador de AWS):
# las cookies de sesión y CSRF solo viajan cifradas.
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.environ.get('ZENTRY_CSRF_ORIGINS', '').split(',') if o.strip()]


# Aplicaciones ----------------------------------------------------------------

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'rest_framework',
    'rest_framework.authtoken',
    'cuentas',
    'registro',
    'verificacion',
    'alertas',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'Zentry.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'cuentas.context_processors.zentry',
                'alertas.context_processors.alertas',
            ],
        },
    },
]

WSGI_APPLICATION = 'Zentry.wsgi.application'


# Base de datos ----------------------------------------------------------------
# SQLite mientras se desarrolla. Para usar PostgreSQL en Amazon RDS basta con
# definir ZENTRY_DB_ENGINE=postgresql y las variables ZENTRY_DB_* (y instalar
# psycopg); el código no cambia porque todo pasa por el ORM.

if os.environ.get('ZENTRY_DB_ENGINE', 'sqlite').lower() == 'postgresql':
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.environ.get('ZENTRY_DB_NAME', 'zentry'),
            'USER': os.environ.get('ZENTRY_DB_USER', 'zentry'),
            'PASSWORD': os.environ.get('ZENTRY_DB_PASSWORD', ''),
            'HOST': os.environ.get('ZENTRY_DB_HOST', 'localhost'),
            'PORT': os.environ.get('ZENTRY_DB_PORT', '5432'),
            'CONN_MAX_AGE': 60,
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            # ZENTRY_SQLITE permite usar otra base (p. ej. una copia para pruebas).
            'NAME': os.environ.get('ZENTRY_SQLITE', BASE_DIR / 'db.sqlite3'),
            # El agente y la web escriben a la vez: se espera en vez de fallar.
            'OPTIONS': {'timeout': 20},
        }
    }


# Contraseñas -----------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LOGIN_URL = 'cuentas:iniciar_sesion'
LOGIN_REDIRECT_URL = 'alertas:dashboard'
LOGOUT_REDIRECT_URL = 'cuentas:iniciar_sesion'


# Idioma y zona horaria -------------------------------------------------------

LANGUAGE_CODE = 'es'
TIME_ZONE = 'America/Guayaquil'
USE_I18N = True
USE_TZ = True


# Archivos estáticos y subidos -------------------------------------------------

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Las imágenes de las cámaras y la evidencia NO se publican en MEDIA_URL: se
# entregan con vistas que exigen sesión iniciada (RNF-08).
MEDIA_ROOT = Path(os.environ.get('ZENTRY_MEDIA', BASE_DIR / 'media'))

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# Django REST Framework ---------------------------------------------------------
# La web usa la sesión; el agente usa su token (cabecera "Authorization: Token ...").

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
        'rest_framework.authentication.TokenAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.IsAuthenticated'],
    'DEFAULT_RENDERER_CLASSES': ['rest_framework.renderers.JSONRenderer'],
}


# Correo -----------------------------------------------------------------------

# Zentry no envía correos con Django (los avisos van por el puerto de notificación:
# consola, SNS o SES). En desarrollo cualquier correo se muestra en la terminal.
MAILERS = {
    'default': {
        'BACKEND': os.environ.get('ZENTRY_EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend'
                                  if DEBUG else 'django.core.mail.backends.smtp.EmailBackend'),
    },
}


# Registro (logs) ----------------------------------------------------------------
# Los avisos de Zentry (alertas nuevas, notificaciones de consola) salen por la terminal.

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {'simple': {'format': '{asctime} {levelname} {name}: {message}', 'style': '{', 'datefmt': '%H:%M:%S'}},
    'handlers': {'consola': {'class': 'logging.StreamHandler', 'formatter': 'simple'}},
    'loggers': {'zentry': {'handlers': ['consola'], 'level': os.environ.get('ZENTRY_LOG', 'INFO'), 'propagate': False}},
}


# Zentry -------------------------------------------------------------------------

ZENTRY = {
    # Puertos y adaptadores (Tarea 5): qué implementación usa cada puerto.
    # Producción: 'alertas.adaptadores.EvidenciaS3' y 'alertas.adaptadores.NotificacionSES'
    # (o NotificacionSNS), con las variables ZENTRY_S3_BUCKET, ZENTRY_SNS_TOPIC, etc.
    'EVIDENCIA': os.environ.get('ZENTRY_EVIDENCIA', 'alertas.adaptadores.EvidenciaLocal'),
    'NOTIFICACION': os.environ.get('ZENTRY_NOTIFICACION', 'alertas.adaptadores.NotificacionConsola'),

    # Verificación: capturas seguidas que deben coincidir antes de aceptar un
    # cambio en una plaza (evita alertas por una lectura suelta del OCR).
    'LECTURAS_PARA_CONFIRMAR': int(os.environ.get('ZENTRY_LECTURAS_PARA_CONFIRMAR', 2)),
    # Por debajo de esta confianza (%) la lectura es "No concluyente".
    'CONFIANZA_MINIMA': int(os.environ.get('ZENTRY_CONFIANZA_MINIMA', 70)),

    # Agente: cada cuántos segundos captura y cuándo se considera desconectado.
    'INTERVALO_CAPTURA_S': float(os.environ.get('ZENTRY_INTERVALO_CAPTURA', 3)),
    'AGENTE_SIN_SENAL_S': int(os.environ.get('ZENTRY_AGENTE_SIN_SENAL', 30)),
    # Archivo donde `manage.py datos_demo` deja el token del agente para que el
    # agente y el simulador se conecten solos en desarrollo (está en .gitignore).
    'TOKEN_AGENTE_ARCHIVO': Path(os.environ.get('ZENTRY_TOKEN_ARCHIVO', RAIZ_PROYECTO / '.agente_token')),

    # Alertas: minutos para marcar una alerta pendiente como "Atrasada".
    'ALERTA_ATRASADA_MIN': 30,
    # Días que se conserva la evidencia de las alertas resueltas (RF-12). La aplica
    # `python manage.py depurar`, que conviene programar una vez al día.
    'RETENCION_DIAS': int(os.environ.get('ZENTRY_RETENCION_DIAS', 90)),
    # Cada cuántos milisegundos el navegador pide el estado del parqueadero (RNF-01).
    'REFRESCO_MS': 3000,
}
