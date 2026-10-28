/*
 * Zentry · editor de plazas (RF-03). Solo para el Administrador.
 * Permite dibujar plazas (4 clics), mover vértices o la plaza entera, renombrar,
 * eliminar, deshacer (Ctrl+Z) y "Detectar automáticamente" (aplica las plazas que
 * propuso el agente: verdad del simulador o detección de líneas amarillas).
 * Guarda con PUT /api/camaras/<codigo>/plazas/.
 */
(function () {
  'use strict';

  const Z = window.Zentry;
  const SVG = 'http://www.w3.org/2000/svg';
  const URL_API = (codigo) => (document.body.dataset.urlPlazas || '/api/camaras/CODIGO/plazas/').replace('CODIGO', encodeURIComponent(codigo));

  function copiar(plazas) {
    return plazas.map((p) => ({ ...p, poligono: p.poligono.map((punto) => [...punto]) }));
  }

  function plantilla() {
    return `
      <div class="cabeza-modal">
        <h2 data-ed="titulo">Editar plazas</h2>
        <button type="button" aria-label="Cerrar" data-ed="cerrar">${Z.icono('x', 24)}</button>
      </div>
      <div class="modo">Modo edición · los cambios no se aplican hasta guardar</div>
      <div class="herramientas">
        <button type="button" class="boton-secundario" data-ed="nueva">${Z.icono('plus', 14)}<span>Nueva plaza</span></button>
        <button type="button" class="boton-secundario" data-ed="deshacer" disabled>${Z.icono('undo-2', 14)}Deshacer</button>
        <button type="button" class="boton-secundario" data-ed="detectar">${Z.icono('scan-line', 14)}Detectar automáticamente</button>
      </div>
      <p class="ayuda" data-ed="ayuda"></p>
      <div class="lienzo" data-ed="lienzo">
        <img data-ed="imagen" alt="Imagen de la cámara para editar plazas">
        <svg data-ed="svg" preserveAspectRatio="none"></svg>
      </div>
      <div class="seleccion" data-ed="seleccion" hidden>
        <div class="fila">
          <strong data-ed="sel-nombre"></strong>
          <button type="button" class="enlace" data-ed="renombrar">Renombrar</button>
          <button type="button" class="enlace rojo" data-ed="eliminar">Eliminar</button>
        </div>
        <form class="fila mt-3" data-ed="form-nombre" hidden>
          <input class="entrada" style="max-width: 220px" maxlength="24" data-ed="nombre" data-mayusculas aria-label="Nombre de plaza">
          <button type="submit" class="boton">Renombrar</button>
          <span class="error-campo" data-ed="error-nombre" hidden></span>
        </form>
        <code data-ed="coordenadas"></code>
      </div>
      <p class="error-general error-editor" data-ed="error" hidden></p>
      <div class="acciones">
        <button type="button" class="boton-secundario" data-ed="descartar">Descartar cambios</button>
        <button type="button" class="boton" data-ed="guardar" disabled>Guardar cambios</button>
      </div>`;
  }

  async function abrir(codigo) {
    let datos;
    try {
      datos = await Z.enviar(URL_API(codigo), { method: 'GET' });
    } catch (error) {
      Z.aviso(error.message, { error: true });
      return;
    }
    const dialogo = document.createElement('dialog');
    dialogo.className = 'modal grande editor';
    dialogo.setAttribute('aria-label', `Editar plazas · ${datos.camara.nombre}`);
    dialogo.innerHTML = plantilla();
    (document.querySelector('.muro') || document.body).appendChild(dialogo);
    const $ = (clave) => dialogo.querySelector(`[data-ed="${clave}"]`);
    $('titulo').textContent = `Editar plazas · ${datos.camara.nombre}`;

    const alto = (1000 * datos.camara.alto) / datos.camara.ancho;
    const svg = $('svg');
    svg.setAttribute('viewBox', `0 0 1000 ${alto}`);
    $('lienzo').style.aspectRatio = `${datos.camara.ancho} / ${datos.camara.alto}`;
    $('imagen').src = `${datos.camara.imagen}?t=${Date.now()}`;
    $('imagen').onerror = () => { $('imagen').replaceWith(Object.assign(document.createElement('div'), { className: 'sin-imagen', textContent: 'La cámara todavía no envió imágenes' })); };

    const inicial = JSON.stringify(datos.plazas.map((p) => [p.id, p.nombre, p.poligono]));
    const vehiculosPorId = Object.fromEntries(datos.plazas.map((p) => [p.id, p.vehiculos]));
    let borrador = copiar(datos.plazas);
    let historial = [];
    let seleccion = null;
    let creando = false;
    let puntos = [];
    let escaneando = false;
    let arrastre = null;
    let siguienteTemporal = -1;

    const sucio = () => JSON.stringify(borrador.map((p) => [p.id, p.nombre, p.poligono])) !== inicial;
    const nombresOcupados = (excepto) => new Set([
      ...datos.nombres_otras_camaras.map((n) => n.toUpperCase()),
      ...borrador.filter((p) => p !== excepto).map((p) => p.nombre.toUpperCase()),
    ]);

    function siguienteNombre() {
      const usados = nombresOcupados(null);
      let numero = Number(String(datos.siguiente_nombre).replace(/\D/g, '')) || 1;
      while (usados.has(`P${numero}`)) numero += 1;
      return `P${numero}`;
    }

    function guardarPaso() {
      historial.push(copiar(borrador));
      $('deshacer').disabled = false;
    }

    function deshacer() {
      const ultimo = historial.pop();
      if (!ultimo) return;
      borrador = ultimo;
      puntos = [];
      creando = false;
      if (seleccion && !borrador.some((p) => p.clave === seleccion)) seleccion = null;
      $('deshacer').disabled = !historial.length;
      pintar();
    }

    borrador.forEach((p) => { p.clave = String(p.id); });

    function coordenada(evento) {
      const caja = svg.getBoundingClientRect();
      return [
        Math.max(0, Math.min(1, (evento.clientX - caja.left) / caja.width)),
        Math.max(0, Math.min(1, (evento.clientY - caja.top) / caja.height)),
      ];
    }

    function nodo(nombre, atributos, padre) {
      const elemento = document.createElementNS(SVG, nombre);
      Object.entries(atributos).forEach(([k, v]) => elemento.setAttribute(k, v));
      padre.appendChild(elemento);
      return elemento;
    }

    function pintar() {
      svg.replaceChildren();
      svg.classList.toggle('creando', creando);
      borrador.forEach((plaza) => {
        const elegida = plaza.clave === seleccion;
        const grupo = nodo('g', {}, svg);
        const poligono = nodo('polygon', {
          points: plaza.poligono.map((p) => `${p[0] * 1000},${p[1] * alto}`).join(' '),
          fill: elegida ? '#5580C2' : '#0A2A66', 'fill-opacity': '.4', stroke: elegida ? '#fff' : '#84A0C9', 'stroke-width': 3,
          'vector-effect': 'non-scaling-stroke',
        }, grupo);
        poligono.addEventListener('pointerdown', (e) => empezarArrastre(e, plaza));
        const cx = plaza.poligono.reduce((s, p) => s + (p[0] * 1000) / plaza.poligono.length, 0);
        const cy = plaza.poligono.reduce((s, p) => s + (p[1] * alto) / plaza.poligono.length, 0);
        const texto = nodo('text', { x: cx, y: cy + 6, 'text-anchor': 'middle', 'font-size': 17, 'font-weight': 700, fill: '#fff', 'pointer-events': 'none' }, grupo);
        texto.textContent = plaza.nombre;
        plaza.poligono.forEach((punto, indice) => {
          const vertice = nodo('circle', {
            class: 'vertice', cx: punto[0] * 1000, cy: punto[1] * alto, r: elegida ? 9 : 6,
            fill: '#fff', stroke: '#0A2A66', 'stroke-width': 2,
          }, grupo);
          vertice.addEventListener('pointerdown', (e) => empezarArrastre(e, plaza, indice));
        });
      });
      puntos.forEach((p) => nodo('circle', { cx: p[0] * 1000, cy: p[1] * alto, r: 7, fill: '#facc15', 'pointer-events': 'none' }, svg));
      if (puntos.length > 1) {
        nodo('polyline', { points: puntos.map((p) => `${p[0] * 1000},${p[1] * alto}`).join(' '), stroke: '#facc15', 'stroke-width': 3, fill: 'none', 'pointer-events': 'none' }, svg);
      }
      $('ayuda').textContent = creando
        ? `Haz clic en 4 puntos de la imagen (${puntos.length}/4).`
        : 'Selecciona una plaza. Arrastra sus vértices para corregirla o su interior para moverla.';
      $('nueva').querySelector('span').textContent = creando ? 'Cancelar nueva plaza' : 'Nueva plaza';
      const elegida = borrador.find((p) => p.clave === seleccion);
      $('seleccion').hidden = !elegida;
      if (elegida) {
        $('sel-nombre').textContent = elegida.nombre;
        $('coordenadas').textContent = JSON.stringify(elegida.poligono.map((p) => p.map((v) => Number(v.toFixed(4)))));
      }
      $('guardar').disabled = escaneando || creando || !sucio();
      ['nueva', 'detectar'].forEach((k) => { $(k).disabled = escaneando; });
    }

    function empezarArrastre(evento, plaza, vertice) {
      if (creando || escaneando) return;
      evento.stopPropagation();
      evento.preventDefault();
      seleccion = plaza.clave;
      guardarPaso();
      arrastre = { plaza, vertice, inicio: coordenada(evento), puntos: plaza.poligono.map((p) => [...p]) };
      svg.setPointerCapture(evento.pointerId);
      pintar();
    }

    svg.addEventListener('pointermove', (evento) => {
      if (!arrastre) return;
      const pos = coordenada(evento);
      let nuevos = arrastre.puntos.map((p) => [...p]);
      if (arrastre.vertice !== undefined) {
        nuevos[arrastre.vertice] = pos;
      } else {
        const xs = nuevos.map((p) => p[0]);
        const ys = nuevos.map((p) => p[1]);
        const dx = Math.max(-Math.min(...xs), Math.min(1 - Math.max(...xs), pos[0] - arrastre.inicio[0]));
        const dy = Math.max(-Math.min(...ys), Math.min(1 - Math.max(...ys), pos[1] - arrastre.inicio[1]));
        nuevos = nuevos.map((p) => [p[0] + dx, p[1] + dy]);
      }
      arrastre.plaza.poligono = nuevos;
      pintar();
    });

    const soltar = (evento) => {
      arrastre = null;
      if (svg.hasPointerCapture(evento.pointerId)) svg.releasePointerCapture(evento.pointerId);
    };
    svg.addEventListener('pointerup', soltar);
    svg.addEventListener('pointercancel', soltar);

    svg.addEventListener('pointerdown', (evento) => {
      if (!creando || escaneando) {
        if (!evento.target.closest('polygon, circle')) { seleccion = null; pintar(); }
        return;
      }
      puntos.push(coordenada(evento));
      if (puntos.length === 4) {
        guardarPaso();
        const nueva = { id: null, clave: String(siguienteTemporal--), nombre: siguienteNombre(), poligono: puntos };
        borrador.push(nueva);
        seleccion = nueva.clave;
        creando = false;
        puntos = [];
      }
      pintar();
    });

    $('nueva').addEventListener('click', () => { creando = !creando; puntos = []; seleccion = null; pintar(); });
    $('deshacer').addEventListener('click', deshacer);

    $('detectar').addEventListener('click', () => {
      if (!datos.sugeridas || !datos.sugeridas.length) {
        Z.aviso('El agente todavía no propuso plazas para esta cámara. Inicia el agente o el entorno virtual.', { error: true });
        return;
      }
      escaneando = true;
      creando = false;
      puntos = [];
      const capa = document.createElement('div');
      capa.className = 'escaneo';
      capa.innerHTML = `<span>${Z.icono('scan-line', 18)}Buscando las líneas amarillas…</span>`;
      $('lienzo').appendChild(capa);
      pintar();
      setTimeout(() => {
        guardarPaso();
        const porNombre = Object.fromEntries(borrador.map((p) => [p.nombre.toUpperCase(), p]));
        borrador = datos.sugeridas.map((s) => {
          const existente = porNombre[s.nombre.toUpperCase()];
          return existente
            ? { ...existente, poligono: s.poligono.map((p) => [...p]) }
            : { id: null, clave: String(siguienteTemporal--), nombre: s.nombre.toUpperCase(), poligono: s.poligono.map((p) => [...p]) };
        });
        seleccion = null;
        escaneando = false;
        capa.remove();
        pintar();
        Z.aviso(`Se detectaron ${borrador.length} plazas`);
      }, 1500);
    });

    $('renombrar').addEventListener('click', () => {
      const elegida = borrador.find((p) => p.clave === seleccion);
      if (!elegida) return;
      $('form-nombre').hidden = false;
      $('nombre').value = elegida.nombre;
      $('error-nombre').hidden = true;
      $('nombre').focus();
    });

    $('form-nombre').addEventListener('submit', (evento) => {
      evento.preventDefault();
      const elegida = borrador.find((p) => p.clave === seleccion);
      const valor = $('nombre').value.trim().toUpperCase();
      const error = (texto) => { $('error-nombre').textContent = texto; $('error-nombre').hidden = false; };
      if (!valor) return error('Ingresa un nombre');
      if (nombresOcupados(elegida).has(valor)) return error('El nombre no se puede repetir');
      guardarPaso();
      elegida.nombre = valor;
      $('form-nombre').hidden = true;
      pintar();
    });

    $('eliminar').addEventListener('click', () => {
      const elegida = borrador.find((p) => p.clave === seleccion);
      if (!elegida) return;
      const conVehiculos = elegida.id && (vehiculosPorId[elegida.id] || []).length;
      if (!window.confirm(`¿Eliminar ${elegida.nombre}?${conVehiculos ? ' El vehículo asignado quedará sin plaza al guardar.' : ''}`)) return;
      guardarPaso();
      borrador = borrador.filter((p) => p !== elegida);
      seleccion = null;
      pintar();
    });

    function cerrar() {
      if (sucio() && !window.confirm('Hay cambios sin guardar. ¿Descartar cambios?')) return;
      dialogo.close();
    }
    $('cerrar').addEventListener('click', cerrar);
    $('descartar').addEventListener('click', cerrar);
    dialogo.addEventListener('cancel', (evento) => { evento.preventDefault(); cerrar(); });
    dialogo.addEventListener('close', () => { document.removeEventListener('keydown', teclado, true); dialogo.remove(); });

    function teclado(evento) {
      if ((evento.ctrlKey || evento.metaKey) && evento.key.toLowerCase() === 'z' && !(evento.target instanceof HTMLInputElement)) {
        evento.preventDefault();
        evento.stopPropagation();
        deshacer();
      }
    }
    document.addEventListener('keydown', teclado, true);

    $('guardar').addEventListener('click', async () => {
      const ids = new Set(borrador.map((p) => p.id).filter(Boolean));
      const quedanSinPlaza = datos.plazas.filter((p) => !ids.has(p.id)).reduce((n, p) => n + p.vehiculos.length, 0);
      if (quedanSinPlaza && !window.confirm(`Se dejarán ${quedanSinPlaza} vehículo(s) sin plaza asignada. ¿Guardar?`)) return;
      $('guardar').disabled = true;
      $('error').hidden = true;
      try {
        await Z.enviar(URL_API(codigo), {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ plazas: borrador.map((p) => ({ id: p.id, nombre: p.nombre, poligono: p.poligono })) }),
        });
        Z.avisoTrasRecargar('Plazas actualizadas');
        window.location.reload();
      } catch (error) {
        $('error').textContent = error.message;
        $('error').hidden = false;
        $('guardar').disabled = false;
      }
    });

    pintar();
    dialogo.showModal();
  }

  window.ZentryEditor = { abrir };
})();
