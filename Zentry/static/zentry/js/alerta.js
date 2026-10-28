/*
 * Zentry · detalle de una alerta: cambiar estado (revisar, resolver, reabrir),
 * sugerencias de resolución y evidencia (recorte / imagen completa / reintentar).
 */
(function () {
  'use strict';

  const Z = window.Zentry;
  const ETIQUETAS = { pendiente: 'Pendiente', revisada: 'Revisada', resuelta: 'Resuelta' };

  // ---------------------------------------------------------------- cambio de estado
  document.addEventListener('change', (evento) => {
    const selector = evento.target.closest('[data-cambiar-estado]');
    if (!selector) return;
    const actual = selector.dataset.estadoActual;
    const destino = selector.value;
    selector.value = actual; // no cambia hasta confirmar en el modal
    if (!Z.esAdmin) {
      Z.aviso(Z.MENSAJE_DENEGADO, { error: true });
      return;
    }
    if (destino === actual) return;
    const modal = document.getElementById('modal-estado');
    const reabre = actual === 'resuelta' || destino === 'pendiente';
    const resolver = destino === 'resuelta' && !reabre;
    const poner = (clave, texto) => { modal.querySelector(`[data-texto-${clave}]`).textContent = texto; };
    modal.querySelector('input[name=estado]').value = destino;
    poner('titulo', reabre ? '¿Reabrir alerta?' : resolver ? 'Resolver alerta' : 'Revisar alerta');
    poner('nuevo-estado', reabre ? ` · Nuevo estado: ${ETIQUETAS[destino]}` : '');
    poner('etiqueta', reabre ? 'Motivo (opcional)' : resolver ? '¿Cómo se resolvió?' : 'Nota de revisión (opcional)');
    poner('boton', reabre ? 'Reabrir alerta' : resolver ? 'Marcar como resuelta' : 'Marcar como revisada');
    modal.querySelector('[data-sugerencias]').hidden = !resolver;
    const nota = modal.querySelector('textarea[name=nota]');
    nota.value = '';
    modal.querySelector('[data-contador]').textContent = '0';
    const error = modal.querySelector('[data-error]');
    error.hidden = true;
    Z.abrirModal(modal);
  });

  document.addEventListener('click', (evento) => {
    const sugerencia = evento.target.closest('[data-sugerencia]');
    if (sugerencia) {
      const campo = sugerencia.closest('form').querySelector('textarea');
      campo.value = sugerencia.textContent.trim();
      campo.dispatchEvent(new Event('input', { bubbles: true }));
      campo.focus();
    }
  });

  // Validación en el navegador con los mismos mensajes del servidor.
  document.addEventListener('submit', (evento) => {
    const formulario = evento.target;
    const nota = formulario.querySelector('textarea[name=nota], textarea[name=texto]');
    if (!nota) return;
    const obligatoria = nota.name === 'texto' || formulario.querySelector('input[name=estado]')?.value === 'resuelta';
    const largo = nota.value.trim().length;
    if (obligatoria && (largo < 10 || largo > 300)) {
      evento.preventDefault();
      evento.stopImmediatePropagation();
      const error = formulario.querySelector('[data-error]');
      error.textContent = 'Escribe una descripción de 10 a 300 caracteres';
      error.hidden = false;
    }
  }, true);

  document.addEventListener('submit', (evento) => {
    const placa = evento.target.querySelector('input[name=placa]');
    if (!placa) return;
    if (!/^[A-Z]{3}-\d{3,4}$/.test(placa.value.trim())) {
      evento.preventDefault();
      evento.stopImmediatePropagation();
      const error = evento.target.querySelector('[data-error]');
      error.textContent = 'Formato requerido: ABC-1234';
      error.hidden = false;
    }
  }, true);

  // ---------------------------------------------------------------- evidencia
  document.addEventListener('click', (evento) => {
    const alternar = evento.target.closest('[data-evidencia-alternar]');
    if (alternar) {
      const imagen = document.querySelector('[data-evidencia-imagen]');
      const completa = imagen.getAttribute('src') !== imagen.dataset.completa;
      imagen.src = completa ? imagen.dataset.completa : imagen.dataset.recorte;
      imagen.style.objectFit = completa ? 'contain' : 'cover';
      alternar.querySelector('span').textContent = completa ? 'Ver recorte del vehículo' : 'Ver imagen completa';
      return;
    }
    if (evento.target.closest('[data-evidencia-reintentar]')) {
      const imagen = document.querySelector('[data-evidencia-imagen]');
      document.querySelector('[data-evidencia-error]').hidden = true;
      imagen.hidden = false;
      imagen.src = `${imagen.getAttribute('src').split('?')[0]}?r=${Date.now()}`;
    }
  });

  document.addEventListener('error', (evento) => {
    if (!evento.target.matches || !evento.target.matches('[data-evidencia-imagen]')) return;
    evento.target.hidden = true;
    const error = document.querySelector('[data-evidencia-error]');
    if (error) error.hidden = false;
  }, true);
})();
