/* Lógica de los widgets del formulario de edición de socio.
 * Reutilizado por la página standalone (comprador_editar.html) y por el
 * modal nativo de la lista (#modalEditar en compradores.html).
 *
 * Uso: window.initEditarSocio(root)  // root = elemento contenedor del form
 * Todas las consultas se acotan a `root` para que convivan varios formularios
 * sin pisarse (aunque normalmente hay uno solo a la vez).
 */
(function () {
  function initEditarSocio(root) {
    root = root || document;
    const $  = (sel) => root.querySelector(sel);
    const $$ = (sel) => Array.from(root.querySelectorAll(sel));

    // ── Zona: mostrar input "nueva zona" + heredar vendedor de la zona ──
    const zonaSelect     = $('#zonaSelectEdit');
    const zonaNuevaInput = $('#zonaNuevaEdit');
    const vendedorSelect = $('#vendedorSelectEdit');
    if (zonaSelect && zonaNuevaInput) {
      zonaSelect.addEventListener('change', () => {
        const nueva = zonaSelect.value === '__nueva__';
        zonaNuevaInput.classList.toggle('d-none', !nueva);
        zonaNuevaInput.required = nueva;
        if (nueva) { zonaNuevaInput.value = ''; zonaNuevaInput.focus(); }
        else {
          zonaNuevaInput.value = '';
          const vid = zonaSelect.options[zonaSelect.selectedIndex]?.dataset.vendedorId;
          if (vid && vendedorSelect) vendedorSelect.value = vid;
        }
      });
    }

    // ── Mayúsculas en tiempo real ──
    $$('.input-upper').forEach(el => {
      el.addEventListener('input', function () {
        const p = this.selectionStart;
        this.value = this.value.toUpperCase();
        this.setSelectionRange(p, p);
      });
    });

    // ════════════════ CONTADO (ETAPA 2) ════════════════
    function fmtN(n, nd) { return String(n).padStart(nd || 3, '0'); }

    function fillTaloneras(sel, taloneras) {
      sel.innerHTML = '';
      if (!taloneras.length) {
        const o = document.createElement('option');
        o.value = ''; o.textContent = '— sin taloneras —'; o.disabled = true;
        sel.appendChild(o);
        return;
      }
      for (const t of taloneras) {
        const o = document.createElement('option');
        o.value = String(t.id);
        const cnt = t.numeros_libres.length;
        o.textContent = t.nombre + (cnt ? ` (${cnt} libres)` : ' (sin libres)');
        sel.appendChild(o);
      }
    }

    function refreshNumbers(selTal, selNum, taloneras, currentNe) {
      const tid = parseInt(selTal.value);
      const t = taloneras.find(x => x.id === tid);
      selNum.innerHTML = '';
      const optEmpty = document.createElement('option');
      optEmpty.value = ''; optEmpty.textContent = '— elegí número —';
      selNum.appendChild(optEmpty);
      if (!t) return;
      const nd = t.num_digitos || 3;
      let nums = (t.numeros_libres || []).slice();
      if (currentNe && !nums.includes(currentNe)) nums.push(currentNe);
      nums.sort((a, b) => a - b);
      for (const n of nums) {
        const o = document.createElement('option');
        o.value = String(n);
        o.textContent = fmtN(n, nd);
        if (n === currentNe) o.selected = true;
        selNum.appendChild(o);
      }
    }

    async function initContadoBlock(block) {
      const bid    = block.dataset.boleta;
      const curNe  = block.dataset.currentNe  ? parseInt(block.dataset.currentNe)  : null;
      const curTe  = block.dataset.currentTe  ? parseInt(block.dataset.currentTe)  : null;
      const curNe2 = block.dataset.currentNe2 ? parseInt(block.dataset.currentNe2) : null;
      const curTe2 = block.dataset.currentTe2 ? parseInt(block.dataset.currentTe2) : null;

      const status     = block.querySelector('.contado-status');
      const selectores = block.querySelector('.selectores-contado');
      const slot1      = block.querySelector('.slot-1');
      const slot2      = block.querySelector('.slot-2');
      const sel1Tal    = block.querySelector('.sel-talonera-1');
      const sel1Num    = block.querySelector('.sel-numero-1');
      const sel2Tal    = block.querySelector('.sel-talonera-2');
      const sel2Num    = block.querySelector('.sel-numero-2');

      function applyModal() {
        const mod = block.querySelector('.mod-radio:checked')?.value || 'cuotas';
        if (mod === 'cuotas') {
          selectores.classList.add('d-none');
        } else if (mod === '1pago') {
          selectores.classList.remove('d-none');
          slot1.classList.remove('d-none');
          slot2.classList.remove('d-none');
        } else { // 2pagos
          selectores.classList.remove('d-none');
          slot1.classList.add('d-none');
          slot2.classList.remove('d-none');
        }
      }
      block.querySelectorAll('.mod-radio').forEach(r => r.addEventListener('change', applyModal));
      applyModal();

      status.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Cargando pool del vendedor…';
      let data;
      try {
        const r = await fetch(`/compradores/boleta/${bid}/contado-disponibles`, { credentials: 'include' });
        data = await r.json();
      } catch (e) {
        status.innerHTML = '<i class="bi bi-x-circle text-danger"></i> Error de red cargando pool';
        return;
      }
      if (!data.ok) {
        status.innerHTML = '<i class="bi bi-x-circle text-danger"></i> Error: ' + (data.error || '');
        return;
      }
      if (!data.vendedor_id) {
        status.innerHTML = '<i class="bi bi-info-circle"></i> Esta boleta no tiene vendedor — no puede asignarse CONTADO.';
        return;
      }
      status.innerHTML = `<i class="bi bi-person-fill"></i> Pool de <strong>${data.vendedor_nombre || ''}</strong>`;

      const tCONTADO  = data.taloneras_contado.filter(t => t.rol === 'CONTADO' || t.rol === 'OTRO');
      const tCONTADO2 = data.taloneras_contado.filter(t => t.rol === 'CONTADO_2');

      fillTaloneras(sel1Tal, tCONTADO);
      fillTaloneras(sel2Tal, tCONTADO2);

      if (curTe)  sel1Tal.value = String(curTe);
      if (curTe2) sel2Tal.value = String(curTe2);

      sel1Tal.addEventListener('change', () => refreshNumbers(sel1Tal, sel1Num, tCONTADO,  curNe));
      sel2Tal.addEventListener('change', () => refreshNumbers(sel2Tal, sel2Num, tCONTADO2, curNe2));
      refreshNumbers(sel1Tal, sel1Num, tCONTADO,  curNe);
      refreshNumbers(sel2Tal, sel2Num, tCONTADO2, curNe2);
    }
    $$('.contado-block').forEach(initContadoBlock);

    // ════════════════ REASIGNAR TALONERA / NUMERO ════════════════
    $$('.btn-reasignar').forEach(btn => {
      btn.addEventListener('click', () => {
        const bid = btn.dataset.boletaId;
        const panel = root.querySelector(`.reasignar-panel[data-boleta-id="${bid}"]`);
        if (!panel) return;
        const liberar = root.querySelector(`.liberar-panel[data-boleta-id="${bid}"]`);
        if (liberar) liberar.classList.add('d-none');
        panel.classList.toggle('d-none');
        const status = panel.querySelector('.reasignar-status');
        if (status) status.innerHTML = '';
      });
    });
    $$('.btn-cancelar-reasignar').forEach(btn => {
      btn.addEventListener('click', () => { btn.closest('.reasignar-panel').classList.add('d-none'); });
    });

    // ════════════════ LIBERAR BOLETA (doble seguro) ════════════════
    $$('.btn-liberar').forEach(btn => {
      btn.addEventListener('click', () => {
        const bid = btn.dataset.boletaId;
        const panel = root.querySelector(`.liberar-panel[data-boleta-id="${bid}"]`);
        if (!panel) return;
        const reasignar = root.querySelector(`.reasignar-panel[data-boleta-id="${bid}"]`);
        if (reasignar) reasignar.classList.add('d-none');
        panel.classList.toggle('d-none');
        const inp = panel.querySelector('.inp-liberar-confirmacion');
        const status = panel.querySelector('.liberar-status');
        const btnOk = panel.querySelector('.btn-confirmar-liberar');
        if (inp) { inp.value = ''; }
        if (status) status.innerHTML = '';
        if (btnOk) btnOk.disabled = true;
      });
    });
    $$('.btn-cancelar-liberar').forEach(btn => {
      btn.addEventListener('click', () => { btn.closest('.liberar-panel').classList.add('d-none'); });
    });
    $$('.inp-liberar-confirmacion').forEach(inp => {
      inp.addEventListener('input', () => {
        const panel    = inp.closest('.liberar-panel');
        const esperado = panel.dataset.numero;
        const btnOk    = panel.querySelector('.btn-confirmar-liberar');
        btnOk.disabled = (inp.value.trim() !== esperado);
      });
    });

    // Confirmar liberacion (AJAX)
    $$('.btn-confirmar-liberar').forEach(btn => {
      btn.addEventListener('click', async () => {
        const panel    = btn.closest('.liberar-panel');
        const url      = panel.dataset.url;
        const numero   = panel.dataset.numero;
        const talonera = panel.dataset.talonera;
        const inp      = panel.querySelector('.inp-liberar-confirmacion');
        const status   = panel.querySelector('.liberar-status');

        if (inp.value.trim() !== numero) {
          status.innerHTML = `<i class="bi bi-x-circle text-danger"></i> Tenés que tipear exactamente "${numero}".`;
          return;
        }
        const msg = `¿LIBERAR DEFINITIVAMENTE la boleta ${numero} ${talonera}?\n\n` +
                    `• Se perderán las cuotas pagadas y el historial.\n` +
                    `• La boleta quedará disponible para vender a otro socio.\n` +
                    `• Esta acción NO se puede deshacer.\n\n` +
                    `Solo confirmá si estás 100% seguro.`;
        if (!confirm(msg)) return;

        btn.disabled = true;
        status.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Liberando…';
        try {
          const fd = new FormData();
          fd.append('confirmacion', numero);
          const resp = await fetch(url, { method: 'POST', body: fd, credentials: 'include' });
          const data = await resp.json();
          if (data.ok) {
            status.innerHTML = '<i class="bi bi-check-circle text-success"></i> ¡Listo!';
            // La lista se refresca conservando los filtros (recarga la URL actual).
            setTimeout(() => location.reload(), 500);
          } else {
            status.innerHTML = `<i class="bi bi-x-circle text-danger"></i> ${data.error || 'No se pudo liberar.'}`;
            btn.disabled = false;
          }
        } catch (e) {
          status.innerHTML = `<i class="bi bi-x-circle text-danger"></i> Error de red: ${e}`;
          btn.disabled = false;
        }
      });
    });

    // Confirmar reasignacion (AJAX)
    $$('.btn-confirmar-reasignar').forEach(btn => {
      btn.addEventListener('click', async () => {
        const panel  = btn.closest('.reasignar-panel');
        const url    = panel.dataset.url;
        const selTal = panel.querySelector('.sel-nueva-talonera');
        const inpNum = panel.querySelector('.inp-nuevo-numero');
        const status = panel.querySelector('.reasignar-status');
        const nuevaTalId = parseInt(selTal.value);
        const nuevoNum   = parseInt(inpNum.value);

        if (!nuevaTalId || !nuevoNum) {
          status.innerHTML = '<i class="bi bi-x-circle text-danger"></i> Elegí una talonera y un número válido.';
          return;
        }
        const opt = selTal.options[selTal.selectedIndex];
        const ini = parseInt(opt.dataset.inicio);
        const fin = parseInt(opt.dataset.fin);
        if (ini && fin && (nuevoNum < ini || nuevoNum > fin)) {
          status.innerHTML = `<i class="bi bi-x-circle text-danger"></i> El número debe estar entre ${ini} y ${fin}.`;
          return;
        }
        const talNombre = opt.textContent.trim().split(' ')[0];
        const numFmt    = String(nuevoNum).padStart(4, '0');
        if (!confirm(`¿Confirmás reasignar este socio al número ${numFmt} de ${talNombre}?\n\nEl número viejo quedará libre.`)) return;

        btn.disabled = true;
        status.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Reasignando…';
        try {
          const fd = new FormData();
          fd.append('nueva_talonera_id', nuevaTalId);
          fd.append('nuevo_numero', nuevoNum);
          const resp = await fetch(url, { method: 'POST', body: fd, credentials: 'include' });
          const data = await resp.json();
          if (data.ok) {
            let extra = '';
            if (data.vendedor_nombre) {
              extra = ` Vendedor del número nuevo: <strong>${data.vendedor_nombre}</strong>.`;
            }
            status.innerHTML = `<i class="bi bi-check-circle text-success"></i> ¡Listo!${extra} Recargando…`;
            if (data.aviso) {
              status.innerHTML += `<div class="text-danger mt-1"><i class="bi bi-exclamation-triangle-fill me-1"></i>${data.aviso}</div>`;
              alert(data.aviso);
            }
            setTimeout(() => location.reload(), data.aviso ? 1500 : 500);
          } else {
            status.innerHTML = `<i class="bi bi-x-circle text-danger"></i> ${data.error || 'No se pudo reasignar.'}`;
            btn.disabled = false;
          }
        } catch (e) {
          status.innerHTML = `<i class="bi bi-x-circle text-danger"></i> Error de red: ${e}`;
          btn.disabled = false;
        }
      });
    });
  }

  window.initEditarSocio = initEditarSocio;
})();
