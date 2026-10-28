"""Cliente HTTP de la API del agente (verificacion/api.py en el servidor)."""
import json

import requests


class SinConexion(Exception):
    """El servidor no responde o devolvió un error temporal (5xx): conviene reintentar."""


class Rechazada(Exception):
    """El servidor rechazó la petición (4xx): reintentar no sirve."""

    def __init__(self, estado, detalle):
        super().__init__(f'HTTP {estado}: {detalle}')
        self.estado = estado
        self.detalle = detalle


class ClienteZentry:
    def __init__(self, config):
        self.config = config
        self.sesion = requests.Session()
        self.sesion.headers['User-Agent'] = 'zentry-agente/1.0'

    def _url(self, ruta):
        return f'{self.config.servidor}/api/agente/{ruta}'

    def _pedir(self, metodo, ruta, **kwargs):
        token = self.config.leer_token()
        if not token:
            raise Rechazada(401, 'No hay token del agente. Ejecuta: python Zentry/manage.py crear_agente')
        try:
            respuesta = self.sesion.request(
                metodo, self._url(ruta), headers={'Authorization': f'Token {token}'},
                timeout=self.config.tiempo_espera, **kwargs,
            )
        except requests.Timeout as error:
            raise SinConexion('el servidor tardó demasiado en responder') from error
        except requests.ConnectionError as error:
            raise SinConexion('el servidor no responde') from error
        except requests.RequestException as error:
            raise SinConexion(error.__class__.__name__) from error
        if respuesta.status_code >= 500:
            raise SinConexion(f'HTTP {respuesta.status_code}')
        if respuesta.status_code >= 400:
            try:
                detalle = respuesta.json()
            except ValueError:
                detalle = respuesta.text[:300]
            raise Rechazada(respuesta.status_code, detalle)
        return respuesta.json()

    def configuracion(self):
        """Cámaras activas, sus fuentes, sus plazas, el intervalo y la versión de la configuración."""
        return self._pedir('GET', 'configuracion/')

    def anunciar_camara(self, codigo, nombre, tipo, url='', ancho=None, alto=None,
                        plazas_sugeridas=None, sincronizar_plazas=False):
        datos = {'codigo': codigo, 'nombre': nombre, 'tipo': tipo, 'url': url,
                 'sincronizar_plazas': sincronizar_plazas}
        if ancho and alto:
            datos.update(ancho=int(ancho), alto=int(alto))
        if plazas_sugeridas:
            datos['plazas_sugeridas'] = plazas_sugeridas
        return self._pedir('POST', 'camaras/', json=datos)

    def enviar_captura(self, captura):
        """`captura`: dict de cola.Captura (camara, captura, capturada_en, detecciones, jpeg)."""
        datos = {
            'camara': captura['camara'],
            'captura': captura['captura'],
            'capturada_en': captura['capturada_en'],
            'detecciones': json.dumps(captura['detecciones']),
        }
        archivos = None
        if captura.get('jpeg'):
            archivos = {'imagen': (f'{captura["camara"]}.jpg', captura['jpeg'], 'image/jpeg')}
        return self._pedir('POST', 'capturas/', data=datos, files=archivos)
