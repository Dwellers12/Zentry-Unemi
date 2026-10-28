/*
 * Zentry · cámaras en vivo.
 *  - Dibuja sobre cada imagen las plazas con el color de su estado (Dashboard).
 *  - Refresca la imagen cuando el agente envía una captura nueva.
 *  - Pinta el mapa de plazas y la ficha de cada plaza (Vehículos y plazas).
 *  - Muro de cámaras a pantalla completa.
 * Los datos llegan con el evento "zentry:estado" que emite zentry.js.
 */
(function () {
  'use strict';

  const Z = window.Zentry;
  const SVG = 'http://www.w3.org/2000/svg';
  const COLORES = {
    libre: '#5baf6c',
    coincidencia: '#5580C2',
    discrepancia: '#d36b6b',
    no_registrada: '#d36b6b',
    no_concluyente: '#e0a526',
  };
  const ESTADO_CLASE = { Libre: '', Ocupada: 'ocupada', 'No autorizada': 'no-autorizada', 'No concluyente': 'no-concluyente' };

  let estado = null;
  const capturasMostradas = {};

  const plazasDe = (codigo) => (estado ? estado.plazas.filter((p) => p.camara === codigo) : []);
  const camara = (codigo) => (estado ? estado.camaras.find((c) => c.codigo === codigo) : null);
  const alto = (cam) => (cam && cam.ancho ? (1000 * cam.alto) / cam.ancho : 562.5);
  const hora = (iso) => (iso ? new Date(iso).toLocaleTimeString('es-EC', { hour12: false }) : '—');

  function nodo(nombre, atributos = {}, padre = null) {
    const elemento = document.createElementNS(SVG, nombre);
    Object.entries(atributos).forEach(([clave, valor]) => elemento.setAttribute(clave, valor));
    if (padre) padre.appendChild(elemento);
    return elemento;
  }

  function centro(puntos) {
    return puntos.reduce((s, p) => [s[0] + p[0] / puntos.length, s[1] + p[1] / puntos.length], [0, 0]);
  }

  // --------------------------------------------------------------- capa de plazas sobre la imagen
  function dibujarCapa(svg, codigo, abrirAlerta = true) {
    const cam = camara(codigo);
    const h = alto(cam);
    svg.setAttribute('viewBox', `0 0 1000 ${h}`);
    svg.replaceChildren();
    plazasDe(codigo).forEach((plaza) => {
      if (!plaza.poligono || plaza.poligono.length < 3) return;
      const color = COLORES[plaza.resultado] || COLORES.libre;
      const alerta = plaza.alerta;
      const grupo = nodo('g', alerta ? { class: 'con-alerta', role: 'link', tabindex: '0', 'aria-label': `Ver alerta ${alerta.placa || plaza.nombre}` } : {}, svg);
      const puntos = plaza.poligono.map((p) => `${p[0] * 1000},${p[1] * h}`).join(' ');
      nodo('polygon', {
        class: 'plaza', points: puntos, fill: color, 'fill-opacity': '.3',
        stroke: alerta ? '#ef4444' : color, 'stroke-width': alerta ? '4' : '2.5', 'vector-effect': 'non-scaling-stroke',
      }, grupo);
      const [cx, cy] = centro(plaza.poligono);
      const rotulo = nodo('text', {
        class: 'rotulo', x: cx * 1000, y: cy * h + 6, 'text-anchor': 'middle', fill: '#fff',
        stroke: '#0A2A66', 'stroke-width': '3', 'font-size': '17',
      }, grupo);
      rotulo.textContent = plaza.nombre;
      if (alerta) {
        // Recuadro punteado del vehículo y etiqueta compacta DENTRO de su plaza (no se encima con las vecinas).
        const caja = plaza.vehiculo_caja || [
          Math.min(...plaza.poligono.map((p) => p[0])), Math.min(...plaza.poligono.map((p) => p[1])),
          Math.max(...plaza.poligono.map((p) => p[0])), Math.max(...plaza.poligono.map((p) => p[1])),
        ];
        nodo('rect', {
          x: caja[0] * 1000, y: caja[1] * h, width: (caja[2] - caja[0]) * 1000, height: (caja[3] - caja[1]) * h,
          fill: 'none', stroke: '#ef4444', 'stroke-width': '2', 'stroke-dasharray': '7 4', 'vector-effect': 'non-scaling-stroke',
        }, grupo);
        const ancho = Math.max(60, (caja[2] - caja[0]) * 1000);
        const texto = alerta.placa || 'ILEGIBLE';
        const tamano = Math.max(9, Math.min(13, ancho / (texto.length * 0.75)));
        nodo('rect', { x: caja[0] * 1000, y: caja[1] * h - tamano - 8, width: ancho, height: tamano + 8, rx: 4, fill: '#b91c1c' }, grupo);
        const etiqueta = nodo('text', {
          x: caja[0] * 1000 + ancho / 2, y: caja[1] * h - 6, 'text-anchor': 'middle', fill: '#fff',
          'font-size': tamano, 'font-weight': '700',
        }, grupo);
        etiqueta.textContent = texto;
        if (abrirAlerta) {
          const ir = (evento) => { evento.stopPropagation(); window.location.href = Z.urlAlerta(alerta.id); };
          grupo.addEventListener('click', ir);
          grupo.addEventListener('keydown', (evento) => { if (evento.key === 'Enter' || evento.key === ' ') ir(evento); });
        }
      }
    });
  }

  // --------------------------------------------------------------- imagen de la cámara
  function refrescarImagen(contenedor, codigo) {
    const cam = camara(codigo);
    const imagen = contenedor.querySelector('[data-feed-imagen]');
    const vacio = contenedor.querySelector('[data-feed-vacio]');
    if (!imagen) return;
    if (!cam || !cam.tiene_imagen) {
      imagen.hidden = true;
      if (vacio) vacio.hidden = false;
      return;
    }
    const marca = cam.ultima_captura || '';
    if (imagen.dataset.marca === marca && !imagen.hidden) return;
    const nueva = new Image();
    nueva.onload = () => {
      imagen.src = nueva.src;
      imagen.hidden = false;
      imagen.dataset.marca = marca;
      if (vacio) vacio.hidden = true;
    };
    nueva.onerror = () => {
      if (!imagen.dataset.marca) { imagen.hidden = true; if (vacio) vacio.hidden = false; }
    };
    nueva.src = `${Z.urlImagen(codigo)}?t=${encodeURIComponent(marca)}`;
    capturasMostradas[codigo] = marca;
  }

  // --------------------------------------------------------------- tarjetas del Dashboard
  function pintarTarjetas() {
    document.querySelectorAll('article[data-camara]').forEach((tarjeta) => {
      const codigo = tarjeta.dataset.camara;
      const cam = camara(codigo);
      const plazas = plazasDe(codigo);
      const conAlerta = plazas.filter((p) => p.alerta);
      const feed = tarjeta.querySelector('[data-feed]');
      tarjeta.classList.toggle('con-alertas', conAlerta.length > 0);
      const contador = tarjeta.querySelector('[data-camara-alertas]');
      if (contador) {
        contador.hidden = !conAlerta.length;
        contador.textContent = conAlerta.length === 1 ? '1 alerta' : `${conAlerta.length} alertas`;
        if (conAlerta.length) contador.href = Z.urlAlerta(conAlerta[0].alerta.id);
      }
      const pastilla = tarjeta.querySelector('[data-camara-estado]');
      if (pastilla && cam) {
        pastilla.className = `estado-camara ${cam.en_linea ? 'en-linea' : 'sin-senal'}`;
        pastilla.textContent = cam.en_linea ? 'En línea' : 'Sin señal';
      }
      const libres = tarjeta.querySelector('[data-camara-libres]');
      if (libres) libres.textContent = `${plazas.filter((p) => p.resultado === 'libre').length} de ${plazas.length}`;
      const lectura = tarjeta.querySelector('[data-camara-lectura]');
      if (lectura && cam) lectura.textContent = hora(cam.ultima_captura);
      if (feed) {
        refrescarImagen(feed, codigo);
        dibujarCapa(feed.querySelector('[data-feed-capa]'), codigo);
      }
    });
  }

  // --------------------------------------------------------------- mapa de plazas (Vehículos y plazas)
  function pintarMapa() {
    document.querySelectorAll('.plaza-tile[data-plaza]').forEach((tile) => {
      const plaza = estado.plazas.find((p) => String(p.id) === tile.dataset.plaza);
      if (!plaza) return;
      tile.className = `plaza-tile ${ESTADO_CLASE[plaza.estado] || ''}`.trim();
      tile.querySelector('[data-plaza-placa]').textContent = plaza.placa || (plaza.estado === 'No concluyente' ? 'Placa ilegible' : 'Sin vehículo');
      tile.querySelector('[data-plaza-estado]').textContent = plaza.estado;
      tile.setAttribute('aria-label', `${plaza.nombre}: ${plaza.estado}${plaza.placa ? ', ' + plaza.placa : ''}`);
    });
    const ficha = document.getElementById('modal-plaza');
    if (ficha && ficha.open && ficha.dataset.plaza) llenarFicha(ficha.dataset.plaza);
  }

  // Vista ampliada de una plaza: escala la imagen para que la plaza quede centrada.
  function vistaAmpliada(contenedor, plaza) {
    const cam = camara(plaza.camara);
    contenedor.style.aspectRatio = cam ? `${cam.ancho} / ${cam.alto}` : '';
    if (!cam || !cam.tiene_imagen || !plaza.poligono.length) {
      contenedor.innerHTML = `<div class="sin-imagen">${Z.icono('camera', 32)}<span>Sin imagen de la cámara</span></div>`;
      return;
    }
    const xs = plaza.poligono.map((p) => p[0]);
    const ys = plaza.poligono.map((p) => p[1]);
    const caja = plaza.vehiculo_caja || [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
    const [cx, cy] = [(caja[0] + caja[2]) / 2, (caja[1] + caja[3]) / 2];
    const escala = Math.max(1, Math.min(5, 0.8 / Math.max(caja[2] - caja[0], caja[3] - caja[1])));
    const limitar = (t) => Math.min(0, Math.max(1 - escala, t));
    const tx = limitar(0.5 - escala * cx) * 100;
    const ty = limitar(0.5 - escala * cy) * 100;
    const marca = encodeURIComponent(cam.ultima_captura || '');
    contenedor.innerHTML = `<div class="zoom" style="transform: translate(${tx}%, ${ty}%) scale(${escala})">
      <img src="${Z.urlImagen(cam.codigo)}?t=${marca}" alt="Vista de la plaza ${Z.escapar(plaza.nombre)}"></div>`;
  }

  function llenarFicha(id) {
    const ficha = document.getElementById('modal-plaza');
    const plaza = estado && estado.plazas.find((p) => String(p.id) === String(id));
    if (!ficha || !plaza) return;
    ficha.dataset.plaza = plaza.id;
    const poner = (clave, texto) => { const el = ficha.querySelector(`[data-ficha="${clave}"]`); if (el) el.textContent = texto; };
    poner('titulo', `Plaza ${plaza.nombre}`);
    poner('estado', plaza.estado);
    poner('placa', plaza.placa || (plaza.estado === 'No concluyente' ? 'Placa ilegible' : 'Ninguno'));
    poner('propietario', plaza.detectado ? `${plaza.detectado.residente} (${plaza.detectado.marca_modelo} · ${plaza.detectado.color})`
      : plaza.placa ? 'No registrado' : '—');
    poner('asignado', plaza.asignada_a
      ? `${plaza.asignada_a.residente} · Dpto. ${plaza.asignada_a.departamento} · ${plaza.asignada_a.vehiculos.map((v) => v.placa).join(', ')}`
      : 'Plaza sin asignar');
    poner('confianza', plaza.confianza !== null ? `${plaza.confianza} %${plaza.corregida ? ' (corregida a mano)' : ''}` : '—');
    poner('desde', plaza.desde ? `${hora(plaza.desde)} · ${Z.hace(Date.parse(plaza.desde))}` : '—');
    ficha.querySelector('[data-ficha="confianza-fila"]').hidden = plaza.estado === 'Libre';
    ficha.querySelector('[data-ficha="desde-fila"]').hidden = plaza.estado === 'Libre';
    const verAlerta = ficha.querySelector('[data-ficha="ver-alerta"]');
    verAlerta.hidden = !plaza.alerta;
    if (plaza.alerta) verAlerta.href = Z.urlAlerta(plaza.alerta.id);
    const editar = ficha.querySelector('[data-ficha="editar"]');
    editar.hidden = !(Z.esAdmin && plaza.camara && window.ZentryEditor);
    editar.dataset.camara = plaza.camara || '';
    vistaAmpliada(ficha.querySelector('[data-ficha="feed"]'), plaza);
  }

  document.addEventListener('click', (evento) => {
    const tile = evento.target.closest('.plaza-tile[data-plaza]');
    if (tile) {
      llenarFicha(tile.dataset.plaza);
      Z.abrirModal(document.getElementById('modal-plaza'));
      return;
    }
    const editar = evento.target.closest('[data-ficha="editar"]');
    if (editar && editar.dataset.camara && window.ZentryEditor) {
      Z.cerrarModal(editar.closest('dialog'));
      window.ZentryEditor.abrir(editar.dataset.camara);
      return;
    }
    const elegir = evento.target.closest('[data-editar-plazas]');
    if (elegir && window.ZentryEditor) {
      Z.cerrarModal(elegir.closest('dialog'));
      window.ZentryEditor.abrir(elegir.dataset.editarPlazas);
    }
  });

  // --------------------------------------------------------------- muro de cámaras (pantalla completa)
  const muro = { raiz: null, seleccion: null, reloj: null };

  function tarjetaOscura(codigo, grande) {
    const cam = camara(codigo);
    const plazas = plazasDe(codigo);
    const conAlerta = plazas.some((p) => p.alerta);
    const libres = plazas.filter((p) => p.resultado === 'libre').length;
    const relacion = cam ? `${cam.ancho} / ${cam.alto}` : '16 / 9';
    return `<article class="tarjeta-oscura${conAlerta ? ' con-alertas' : ''}" data-muro-camara="${Z.escapar(codigo)}">
      <div class="feed${grande ? '' : ' clic'}" style="aspect-ratio: ${relacion}" ${grande ? '' : `role="button" tabindex="0" aria-label="Ampliar ${Z.escapar(cam ? cam.nombre : codigo)}"`}>
        <img data-feed-imagen alt="Imagen de ${Z.escapar(cam ? cam.nombre : codigo)}" hidden>
        <div class="sin-imagen" data-feed-vacio hidden>${Z.icono('wifi-off', 32)}<span>Sin imagen</span></div>
        <svg preserveAspectRatio="none" data-feed-capa></svg>
      </div>
      <div class="pie"><h3>${Z.escapar(cam ? cam.nombre : codigo)}</h3><span>${libres} de ${plazas.length} libres</span></div>
      <p class="lectura">Última lectura: ${hora(cam && cam.ultima_captura)} · ${cam && cam.en_linea ? 'en línea' : 'sin señal'}</p>
    </article>`;
  }

  function pintarMuro() {
    if (!muro.raiz || !estado) return;
    const codigos = estado.camaras.map((c) => c.codigo);
    const activas = estado.plazas.filter((p) => p.alerta).length;
    muro.raiz.querySelector('[data-muro-activas]').textContent = `${activas} alerta${activas === 1 ? '' : 's'} activa${activas === 1 ? '' : 's'}`;
    const vivo = muro.raiz.querySelector('[data-muro-vivo]');
    vivo.classList.toggle('apagado', !estado.agente.en_linea);
    vivo.textContent = estado.agente.en_linea ? 'EN VIVO' : 'SIN SEÑAL';
    const zona = muro.raiz.querySelector('[data-muro-zona]');
    if (muro.seleccion && !codigos.includes(muro.seleccion)) muro.seleccion = null;
    if (!muro.seleccion) {
      zona.innerHTML = `<div class="rejilla${codigos.length > 2 ? ' tres' : ''}">${codigos.map((c) => tarjetaOscura(c, false)).join('')}</div>`;
    } else {
      const plazas = plazasDe(muro.seleccion);
      zona.innerHTML = `
        <div class="navegacion">
          <button type="button" data-muro-todas>← Todas las cámaras</button>
          <div class="botones">
            ${Z.esAdmin && window.ZentryEditor ? `<button type="button" class="boton-azul" data-muro-editar>${Z.icono('pencil', 15)}Editar plazas</button>` : ''}
            <button type="button" class="boton-oscuro redondo" aria-label="Cámara anterior" data-muro-paso="-1">${Z.icono('chevron-left', 20)}</button>
            <button type="button" class="boton-oscuro redondo" aria-label="Cámara siguiente" data-muro-paso="1">${Z.icono('chevron-right', 20)}</button>
          </div>
        </div>
        <div class="ampliada">
          ${tarjetaOscura(muro.seleccion, true)}
          <aside><h3>Plazas de la cámara</h3>
            ${plazas.map((p) => `<div class="plaza"><div><strong>${Z.escapar(p.nombre)}</strong>
              <small>${Z.escapar(p.placa || (p.estado === 'No concluyente' ? 'Placa ilegible' : 'Sin vehículo'))}</small></div>
              <span style="color: ${COLORES[p.resultado] || COLORES.libre}">${Z.escapar(p.estado)}</span></div>`).join('') || '<p class="texto-mini">Sin plazas</p>'}
          </aside>
        </div>`;
    }
    zona.querySelectorAll('[data-muro-camara]').forEach((tarjeta) => {
      const codigo = tarjeta.dataset.muroCamara;
      const feed = tarjeta.querySelector('.feed');
      refrescarImagenMuro(feed, codigo);
      dibujarCapa(feed.querySelector('[data-feed-capa]'), codigo);
    });
  }

  function refrescarImagenMuro(feed, codigo) {
    const cam = camara(codigo);
    const imagen = feed.querySelector('[data-feed-imagen]');
    const vacio = feed.querySelector('[data-feed-vacio]');
    if (!cam || !cam.tiene_imagen) { vacio.hidden = false; return; }
    imagen.src = `${Z.urlImagen(codigo)}?t=${encodeURIComponent(cam.ultima_captura || '')}`;
    imagen.hidden = false;
    imagen.onerror = () => { imagen.hidden = true; vacio.hidden = false; };
  }

  function abrirMuro(codigo) {
    if (muro.raiz) return;
    muro.seleccion = codigo || null;
    const raiz = document.createElement('div');
    raiz.className = 'muro';
    raiz.setAttribute('role', 'dialog');
    raiz.setAttribute('aria-modal', 'true');
    raiz.setAttribute('aria-label', 'Zentry · Cámaras');
    raiz.innerHTML = `
      <header class="barra">
        <h2>Zentry<span> · Cámaras</span></h2>
        <div class="derecha">
          <span class="reloj" data-muro-reloj></span>
          <span class="en-vivo" data-muro-vivo>EN VIVO</span>
          <span class="alertas-activas" data-muro-activas></span>
          <button type="button" class="boton-oscuro" data-muro-salir>${Z.icono('x', 16)}Salir</button>
        </div>
      </header>
      <main data-muro-zona></main>`;
    document.body.appendChild(raiz);
    document.body.classList.add('sin-scroll');
    muro.raiz = raiz;
    const tic = () => {
      const ahora = new Date();
      raiz.querySelector('[data-muro-reloj]').textContent =
        `${ahora.toLocaleDateString('es-EC', { day: '2-digit', month: '2-digit', year: 'numeric' })} · ${ahora.toLocaleTimeString('es-EC', { hour12: false })}`;
    };
    tic();
    muro.reloj = setInterval(tic, 1000);
    pintarMuro();
    raiz.querySelector('[data-muro-salir]').focus();
    if (raiz.requestFullscreen) raiz.requestFullscreen().catch(() => {});
  }

  function cerrarMuro() {
    if (!muro.raiz) return;
    clearInterval(muro.reloj);
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    muro.raiz.remove();
    muro.raiz = null;
    if (!document.querySelector('dialog[open]')) document.body.classList.remove('sin-scroll');
  }

  function pasoMuro(direccion) {
    const codigos = estado.camaras.map((c) => c.codigo);
    const indice = codigos.indexOf(muro.seleccion);
    muro.seleccion = codigos[(indice + direccion + codigos.length) % codigos.length];
    pintarMuro();
  }

  document.addEventListener('fullscreenchange', () => {
    // Salir de pantalla completa con Esc cierra el muro (salvo que se esté editando plazas).
    if (!document.fullscreenElement && muro.raiz && !document.querySelector('dialog[open]')) cerrarMuro();
  });

  document.addEventListener('click', (evento) => {
    const abrir = evento.target.closest('[data-abrir-muro]');
    if (abrir && !muro.raiz) {
      abrirMuro(abrir.dataset.abrirMuro || null);
      return;
    }
    if (!muro.raiz || !muro.raiz.contains(evento.target)) return;
    if (evento.target.closest('[data-muro-salir]')) return cerrarMuro();
    if (evento.target.closest('[data-muro-todas]')) { muro.seleccion = null; return pintarMuro(); }
    const paso = evento.target.closest('[data-muro-paso]');
    if (paso) return pasoMuro(Number(paso.dataset.muroPaso));
    if (evento.target.closest('[data-muro-editar]') && window.ZentryEditor) return window.ZentryEditor.abrir(muro.seleccion);
    const tarjeta = evento.target.closest('.feed.clic');
    if (tarjeta) { muro.seleccion = tarjeta.closest('[data-muro-camara]').dataset.muroCamara; pintarMuro(); }
  });

  document.addEventListener('keydown', (evento) => {
    if (!muro.raiz) {
      const abrir = evento.target.closest && evento.target.closest('[data-abrir-muro][role=button]');
      if (abrir && (evento.key === 'Enter' || evento.key === ' ')) { evento.preventDefault(); abrirMuro(abrir.dataset.abrirMuro || null); }
      return;
    }
    if (document.querySelector('dialog[open]')) return;
    if (evento.key === 'Escape') cerrarMuro();
    if (muro.seleccion && evento.key === 'ArrowLeft') pasoMuro(-1);
    if (muro.seleccion && evento.key === 'ArrowRight') pasoMuro(1);
    const tarjeta = evento.target.closest && evento.target.closest('.feed.clic');
    if (tarjeta && (evento.key === 'Enter' || evento.key === ' ')) {
      evento.preventDefault();
      muro.seleccion = tarjeta.closest('[data-muro-camara]').dataset.muroCamara;
      pintarMuro();
    }
  });

  // --------------------------------------------------------------- datos nuevos
  document.addEventListener('zentry:estado', (evento) => {
    estado = evento.detail;
    pintarTarjetas();
    pintarMapa();
    pintarMuro();
  });

  window.ZentryCamaras = { dibujarCapa, abrirMuro, cerrarMuro, get estado() { return estado; } };
})();
