/*
 * Zentry · comportamiento común de la web (sin frameworks).
 *  - Avisos (toasts), confirmaciones, menú móvil, modales y formularios por fetch.
 *  - Campana de notificaciones.
 *  - Consulta periódica del estado del parqueadero (RNF-01) y aviso de lo nuevo.
 * Las páginas con cámaras usan además camaras.js, que escucha el evento "zentry:estado".
 */
(function () {
  'use strict';

  const cuerpo = document.body;
  const MENSAJE_DENEGADO = 'Acción denegada: tu rol solo permite consultar alertas';
  const iconos = window.ZentryIconos || {};

  function icono(nombre, tamano = 18, clase = '') {
    return `<svg class="icono ${clase}" width="${tamano}" height="${tamano}" viewBox="0 0 24 24" fill="none" ` +
      `stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${iconos[nombre] || ''}</svg>`;
  }

  function escapar(texto) {
    return String(texto ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  }

  function csrf() {
    const galleta = document.cookie.split('; ').find((c) => c.startsWith('csrftoken='));
    if (galleta) return decodeURIComponent(galleta.split('=')[1]);
    const campo = document.querySelector('input[name=csrfmiddlewaretoken]');
    return campo ? campo.value : '';
  }

  function urlAlerta(id) {
    return (cuerpo.dataset.urlAlerta || '/alertas/0/').replace('/0/', `/${id}/`);
  }

  function urlImagen(codigo) {
    return (cuerpo.dataset.urlImagen || '').replace('CODIGO', encodeURIComponent(codigo));
  }

  // ------------------------------------------------------------------ avisos (toasts)
  const zonaAvisos = document.getElementById('avisos');

  function cerrarAviso(aviso) {
    if (aviso && aviso.parentNode) aviso.remove();
  }

  function programarCierre(aviso) {
    setTimeout(() => cerrarAviso(aviso), 6500);
  }

  function aviso(texto, opciones = {}) {
    if (!zonaAvisos) return;
    const elemento = document.createElement('div');
    elemento.className = 'aviso' + (opciones.error ? ' error' : '');
    elemento.setAttribute('role', 'status');
    elemento.innerHTML = icono(opciones.error ? 'triangle-alert' : 'check', 20) +
      `<span>${escapar(texto)}</span>` +
      (opciones.alertaId ? `<a href="${urlAlerta(opciones.alertaId)}">Ver alerta</a>` : '') +
      `<button type="button" aria-label="Cerrar aviso">${icono('x', 17)}</button>`;
    elemento.querySelector('button').addEventListener('click', () => cerrarAviso(elemento));
    zonaAvisos.appendChild(elemento);
    while (zonaAvisos.children.length > 3) zonaAvisos.firstElementChild.remove();
    programarCierre(elemento);
  }

  // Avisos que llegan del servidor (mensajes de Django) o que quedaron antes de recargar.
  document.querySelectorAll('[data-autocerrar]').forEach(programarCierre);
  document.querySelectorAll('[data-cerrar-aviso]').forEach((b) => b.addEventListener('click', () => cerrarAviso(b.closest('.aviso'))));
  try {
    const pendiente = sessionStorage.getItem('zentry-aviso');
    if (pendiente) {
      sessionStorage.removeItem('zentry-aviso');
      const datos = JSON.parse(pendiente);
      aviso(datos.texto, datos);
    }
  } catch (e) { /* almacenamiento no disponible */ }

  function avisoTrasRecargar(texto, opciones = {}) {
    try { sessionStorage.setItem('zentry-aviso', JSON.stringify({ texto, ...opciones })); } catch (e) { /* nada */ }
  }

  // ------------------------------------------------------------------ "hace X min"
  function hace(ms) {
    const minutos = Math.max(0, Math.floor((Date.now() - ms) / 60000));
    if (minutos < 1) return 'hace un momento';
    if (minutos < 60) return `hace ${minutos} min`;
    if (minutos < 1440) return `hace ${Math.floor(minutos / 60)} h`;
    const dias = Math.floor(minutos / 1440);
    return `hace ${dias} ${dias === 1 ? 'día' : 'días'}`;
  }

  function actualizarTiempos(raiz = document) {
    raiz.querySelectorAll('[data-hace]').forEach((el) => {
      const ms = Number(el.dataset.hace);
      if (ms) el.textContent = hace(ms);
    });
  }
  setInterval(actualizarTiempos, 30000);

  // ------------------------------------------------------------------ confirmaciones y acciones sencillas
  document.addEventListener('click', (evento) => {
    const confirmar = evento.target.closest('[data-confirmar]');
    if (confirmar && !window.confirm(confirmar.dataset.confirmar)) {
      evento.preventDefault();
      evento.stopImmediatePropagation();
      return;
    }
    const denegado = evento.target.closest('[data-denegado]');
    if (denegado) {
      evento.preventDefault();
      aviso(MENSAJE_DENEGADO, { error: true });
      return;
    }
    const alternar = evento.target.closest('[data-alternar]');
    if (alternar) {
      const destino = document.getElementById(alternar.dataset.alternar);
      if (destino) {
        destino.hidden = !destino.hidden;
        alternar.setAttribute('aria-expanded', String(!destino.hidden));
      }
      return;
    }
    const fila = evento.target.closest('tr[data-href]');
    if (fila && !evento.target.closest('a, button')) {
      window.location.href = fila.dataset.href;
      return;
    }
    const volver = evento.target.closest('[data-volver]');
    if (volver && document.referrer.startsWith(window.location.origin) && window.history.length > 1) {
      evento.preventDefault();
      window.history.back();
    }
  });

  // Placas siempre en mayúsculas mientras se escriben.
  document.addEventListener('input', (evento) => {
    const campo = evento.target;
    if (campo.matches('[data-mayusculas]')) {
      const posicion = campo.selectionStart;
      campo.value = campo.value.toUpperCase();
      try { campo.setSelectionRange(posicion, posicion); } catch (e) { /* nada */ }
    }
    if (campo.matches('[data-contar]')) contar(campo);
  });

  function contar(campo) {
    const contador = campo.closest('form, dialog')?.querySelector('[data-contador]');
    if (contador) contador.textContent = campo.value.length;
  }
  document.querySelectorAll('[data-contar]').forEach(contar);

  // ------------------------------------------------------------------ modales (<dialog>)
  function abrirModal(dialogo) {
    if (!dialogo || dialogo.open) return;
    dialogo.showModal();
    cuerpo.classList.add('sin-scroll');
    const primero = dialogo.querySelector('textarea, input:not([type=hidden]), select');
    if (primero) primero.focus();
  }

  function cerrarModal(dialogo) {
    if (dialogo && dialogo.open) dialogo.close();
  }

  document.addEventListener('close', (evento) => {
    const dialogo = evento.target;
    if (!(dialogo instanceof HTMLDialogElement)) return;
    if (!document.querySelector('dialog[open]')) cuerpo.classList.remove('sin-scroll');
    if (dialogo.dataset.alCerrar) window.location.href = dialogo.dataset.alCerrar;
  }, true);

  document.addEventListener('click', (evento) => {
    const abrir = evento.target.closest('[data-abrir-modal]');
    if (abrir) {
      evento.preventDefault();
      abrirModal(document.getElementById(abrir.dataset.abrirModal));
      return;
    }
    const cerrar = evento.target.closest('[data-cerrar-modal]');
    if (cerrar) {
      cerrarModal(cerrar.closest('dialog'));
      return;
    }
    // Clic en el fondo oscuro: cierra el modal.
    if (evento.target instanceof HTMLDialogElement && evento.target.classList.contains('modal')) {
      const caja = evento.target.getBoundingClientRect();
      const dentro = evento.clientX >= caja.left && evento.clientX <= caja.right &&
        evento.clientY >= caja.top && evento.clientY <= caja.bottom;
      if (!dentro) cerrarModal(evento.target);
    }
  });

  document.querySelectorAll('dialog[data-abrir-al-cargar]').forEach(abrirModal);

  // ------------------------------------------------------------------ formularios por fetch
  async function enviar(url, opciones = {}) {
    const respuesta = await fetch(url, {
      credentials: 'same-origin',
      ...opciones,
      headers: { 'X-CSRFToken': csrf(), 'X-Requested-With': 'fetch', Accept: 'application/json', ...(opciones.headers || {}) },
    });
    let datos = {};
    try { datos = await respuesta.json(); } catch (e) { /* respuesta sin JSON */ }
    if (!respuesta.ok) throw new Error(datos.error || datos.detail || 'No se pudo completar la acción. Inténtalo de nuevo.');
    return datos;
  }

  document.addEventListener('submit', async (evento) => {
    const formulario = evento.target;
    if (!formulario.matches('form[data-ajax]')) return;
    evento.preventDefault();
    const error = formulario.querySelector('[data-error]');
    const boton = formulario.querySelector('[type=submit]');
    if (error) { error.hidden = true; error.textContent = ''; }
    if (boton) boton.disabled = true;
    try {
      const datos = await enviar(formulario.action, { method: 'POST', body: new FormData(formulario) });
      if (formulario.hasAttribute('data-sin-recarga')) {
        formulario.dispatchEvent(new CustomEvent('zentry:enviado', { bubbles: true, detail: datos }));
        consultarEstado();
      } else {
        avisoTrasRecargar(datos.mensaje || 'Cambios guardados');
        window.location.reload();
      }
    } catch (fallo) {
      if (error) { error.textContent = fallo.message; error.hidden = false; } else aviso(fallo.message, { error: true });
    } finally {
      if (boton) boton.disabled = false;
    }
  });

  // ------------------------------------------------------------------ búsqueda y filtros sin recargar
  async function recargarSecciones(url) {
    const respuesta = await fetch(url, { credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch-html' } });
    if (!respuesta.ok) return;
    const nueva = new DOMParser().parseFromString(await respuesta.text(), 'text/html');
    nueva.querySelectorAll('[data-vivo-seccion]').forEach((seccion) => {
      const actual = document.querySelector(`[data-vivo-seccion="${seccion.dataset.vivoSeccion}"]`);
      if (actual) actual.innerHTML = seccion.innerHTML;
    });
    actualizarTiempos();
  }

  let temporizadorBusqueda;
  document.querySelectorAll('[data-buscar-al-escribir]').forEach((campo) => {
    campo.addEventListener('input', () => {
      clearTimeout(temporizadorBusqueda);
      temporizadorBusqueda = setTimeout(async () => {
        const formulario = campo.form;
        const url = new URL(formulario.action || window.location.href, window.location.origin);
        url.search = new URLSearchParams(new FormData(formulario)).toString();
        if (document.querySelector('[data-vivo-seccion]')) {
          window.history.replaceState(null, '', url);
          await recargarSecciones(url);
        } else {
          formulario.submit();
        }
      }, 350);
    });
  });

  document.querySelectorAll('form[data-filtros-auto]').forEach((formulario) => {
    formulario.addEventListener('change', (evento) => {
      const limpiar = evento.target.dataset.limpia;
      if (limpiar && formulario.elements[limpiar]) formulario.elements[limpiar].value = '';
      formulario.requestSubmit();
    });
  });

  // ------------------------------------------------------------------ formulario de vehículo (HU-02)
  // Solo se ofrecen plazas libres o que ya son del residente elegido.
  document.querySelectorAll('form[data-form-vehiculo]').forEach((formulario) => {
    let duenos = {};
    try { duenos = JSON.parse(formulario.dataset.duenos || '{}'); } catch (e) { /* nada */ }
    const residente = formulario.querySelector('select[name$="residente"]');
    const plaza = formulario.querySelector('select[name$="plaza"]');
    if (!residente || !plaza) return;
    const filtrar = () => {
      Array.from(plaza.options).forEach((opcion) => {
        if (!opcion.value) return;
        const dueno = duenos[opcion.value];
        opcion.hidden = Boolean(dueno && residente.value && String(dueno) !== residente.value);
      });
      if (plaza.selectedOptions[0] && plaza.selectedOptions[0].hidden) plaza.value = '';
    };
    residente.addEventListener('change', filtrar);
    filtrar();
  });

  // ------------------------------------------------------------------ campana de notificaciones
  const campana = document.getElementById('campana');
  if (campana) {
    const boton = campana.querySelector('[data-campana]');
    const panel = campana.querySelector('.panel');
    const alternarPanel = (abrir) => {
      panel.hidden = !abrir;
      boton.setAttribute('aria-expanded', String(abrir));
    };
    boton.addEventListener('click', () => alternarPanel(panel.hidden));
    document.addEventListener('mousedown', (evento) => { if (!campana.contains(evento.target)) alternarPanel(false); });
    document.addEventListener('keydown', (evento) => { if (evento.key === 'Escape') alternarPanel(false); });
    campana.addEventListener('zentry:enviado', () => {
      campana.querySelectorAll('.nuevo').forEach((punto) => punto.remove());
      const globo = campana.querySelector('[data-vivo="no-vistas"]');
      globo.hidden = true;
    });
  }

  function pintarCampana(datos) {
    if (!campana) return;
    const globo = campana.querySelector('[data-vivo="no-vistas"]');
    globo.textContent = datos.no_vistas;
    globo.hidden = !datos.no_vistas;
    const lista = campana.querySelector('[data-vivo="campana-lista"]');
    if (!datos.ultimas.length) {
      lista.innerHTML = '<p class="sin-notificaciones">No hay alertas pendientes</p>';
      return;
    }
    lista.innerHTML = datos.ultimas.map((a) => `
      <a class="notificacion" href="${urlAlerta(a.id)}">
        <span class="chip">${icono('bell', 16)}</span>
        <span class="texto"><strong>${escapar(a.plaza)} · ${escapar(a.placa)}</strong>
          <small>${escapar(a.estado)} · <span data-hace="${Date.parse(a.creada_en)}">${hace(Date.parse(a.creada_en))}</span></small></span>
        ${!a.vista && a.estado === 'Pendiente' ? '<span class="nuevo" aria-label="No vista"></span>' : ''}
      </a>`).join('');
  }

  // ------------------------------------------------------------------ estado en vivo
  const urlEstado = cuerpo.dataset.apiEstado;
  const refresco = Number(cuerpo.dataset.refresco) || 3000;
  let ultimaConsulta = null;
  let ultimoEstado = null;
  let consultando = false;

  try {
    const inicial = JSON.parse(document.getElementById('zentry-inicial')?.textContent || 'null');
    if (inicial) { ultimoEstado = inicial; ultimaConsulta = inicial.ahora; }
  } catch (e) { /* nada */ }

  function textoAgente(agente) {
    return agente.en_linea
      ? `Agente conectado · ${agente.camaras_en_linea} de ${agente.camaras_total} cámaras en línea`
      : 'Agente sin señal';
  }

  function aplicarEstado(datos) {
    pintarCampana(datos.campana);
    document.querySelectorAll('[data-vivo="pendientes"]').forEach((el) => { el.textContent = datos.resumen.pendientes; });
    document.querySelectorAll('[data-resumen]').forEach((el) => {
      const valor = datos.resumen[el.dataset.resumen];
      el.textContent = valor === null || valor === undefined ? '—' : valor;
    });
    document.querySelectorAll('[data-vivo="agente"]').forEach((el) => {
      el.textContent = textoAgente(datos.agente);
      el.classList.toggle('desconectado', !datos.agente.en_linea);
    });
    document.dispatchEvent(new CustomEvent('zentry:estado', { detail: datos }));
  }

  async function consultarEstado() {
    if (!urlEstado || consultando) return;
    consultando = true;
    try {
      const url = new URL(urlEstado, window.location.origin);
      if (ultimaConsulta) url.searchParams.set('desde', ultimaConsulta);
      const respuesta = await fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
      if (respuesta.status === 401 || respuesta.status === 403 || respuesta.redirected) {
        window.location.reload(); // la sesión terminó
        return;
      }
      if (!respuesta.ok) return;
      const datos = await respuesta.json();
      const hayNovedades = datos.avisos.length > 0;
      ultimaConsulta = datos.ahora;
      ultimoEstado = datos;
      datos.avisos.forEach((a) => aviso(a.texto, { alertaId: a.alerta_id }));
      if (hayNovedades && campana) {
        campana.classList.remove('sacude');
        void campana.offsetWidth;
        campana.classList.add('sacude');
      }
      aplicarEstado(datos);
      if (hayNovedades && document.querySelector('[data-vivo-seccion]')) await recargarSecciones(window.location.href);
    } catch (e) {
      /* sin conexión: se reintenta en la próxima vuelta */
    } finally {
      consultando = false;
    }
  }

  if (urlEstado && ultimoEstado) {
    // Primer pintado con el estado que vino incrustado en la página.
    window.addEventListener('DOMContentLoaded', () => aplicarEstado(ultimoEstado));
  }
  if (urlEstado) {
    const vuelta = () => {
      consultarEstado();
      setTimeout(vuelta, document.hidden ? Math.max(refresco, 15000) : refresco);
    };
    setTimeout(vuelta, ultimoEstado ? refresco : 0);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) consultarEstado(); });
  }

  window.Zentry = {
    aviso, avisoTrasRecargar, icono, escapar, csrf, enviar, urlAlerta, urlImagen, abrirModal, cerrarModal,
    recargarSecciones, hace, actualizarTiempos, MENSAJE_DENEGADO,
    esAdmin: cuerpo.dataset.esAdmin === '1',
    get estado() { return ultimoEstado; },
    consultarEstado,
  };
})();
