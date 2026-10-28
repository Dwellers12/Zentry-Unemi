/*
 * Conector Zentry para el simulador parqueadero-cctv.html.
 *
 * Lo inyecta camaras_virtuales.py al servir el simulador (el archivo original no
 * se modifica). Cada `intervalo_ms` captura las dos cámaras CCTV con la API
 * window.parqueadero, las convierte a JPEG y las envía al puente junto con el
 * estado del simulador (verdad de terreno). El puente las publica como si fueran
 * cámaras IP: /camaras/CAM_01/shot.jpg, /camaras/CAM_02/shot.jpg.
 */
(() => {
  'use strict';

  const esperar = (ms) => new Promise((r) => setTimeout(r, ms));
  let config = null;
  let ocupado = false;
  let saltos = 0;
  let inicioCaptura = 0;

  // ----------------------------------------------------------------- aviso en pantalla
  const estilo = document.createElement('style');
  estilo.textContent = `
    #zentryConector{display:flex;gap:8px;align-items:flex-start;padding:8px 14px;border-bottom:1px solid var(--bd,#2c3542);
      background:var(--p1,#151a21);color:var(--tx,#dbe2ea);font:12px/1.4 system-ui,sans-serif}
    #zentryConector.flotante{position:fixed;right:10px;bottom:10px;z-index:50;max-width:340px;border:1px solid var(--bd,#2c3542);
      border-radius:8px;background:rgba(13,17,23,.92)}
    #zentryConector .punto{width:8px;height:8px;border-radius:50%;margin-top:4px;flex:0 0 8px;background:#f59e0b}
    #zentryConector.ok .punto{background:#22c55e}
    #zentryConector.error .punto{background:#ef4444}
    #zentryConector b{font-weight:650}
    #zentryConector small{display:block;color:var(--mu,#8d99a8)}`;
  document.head.appendChild(estilo);
  const aviso = document.createElement('div');
  aviso.id = 'zentryConector';
  aviso.innerHTML = '<span class="punto"></span><div><b>Zentry</b> · esperando al simulador…<small></small></div>';
  // Bajo la cabecera del panel lateral (no tapa controles); si el simulador cambia, flota en una esquina.
  const cabecera = document.querySelector('#panel .cab');
  if (cabecera) cabecera.after(aviso);
  else document.body.appendChild(aviso);
  const base = cabecera ? '' : 'flotante';

  function pintar(clase, titulo, detalle) {
    aviso.className = `${base} ${clase}`.trim();
    aviso.querySelector('div').firstChild.nextSibling.textContent = ` · ${titulo}`;
    aviso.querySelector('small').textContent = detalle || '';
  }

  // ----------------------------------------------------------------- captura
  // Mientras un vehículo entra animado no se captura: estado() y capturar() terminan la animación de golpe.
  function hayAnimacion() {
    const lista = document.getElementById('listaVeh');
    return !!lista && lista.textContent.includes('entrando…');
  }

  async function aJpeg(dataUrl) {
    const png = await (await fetch(dataUrl)).blob();
    const mapa = await createImageBitmap(png);
    const lienzo = new OffscreenCanvas(mapa.width, mapa.height);
    lienzo.getContext('2d').drawImage(mapa, 0, 0);
    mapa.close();
    return lienzo.convertToBlob({ type: 'image/jpeg', quality: config.calidad_jpeg });
  }

  async function enviarCuadro() {
    if (ocupado) return;
    if (hayAnimacion() && saltos < 10) { saltos += 1; return; }
    saltos = 0;
    ocupado = true;
    inicioCaptura = Date.now();
    try {
      const sim = window.parqueadero;
      const t = Date.now();
      const estado = sim.estado();
      const formulario = new FormData();
      formulario.append('t', String(t));
      formulario.append('estado', new Blob([JSON.stringify(estado)], { type: 'application/json' }), 'estado.json');
      for (let i = 0; i < estado.camaras.length; i += 1) {
        const png = sim.capturar(i, { ancho: config.ancho, alto: config.alto, overlay: false });
        formulario.append(estado.camaras[i].id, await aJpeg(png), `${estado.camaras[i].id}.jpg`);
      }
      const respuesta = await fetch('/api/cuadro', { method: 'POST', body: formulario });
      const info = await respuesta.json();
      const z = info.zentry || {};
      const camaras = estado.camaras.map((c) => c.id).join(' y ');
      if (z.estado === 'conectado') pintar('ok', 'cámaras conectadas', `${camaras} → ${z.detalle}`);
      else pintar(z.estado === 'error' ? 'error' : '', 'cámaras listas', `${camaras} · ${z.detalle || 'esperando a Zentry…'}`);
    } catch (error) {
      pintar('error', 'sin conexión con el puente', '¿Se cerró camaras_virtuales.py? ' + (error.message || error));
    } finally {
      ocupado = false;
    }
  }

  async function iniciar() {
    while (!(window.__parqueaderoListo && window.parqueadero)) await esperar(300);
    for (;;) {
      try { config = await (await fetch('/api/config')).json(); break; }
      catch { pintar('error', 'sin conexión con el puente', 'Reintentando…'); await esperar(2000); }
    }
    // Un Worker marca el ritmo: los temporizadores de una pestaña en segundo plano se frenan, los del Worker no.
    const codigo = 'let t; onmessage = (e) => { clearInterval(t); t = setInterval(() => postMessage(0), e.data); };';
    const reloj = new Worker(URL.createObjectURL(new Blob([codigo], { type: 'text/javascript' })));
    reloj.onmessage = enviarCuadro;
    reloj.postMessage(config.intervalo_ms);
    enviarCuadro();
    // Si una captura se queda colgada, se libera para no detener el envío.
    setInterval(() => { if (ocupado && Date.now() - inicioCaptura > 30000) ocupado = false; }, 10000);
  }

  iniciar();
})();
