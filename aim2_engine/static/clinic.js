/* clinic.js - all client behavior for the Aim 2 clinic app.
   Loaded once on every page (defer). Each module guards on the elements it needs,
   so it is harmless on pages that don't have them. No external dependencies. */
(function () {
  "use strict";
  function q(n) { return document.querySelector('[name=' + n + ']'); }
  function byId(id) { return document.getElementById(id); }
  function show(id, on) { var e = byId(id); if (e) { e.style.display = on ? '' : 'none'; } }
  function num(s) { var v = parseFloat(s); return isNaN(v) ? null : v; }
  function g(x) { return Math.round(x * 1000) / 1000; }

  // --- active nav tab (accessibility: aria-current) -----------------------
  function navActive() {
    var p = location.pathname;
    var links = document.querySelectorAll('nav.tabs a');
    for (var i = 0; i < links.length; i++) {
      var h = links[i].getAttribute('href') || '';
      if (h === p || (h !== '/' && h.charAt(0) === '/' && p.indexOf(h) === 0)) {
        links[i].classList.add('active');
        links[i].setAttribute('aria-current', 'page');
      }
    }
  }

  // --- scroll progress bar -------------------------------------------------
  function progressBar() {
    var b = byId('progress'); if (!b) return;
    function u() {
      var h = document.documentElement;
      var max = (h.scrollHeight - h.clientHeight) || 1;
      b.style.width = Math.min(100, Math.max(0, (h.scrollTop / max) * 100)) + '%';
    }
    window.addEventListener('scroll', u, { passive: true });
    window.addEventListener('resize', u);
    u();
  }

  // --- live mg dose calculator --------------------------------------------
  function doseCalc() {
    if (!q('amt') || !q('freq') || !q('unit')) return;
    function unitWord(u, n) {
      if (u === 'mg') return 'mg';
      if (u === 'mL') return 'mL';
      return n === 1 ? 'tablet' : 'tablets';
    }
    function up() {
      var amt = num(q('amt').value), freq = parseInt(q('freq').value, 10), unit = q('unit').value;
      var out = byId('mgout'), box = byId('dosebox'), sb = byId('strengthbox'), cb = byId('concbox');
      var au = byId('amtunit');
      if (au) { au.textContent = unit === 'mL' ? '(mL)' : unit === 'mg' ? '(mg)' : '(tablets)'; }
      if (sb) { sb.style.display = (unit === 'tablets') ? '' : 'none'; }
      if (cb) { cb.style.display = (unit === 'mL') ? '' : 'none'; }
      if (box) { box.style.display = ''; }
      // mg path: the family already entered milligrams, so just total it across
      // the day (amount x times/day). No strength lookup needed. Previously this
      // branch HID the box, so the mg unit showed no live total at all.
      if (unit === 'mg') {
        if (!out) { return; }
        if (amt == null || !freq) { out.textContent = 'Fill in the amount and times per day above.'; return; }
        out.textContent = g(amt) + ' mg x ' + freq + ' a day = ' + g(amt * freq) + ' mg total per day';
        return;
      }
      var st = null, note = '';
      if (unit === 'mL') {
        // Liquid: per-medicine concentration dropdown (mg/mL), with the mg-in-mL
        // pair as the 'Other' fallback.
        var csel = q('conc_mgml') ? q('conc_mgml').value : '';
        if (csel && csel !== '__other__') {
          st = num(csel); if (st != null) { note = g(st) + ' mg/mL'; }
        } else {
          var cm = num(q('conc_mg').value), cl = num(q('conc_ml').value);
          if (cm != null && cl) { st = cm / cl; note = g(cm) + ' mg in ' + g(cl) + ' mL'; }
        }
      } else {
        // Tablet: per-medicine strength dropdown, 'Other' reveals a number box.
        var ssel = q('strength') ? q('strength').value : '';
        st = (ssel === '__other__') ? num(q('strength_other') ? q('strength_other').value : '') : num(ssel);
        if (st != null) { note = g(st) + ' mg per tablet'; }
      }
      if (!out) return;
      if (amt == null || !freq) { out.textContent = 'Fill in the amount and times per day above.'; return; }
      if (st != null) {
        var per = amt * st;
        out.textContent = g(amt) + ' ' + unitWord(unit, amt) + ' (' + note + ') = ' + g(per) +
          ' mg per dose, x ' + freq + ' a day = ' + g(per * freq) + ' mg total per day';
        return;
      }
      out.textContent = (unit === 'mL')
        ? 'Choose the liquid strength above to see the dose in mg.'
        : 'Choose the tablet strength above to see the dose in mg.';
    }
    ['amt', 'freq', 'unit', 'strength', 'strength_other', 'conc_mgml', 'conc_mg', 'conc_ml'].forEach(function (n) {
      var e = q(n); if (e) { e.addEventListener('input', up); e.addEventListener('change', up); }
    });
    window.__doseRecalc__ = up;   // let the per-medicine option builder retrigger the mg calc
    up();
  }

  // --- per-medicine strength/concentration options ------------------------
  // Populates the strength & liquid dropdowns from window.__MED_FORMS__ (a map
  // of {med: {tablets:[mg], mgml:[mg/mL]}} injected by the survey page), so the
  // options a family sees are that medicine's REAL formulations. An 'Other /
  // not listed' choice reveals a free-text box so nothing is lost.
  function medForms() {
    var medEl = q('med'), ssel = q('strength'), csel = q('conc_mgml');
    if (!medEl || (!ssel && !csel)) return;
    var MAP = window.__MED_FORMS__ || {};
    function fill(sel, values, otherBoxId, unitLabel) {
      if (!sel) return;
      var list = values || [];
      if (!list.length) { forceOther(sel, otherBoxId); return; }   // no real list -> clean number box
      // prev = the user's explicit pick (data-chosen) or the server prefill
      // (data-selected). Deliberately NOT sel.value: forceOther() parks a stale
      // '__other__' there when the list was empty, which must not carry over.
      var prev = sel.getAttribute('data-chosen') || sel.getAttribute('data-selected') || '';
      sel.style.display = '';
      sel.innerHTML = '';
      var o0 = document.createElement('option'); o0.value = ''; o0.textContent = 'Choose the strength'; sel.appendChild(o0);
      var matched = false;
      list.forEach(function (v) {
        var o = document.createElement('option'); o.value = String(v); o.textContent = g(v) + ' ' + unitLabel;
        sel.appendChild(o);
        if (String(v) === String(prev)) { matched = true; }
      });
      var oo = document.createElement('option'); oo.value = '__other__'; oo.textContent = 'Other / not listed'; sel.appendChild(oo);
      if (prev === '__other__') { sel.value = '__other__'; }
      else if (prev && matched) { sel.value = String(prev); }
      else if (prev) { sel.value = '__other__'; }   // a prior value not in this drug's list
      else { sel.value = ''; }
      show(otherBoxId, sel.value === '__other__');
    }
    // No formulation list for this medicine (unknown/'Other'): hide the empty
    // dropdown and just use the free-text number box - never a 1-item select.
    function forceOther(sel, otherBoxId) {
      if (!sel) return;
      sel.innerHTML = '';
      var o = document.createElement('option'); o.value = '__other__'; sel.appendChild(o);
      sel.value = '__other__';           // so the server reads strength_other
      sel.style.display = 'none';
      show(otherBoxId, true);
    }
    function rebuild() {
      var f = MAP[medEl.value] || null;
      if (ssel) { fill(ssel, f ? f.tablets : [], 'fs-strength-other', 'mg'); }
      if (csel) { fill(csel, f ? f.mgml : [], 'fs-conc-other', 'mg/mL'); }
      if (window.__doseRecalc__) { window.__doseRecalc__(); }
    }
    if (ssel) { ssel.addEventListener('change', function () { ssel.setAttribute('data-chosen', ssel.value); show('fs-strength-other', ssel.value === '__other__'); }); }
    if (csel) { csel.addEventListener('change', function () { csel.setAttribute('data-chosen', csel.value); show('fs-conc-other', csel.value === '__other__'); }); }
    medEl.addEventListener('change', function () {
      // A new medicine invalidates any prior strength/concentration pick.
      if (ssel) { ssel.removeAttribute('data-chosen'); }
      if (csel) { csel.removeAttribute('data-chosen'); }
      rebuild();
    });
    window.__medFormsRebuild__ = rebuild;
    rebuild();
  }

  // --- drug-aware "Amount each time (mg)" ---------------------------------
  // On the mg path, offer that medicine's real strengths as quick-picks (with an
  // 'Other' free-text escape) instead of a bare number box. The visible <select>
  // has no name; it just drives the submitted numeric [name=amt] field, so the
  // server contract is unchanged.
  function amountOptions() {
    var medEl = q('med'), unitEl = q('unit'), sel = byId('amt_mg_sel'), amt = q('amt');
    if (!medEl || !unitEl || !sel || !amt) return;
    var MAP = window.__MED_FORMS__ || {};
    function rebuild() {
      var unit = unitEl.value, f = MAP[medEl.value] || null;
      var vals = (f && f.tablets) ? f.tablets : [];
      if (unit !== 'mg' || !vals.length) {          // other units / unknown drug -> plain number box
        sel.style.display = 'none';
        amt.style.display = '';
        if (window.__doseRecalc__) { window.__doseRecalc__(); }
        return;
      }
      var prev = sel.getAttribute('data-chosen') || amt.getAttribute('data-selected') || '';
      sel.innerHTML = '';
      var o0 = document.createElement('option'); o0.value = ''; o0.textContent = 'Choose the amount'; sel.appendChild(o0);
      var matched = false;
      vals.forEach(function (v) {
        var o = document.createElement('option'); o.value = String(v); o.textContent = g(v) + ' mg'; sel.appendChild(o);
        if (String(v) === String(prev)) { matched = true; }
      });
      var oo = document.createElement('option'); oo.value = '__other__'; oo.textContent = 'Other / not listed'; sel.appendChild(oo);
      if (prev && matched) { sel.value = String(prev); }
      else if (prev) { sel.value = '__other__'; }
      else { sel.value = ''; }
      sel.style.display = '';
      // numeric box only shows for the 'Other' case; otherwise the pick feeds amt
      if (sel.value === '__other__') { amt.style.display = ''; }
      else { amt.style.display = 'none'; if (sel.value) { amt.value = sel.value; } }
      if (window.__doseRecalc__) { window.__doseRecalc__(); }
    }
    sel.addEventListener('change', function () {
      sel.setAttribute('data-chosen', sel.value);
      if (sel.value === '__other__') { amt.style.display = ''; amt.value = ''; try { amt.focus(); } catch (e) {} }
      else { amt.style.display = 'none'; amt.value = (sel.value || ''); }
      if (window.__doseRecalc__) { window.__doseRecalc__(); }
    });
    medEl.addEventListener('change', function () { sel.removeAttribute('data-chosen'); rebuild(); });
    unitEl.addEventListener('change', rebuild);
    window.__amtRebuild__ = rebuild;
    rebuild();
  }

  // --- survey conditional reveals -----------------------------------------
  function surveyReveals() {
    if (!q('seizures') && !q('q1')) return;   // not the survey page
    function up() {
      show('fs-q1change', q('q1') && q('q1').value !== 'same');
      show('fs-medother', q('med') && q('med').value === 'other');
      show('fs-missed', q('adherence') && q('adherence').value !== 'almost_never');
      var missedOther = document.querySelector('[name=q2a][value=other]');
      show('fs-q2aother', missedOther && missedOther.checked);
      var seizYes = q('seizures') && q('seizures').value === 'yes';
      show('fs-seiz', seizYes);
      var types = ['brief_staring', 'sudden_jerks', 'whole_body', 'focal', 'not_sure'];
      for (var t = 0; t < types.length; t++) {
        var cb = document.querySelector('[name=q4][value=' + types[t] + ']');
        show('fs-seizd-' + types[t], seizYes && cb && cb.checked);
      }
      var seYes = q('se') && q('se').value === 'yes';
      show('fs-se', seYes);
      var rash = document.querySelector('[name=q9][value=rash]');
      show('fs-rash', seYes && rash && rash.checked);
      var q9other = document.querySelector('[name=q9][value=other]');
      show('fs-q9other', seYes && q9other && q9other.checked);
      var goal = q('q10a') ? q('q10a').value : '';
      show('fs-atgoal', goal === 'at_goal');
      show('fs-plannedend', goal !== 'at_goal');   // goal-dose target only while working toward it
      var impact2 = q('impact') ? q('impact').value : '';
      // 'medium' = Level 2 or 3 (the option values are medium_tolerable / medium_alot;
      // the old 'medium_big' check matched nothing, so this card never appeared).
      var medium = (impact2 === 'medium_tolerable' || impact2 === 'medium_alot');
      var titrMed = (goal === 'still_increasing' && medium);
      show('fs-pref-adjust', titrMed);
      // Level 3: wait vs uneven vs three-times-a-day - only the options the engine
      // would suggest (data-* from no_seizures_decision_table.json): uneven for the
      // eligible medicines; otherwise TID at steady state or with seizures.
      var dz = byId('fs-dosing'), dosing = false;
      if (dz) {
        var medNow = (q('med') && q('med').value) || dz.getAttribute('data-med') || '';
        var elig = [];
        try { elig = JSON.parse(dz.getAttribute('data-uneven') || '[]'); } catch (e) { elig = []; }
        var uneven = elig.indexOf(medNow.toLowerCase()) >= 0;
        var steady = goal === 'at_goal' && q('q10c_time') && q('q10c_time').value === 'over_2_weeks';
        var tid = !uneven && !!dz.getAttribute('data-tid') && (steady || seizYes);
        var ds = q('q_dosing_choice');
        if (ds) {
          for (var o = 0; o < ds.options.length; o++) {
            var kind = ds.options[o].getAttribute('data-opt');
            var on = !kind || (kind === 'uneven' ? uneven : tid);
            ds.options[o].hidden = !on; ds.options[o].disabled = !on;
            if (!on && ds.options[o].selected) { ds.value = ''; }
          }
        }
        dosing = impact2 === 'medium_alot';
        show('fs-dosing', dosing);
        if (!dosing && ds) { ds.value = ''; }
      }
      show('card-pref', titrMed || dosing);
      // medication-selection card: only when a switch/add is likely - at the goal
      // dose with ongoing seizures, or Level 3+ side effects (Kristen 2026-10-15).
      var lvl3plus = (impact2 === 'medium_alot' || impact2 === 'big' || impact2 === 'not_a_fit');
      var medselLikely = (goal === 'at_goal' && seizYes) || (seYes && lvl3plus);
      show('card-medsel', medselLikely);
      if (!medselLikely && q('medsel_interest')) { q('medsel_interest').value = 'no'; }
      // reveal the detail only if the family opts in
      show('fs-medsel', medselLikely && q('medsel_interest') && q('medsel_interest').value === 'yes');
      // conflict clarifier: better overall + more seizures
      var c = byId('fs-conflict');
      if (c) {
        var bett = q('q11') && q('q11').value === 'better', more = q('q6') && q('q6').value === 'more';
        c.style.display = (bett && more) ? '' : 'none';
      }
    }
    ['q1', 'med', 'adherence', 'seizures', 'se', 'impact', 'q10a', 'q10c_time', 'q11', 'q6', 'medsel_interest'].forEach(function (n) {
      var e = q(n); if (e) { e.addEventListener('change', up); }
    });
    var boxes = document.querySelectorAll('[name=q9],[name=q4],[name=q2a]');
    for (var i = 0; i < boxes.length; i++) { boxes[i].addEventListener('change', up); }
    up();
  }

  // --- save-and-resume (family survey only) -------------------------------
  function autosave() {
    var form = document.querySelector('form');
    if (!form || !q('seizures')) return;     // only the survey
    var KEY = 'asm_survey_draft';
    try {
      var s = JSON.parse(localStorage.getItem(KEY) || '{}');
      Object.keys(s).forEach(function (k) {
        var el = form.elements[k];
        if (el && el.type !== 'hidden' && el.type !== 'file') {
          if (el.type === 'checkbox') el.checked = !!s[k]; else el.value = s[k];
        }
      });
    } catch (e) {}
    // Re-sync per-medicine dropdowns + mg calc after a draft is restored.
    if (window.__medFormsRebuild__) { window.__medFormsRebuild__(); }
    if (window.__amtRebuild__) { window.__amtRebuild__(); }
    form.addEventListener('input', function () {
      var d = {};
      Array.prototype.forEach.call(form.elements, function (el) {
        if (el.name && el.type !== 'hidden' && el.type !== 'password' && el.type !== 'file') {
          d[el.name] = el.type === 'checkbox' ? el.checked : el.value;
        }
      });
      try { localStorage.setItem(KEY, JSON.stringify(d)); } catch (e) {}
    });
    form.addEventListener('submit', function () { try { localStorage.removeItem(KEY); } catch (e) {} });
  }

  // --- Clinical reference sub-tabs ----------------------------------------
  function refTabs() {
    var tabs = document.querySelectorAll('.reftab');
    if (!tabs.length) return;
    function select(name) {
      var i, t = document.querySelectorAll('.reftab'), s = document.querySelectorAll('.refsection');
      for (i = 0; i < t.length; i++) { t[i].classList.toggle('on', t[i].getAttribute('data-tab') === name); }
      for (i = 0; i < s.length; i++) { s[i].classList.toggle('on', s[i].getAttribute('data-tab') === name); }
    }
    for (var i = 0; i < tabs.length; i++) {
      tabs[i].addEventListener('click', function () { select(this.getAttribute('data-tab')); });
    }
    select(tabs[0].getAttribute('data-tab'));   // default to first tab
  }

  // Read the per-medicine formulation map from the CSP-safe JSON data block
  // (<script type="application/json" id="__MED_FORMS_DATA__">). The strict CSP
  // (script-src 'self') blocks inline SCRIPT execution, so the map can't be set
  // by an inline <script> - it is embedded as non-executable JSON and parsed here
  // before any dose dropdown is built. Without this, every medicine's strength/
  // amount dropdown falls back to an empty free-text box.
  function loadMedForms() {
    if (window.__MED_FORMS__ && Object.keys(window.__MED_FORMS__).length) { return; }
    try {
      var el = byId('__MED_FORMS_DATA__');
      window.__MED_FORMS__ = el ? JSON.parse(el.textContent || '{}') : (window.__MED_FORMS__ || {});
    } catch (e) { window.__MED_FORMS__ = window.__MED_FORMS__ || {}; }
  }

  // --- interactive decision map (pan + zoom) ------------------------------
  // Operates on the static SVG (#dm-svg) by transforming its inner <g id=dm-viewport>.
  // All here in the external clinic.js -> allowed by CSP script-src 'self'.
  function decisionMap() {
    var svg = byId('dm-svg'), vp = byId('dm-viewport');
    if (!svg || !vp) return;
    var s = 1, tx = 0, ty = 0;
    function apply() { vp.setAttribute('transform', 'translate(' + tx + ' ' + ty + ') scale(' + s + ')'); }
    function vb() { return svg.viewBox.baseVal; }
    function pt(e) {
      var r = svg.getBoundingClientRect(), b = vb();
      return { x: b.x + (e.clientX - r.left) / r.width * b.width,
               y: b.y + (e.clientY - r.top) / r.height * b.height };
    }
    function zoom(f, cx, cy) {
      var ns = Math.min(6, Math.max(0.3, s * f));
      tx = cx - (ns / s) * (cx - tx);
      ty = cy - (ns / s) * (cy - ty);
      s = ns; apply();
    }
    svg.addEventListener('wheel', function (e) {
      e.preventDefault(); var p = pt(e); zoom(e.deltaY < 0 ? 1.15 : 1 / 1.15, p.x, p.y);
    }, { passive: false });
    var drag = false, lx = 0, ly = 0;
    svg.addEventListener('mousedown', function (e) { drag = true; lx = e.clientX; ly = e.clientY; svg.style.cursor = 'grabbing'; });
    window.addEventListener('mousemove', function (e) {
      if (!drag) return;
      var r = svg.getBoundingClientRect(), b = vb();
      tx += (e.clientX - lx) / r.width * b.width;
      ty += (e.clientY - ly) / r.height * b.height;
      lx = e.clientX; ly = e.clientY; apply();
    });
    window.addEventListener('mouseup', function () { drag = false; svg.style.cursor = 'grab'; });
    var btns = document.querySelectorAll('[data-dm]');
    for (var i = 0; i < btns.length; i++) {
      btns[i].addEventListener('click', (function (act) {
        return function () {
          var b = vb(), cx = b.x + b.width / 2, cy = b.y + b.height / 2;
          if (act === 'in') zoom(1.3, cx, cy);
          else if (act === 'out') zoom(1 / 1.3, cx, cy);
          else { s = 1; tx = 0; ty = 0; apply(); }
        };
      })(btns[i].getAttribute('data-dm')));
    }
    apply();
  }

  // --- CSP-safe confirm dialogs -------------------------------------------
  // Inline onclick="return confirm(...)" is blocked by the strict CSP
  // (script-src 'self'), which silently dropped the confirmation. Buttons now
  // carry data-confirm="message"; this delegated handler runs the dialog.
  function confirmActions() {
    document.addEventListener('click', function (e) {
      var t = e.target.closest ? e.target.closest('[data-confirm]') : null;
      if (t && !window.confirm(t.getAttribute('data-confirm'))) {
        e.preventDefault(); e.stopPropagation();
      }
    }, true);
  }

  function init() {
    loadMedForms();
    navActive(); progressBar(); doseCalc(); medForms(); amountOptions();
    surveyReveals(); autosave(); refTabs(); decisionMap(); confirmActions();
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else { init(); }
})();
