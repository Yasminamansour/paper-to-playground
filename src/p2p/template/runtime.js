/* Paper to Playground runtime. Generic: renders any SPEC, runs compute(state). No network, no libraries. */
(function () {
  'use strict';

  // SVG namespace taken from the HTML parser, so the page holds no URL strings at all.
  var SVGNS = (function () { var d = document.createElement('div'); d.innerHTML = '<svg></svg>'; return d.firstChild.namespaceURI; })();
  var SPEC = {};
  var app = document.getElementById('app');
  var errBox = document.getElementById('error-box');
  var state = {};
  var lastResult = null;
  var DEC = 3;
  var PALETTE = ['--s1', '--s2', '--s3', '--s4'];
  var SEQ = ['#cde2fb', '#9ec5f4', '#6da7ec', '#3987e5', '#256abf', '#184f95', '#0d366b'];
  var DIV_NEG = ['#e34948', '#ef8a87', '#f6c3c1'];
  var DIV_POS = ['#cde2fb', '#86b6ef', '#2a78d6'];

  // ---------- small helpers ----------
  function el(tag, attrs, kids) {
    var e = document.createElement(tag);
    setAttrs(e, attrs);
    addKids(e, kids);
    return e;
  }
  function sv(tag, attrs, kids) {
    var e = document.createElementNS(SVGNS, tag);
    setAttrs(e, attrs);
    addKids(e, kids);
    return e;
  }
  function setAttrs(e, attrs) {
    if (!attrs) return;
    Object.keys(attrs).forEach(function (k) {
      var v = attrs[k];
      if (v === null || v === undefined || v === false) return;
      if (k === 'text') e.textContent = String(v);
      else if (k === 'html') e.innerHTML = String(v); // SPEC strings are sanitized in Python before embedding
      else if (k.slice(0, 2) === 'on' && typeof v === 'function') e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? '' : String(v));
    });
  }
  function addKids(e, kids) {
    if (kids === null || kids === undefined) return;
    if (!Array.isArray(kids)) kids = [kids];
    kids.forEach(function (k) {
      if (k === null || k === undefined || k === false) return;
      e.appendChild(typeof k === 'string' ? document.createTextNode(k) : k);
    });
  }
  function rich(tag, s, attrs) { var a = attrs || {}; a.html = s || ''; return el(tag, a); }
  function plain(s) { var d = document.createElement('div'); d.innerHTML = String(s === undefined || s === null ? '' : s); return d.textContent; }
  function clone(x) { return x === undefined ? undefined : JSON.parse(JSON.stringify(x)); }
  function num(x, d) { var n = Number(x); return isFinite(n) ? n : d; }
  function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#2a78d6'; }
  function arr(x) { return Array.isArray(x) ? x : (x === undefined || x === null ? [] : [x]); }

  function fmtNum(v, dec) {
    if (typeof v !== 'number') v = Number(v);
    if (!isFinite(v)) return 'undefined (see note)';
    var d = dec === undefined ? DEC : dec;
    var s = v.toFixed(d);
    if (d > 0) s = s.replace(/\.?0+$/, '');
    if (s === '-0') s = '0';
    return s;
  }
  function fmt(v, dec) {
    if (v === null || v === undefined) return '—';
    if (typeof v === 'boolean') return v ? 'true' : 'false';
    if (typeof v === 'number') return fmtNum(v, dec);
    if (Array.isArray(v)) {
      if (v.length && Array.isArray(v[0])) return '[' + v.map(function (r) { return fmt(r, dec); }).join(', ') + ']';
      return '[' + v.map(function (x) { return fmt(x, dec); }).join(', ') + ']';
    }
    if (typeof v === 'object') return JSON.stringify(v);
    return String(v);
  }

  // Resolve "outputs.key.0", "state.id", or a bare key (outputs first, then state).
  function get(path, ctx) {
    if (typeof path !== 'string') return path;
    var parts = path.split('.');
    var root;
    if (parts[0] === 'outputs' || parts[0] === 'state') { root = ctx[parts[0]]; parts = parts.slice(1); }
    else root = (ctx.outputs && parts[0] in ctx.outputs) ? ctx.outputs : ctx.state;
    var cur = root;
    for (var i = 0; i < parts.length; i++) {
      if (cur === null || cur === undefined) return undefined;
      cur = cur[parts[i]];
    }
    return cur;
  }
  // "{outputs.H:2} bits" -> formatted text. Bare values stay as-is.
  function interp(s, ctx) {
    if (typeof s !== 'string') return s === undefined ? '' : String(s);
    return plain(s).replace(/\{([A-Za-z0-9_.]+)(?::(\d))?\}/g, function (_, p, d) {
      return fmt(get(p, ctx), d === undefined ? undefined : Number(d));
    });
  }
  // Same, but keeps the (already sanitized) HTML of the SPEC string; only the inserted values are escaped.
  function interpHtml(s, ctx) {
    if (typeof s !== 'string') return '';
    return s.replace(/\{([A-Za-z0-9_.]+)(?::(\d))?\}/g, function (_, p, d) {
      var t = document.createElement('span');
      t.textContent = fmt(get(p, ctx), d === undefined ? undefined : Number(d));
      return t.innerHTML;
    });
  }
  // Numeric field that may be a number, "{path}" or "path".
  function nval(v, ctx, dflt) {
    if (typeof v === 'number') return v;
    if (typeof v === 'string') {
      var m = v.match(/^\{?([A-Za-z0-9_.]+)\}?$/);
      if (m) return num(get(m[1], ctx), dflt);
      return num(v, dflt);
    }
    return dflt;
  }
  function dataOf(v, ctx) { return typeof v === 'string' ? get(v, ctx) : v; }

  function showError(title, detail) {
    errBox.hidden = false;
    errBox.innerHTML = '';
    addKids(errBox, [el('b', { text: title }), ' ', el('span', { text: detail || '' })]);
  }
  function clearError() { errBox.hidden = true; errBox.innerHTML = ''; }

  // ---------- state ----------
  function controls() { return arr(SPEC.controls); }
  function findCtl(id) { return controls().filter(function (c) { return c.id === id; })[0]; }
  function defaultsFor(c) {
    if (c.default !== undefined) return clone(c.default);
    switch (c.kind) {
      case 'toggle': return false;
      case 'select': return c.options && c.options.length ? optVal(c.options[0]) : '';
      case 'matrix': return [[0]];
      case 'vector': case 'prob': return [0];
      default: return num(c.min, 0);
    }
  }
  function optVal(o) { return (o && typeof o === 'object') ? o.value : o; }
  function optLabel(o) { return (o && typeof o === 'object') ? (o.label !== undefined ? o.label : o.value) : o; }
  function resetState() {
    state = {};
    controls().forEach(function (c) { state[c.id] = defaultsFor(c); });
  }
  function applyPreset(p) {
    Object.keys(p || {}).forEach(function (k) {
      var c = findCtl(k);
      if (!c) return;
      var v = clone(p[k]);
      // keep shape-linked matrices consistent
      state[k] = v;
      if (c.kind === 'matrix' && Array.isArray(v)) syncShapes(c, v.length, (v[0] || []).length, true);
      if ((c.kind === 'vector' || c.kind === 'prob') && Array.isArray(v)) syncLen(c, v.length, true);
    });
  }

  // ---------- compute ----------
  function runCompute() {
    var fn = window.compute;
    if (typeof fn !== 'function') {
      showError('The calculation code did not load.', (window.__p2pErrors || []).join(' | ') || 'compute(state) is not defined.');
      return null;
    }
    var res;
    try {
      res = fn(clone(state));
    } catch (e) {
      showError('The calculation failed for these inputs:', String(e && e.message ? e.message : e));
      return null;
    }
    if (!res || typeof res !== 'object') { showError('The calculation returned nothing.', ''); return null; }
    res.outputs = res.outputs && typeof res.outputs === 'object' ? res.outputs : {};
    res.intermediates = arr(res.intermediates);
    res.checks = arr(res.checks);
    clearError();
    return res;
  }

  function update() {
    var res = runCompute();
    lastResult = res;
    var ctx = { outputs: res ? res.outputs : {}, state: state };
    renderVisuals(ctx, !!res);
    renderIntermediates(res);
    renderChecks(res);
  }

  // ---------- controls ----------
  var ctlBox, visBox, interBox, checkBox;

  function buildControls() {
    ctlBox.innerHTML = '';
    controls().forEach(function (c) {
      try { ctlBox.appendChild(buildControl(c)); }
      catch (e) { ctlBox.appendChild(el('p', { class: 'help', text: 'Control "' + c.id + '" could not be shown.' })); }
    });
    var reset = el('button', { class: 'btn', type: 'button', text: 'Reset to defaults', onclick: function () { resetState(); buildControls(); update(); } });
    ctlBox.appendChild(el('div', { class: 'toolbar' }, [reset]));
  }

  function wrap(c, inputId, body) {
    var lab = inputId ? el('label', { for: inputId, html: c.label || c.id }) : el('div', { class: 'lbl', html: c.label || c.id });
    var kids = [lab].concat(body);
    if (c.help) kids.push(rich('div', c.help, { class: 'help' }));
    return el('div', { class: 'ctl', 'data-ctl': c.id }, kids);
  }

  function clamp(v, c) {
    if (c.min !== undefined && v < Number(c.min)) v = Number(c.min);
    if (c.max !== undefined && v > Number(c.max)) v = Number(c.max);
    return v;
  }

  function numInput(id, value, c, onval, label) {
    var inp = el('input', { type: 'number', id: id, value: value, step: c.step !== undefined ? c.step : 'any',
      min: c.min, max: c.max, 'aria-label': label || null, inputmode: 'decimal' });
    inp.addEventListener('input', function () {
      var v = parseFloat(inp.value);
      if (inp.value.trim() === '' || !isFinite(v)) { inp.setAttribute('aria-invalid', 'true'); return; }
      inp.removeAttribute('aria-invalid');
      onval(v);
    });
    inp.addEventListener('change', function () {
      var v = parseFloat(inp.value);
      if (!isFinite(v)) return;
      var cv = clamp(v, c);
      if (cv !== v) { inp.value = cv; onval(cv); }
    });
    return inp;
  }

  function buildControl(c) {
    var id = 'ctl-' + c.id;
    var v = state[c.id];
    switch (c.kind) {
      case 'slider': {
        var out = el('span', { class: 'val', text: fmtNum(v, c.decimals) });
        var r = el('input', { type: 'range', id: id, min: num(c.min, 0), max: num(c.max, 1), step: c.step !== undefined ? c.step : 'any', value: v });
        r.addEventListener('input', function () { state[c.id] = Number(r.value); out.textContent = fmtNum(state[c.id], c.decimals); update(); });
        return wrap(c, id, [el('div', { class: 'row' }, [r, out])]);
      }
      case 'number':
        return wrap(c, id, [numInput(id, v, c, function (x) { state[c.id] = x; update(); })]);
      case 'toggle': {
        var cb = el('input', { type: 'checkbox', id: id });
        cb.checked = !!v;
        cb.addEventListener('change', function () { state[c.id] = cb.checked; update(); });
        var lab = el('label', { for: id, html: c.label || c.id });
        var kids = [el('div', { class: 'row' }, [cb, lab])];
        if (c.help) kids.push(rich('div', c.help, { class: 'help' }));
        return el('div', { class: 'ctl', 'data-ctl': c.id }, kids);
      }
      case 'select': {
        var s = el('select', { id: id });
        arr(c.options).forEach(function (o) {
          var opt = el('option', { value: String(optVal(o)), text: plain(optLabel(o)) });
          if (String(optVal(o)) === String(v)) opt.selected = true;
          s.appendChild(opt);
        });
        s.addEventListener('change', function () {
          var o = arr(c.options).filter(function (x) { return String(optVal(x)) === s.value; })[0];
          state[c.id] = o === undefined ? s.value : optVal(o);
          update();
        });
        return wrap(c, id, [s]);
      }
      case 'matrix': return buildMatrix(c);
      case 'vector': case 'prob': return buildVector(c);
      default:
        return wrap(c, null, [el('p', { class: 'help', text: 'Unknown control type: ' + c.kind })]);
    }
  }

  function labelAt(list, i, prefix) {
    var l = arr(list)[i];
    return l !== undefined ? plain(l) : prefix + (i + 1);
  }

  function buildMatrix(c) {
    var m = state[c.id];
    if (!Array.isArray(m) || !m.length) m = state[c.id] = [[0]];
    var rows = m.length, cols = (m[0] || []).length;
    var tbl = el('table', { class: 'grid-in' });
    var head = el('tr', null, [el('th')]);
    for (var j = 0; j < cols; j++) head.appendChild(el('th', { scope: 'col', text: labelAt(c.col_labels, j, 'c') }));
    tbl.appendChild(head);
    for (var i = 0; i < rows; i++) {
      var tr = el('tr', null, [el('th', { scope: 'row', text: labelAt(c.row_labels, i, 'r') })]);
      for (var k = 0; k < cols; k++) {
        (function (i, k) {
          var lbl = plain(c.label || c.id) + ' row ' + labelAt(c.row_labels, i, '') + ' column ' + labelAt(c.col_labels, k, '');
          tr.appendChild(el('td', null, [numInput('ctl-' + c.id + '-' + i + '-' + k, num(m[i][k], 0), c, function (x) { state[c.id][i][k] = x; update(); }, lbl)]));
        })(i, k);
      }
      tbl.appendChild(tr);
    }
    var kids = [el('div', { class: 'scroll' }, [tbl])];
    var tb = [];
    var minR = num(c.min_rows, rows), maxR = num(c.max_rows, rows), minC = num(c.min_cols, cols), maxC = num(c.max_cols, cols);
    if (maxR > minR) {
      tb.push(el('button', { class: 'btn', type: 'button', text: '+ row', disabled: rows >= maxR, onclick: function () { syncShapes(c, rows + 1, cols); rerenderShapes(); } }));
      tb.push(el('button', { class: 'btn', type: 'button', text: '− row', disabled: rows <= minR, onclick: function () { syncShapes(c, rows - 1, cols); rerenderShapes(); } }));
    }
    if (maxC > minC) {
      tb.push(el('button', { class: 'btn', type: 'button', text: '+ column', disabled: cols >= maxC, onclick: function () { syncShapes(c, rows, cols + 1); rerenderShapes(); } }));
      tb.push(el('button', { class: 'btn', type: 'button', text: '− column', disabled: cols <= minC, onclick: function () { syncShapes(c, rows, cols - 1); rerenderShapes(); } }));
    }
    if (tb.length) kids.push(el('div', { class: 'toolbar', style: 'margin-top:6px' }, tb));
    return wrap(c, null, kids);
  }

  function resize2(m, r, k, fill) {
    var out = [];
    for (var i = 0; i < r; i++) {
      var row = [];
      for (var j = 0; j < k; j++) row.push(m[i] && m[i][j] !== undefined ? m[i][j] : fill);
      out.push(row);
    }
    return out;
  }
  function resize1(v, n, fill) {
    var out = [];
    for (var i = 0; i < n; i++) out.push(v[i] !== undefined ? v[i] : fill);
    return out;
  }
  // Matrices that share a row/column group (share_rows / share_cols) keep the same size.
  function syncShapes(c, r, k, skipSelf) {
    var cur = state[c.id] || [[0]];
    var oldR = cur.length, oldC = (cur[0] || []).length;
    if (!skipSelf) state[c.id] = resize2(cur, r, k, num(c.fill, 0));
    controls().forEach(function (o) {
      if (o.id === c.id) return;
      if (o.kind === 'matrix') {
        var m = state[o.id] || [[0]];
        var nr = m.length, nc = (m[0] || []).length;
        if (c.share_rows && o.share_rows === c.share_rows && r !== oldR) nr = r;
        if (c.share_rows && o.share_cols === c.share_rows && r !== oldR) nc = r;
        if (c.share_cols && o.share_cols === c.share_cols && k !== oldC) nc = k;
        if (c.share_cols && o.share_rows === c.share_cols && k !== oldC) nr = k;
        if (nr !== m.length || nc !== (m[0] || []).length) state[o.id] = resize2(m, nr, nc, num(o.fill, 0));
      } else if ((o.kind === 'vector' || o.kind === 'prob') && o.share_len) {
        if (o.share_len === c.share_rows && r !== oldR) state[o.id] = resize1(state[o.id] || [], r, num(o.fill, 0));
        if (o.share_len === c.share_cols && k !== oldC) state[o.id] = resize1(state[o.id] || [], k, num(o.fill, 0));
      }
    });
  }
  function syncLen(c, n, skipSelf) {
    var cur = state[c.id] || [];
    if (!skipSelf) state[c.id] = resize1(cur, n, c.kind === 'prob' ? 0 : num(c.fill, 0));
    if (!c.share_len) return;
    controls().forEach(function (o) {
      if (o.id === c.id) return;
      if ((o.kind === 'vector' || o.kind === 'prob') && o.share_len === c.share_len) state[o.id] = resize1(state[o.id] || [], n, num(o.fill, 0));
      if (o.kind === 'matrix') {
        var m = state[o.id] || [[0]];
        if (o.share_rows === c.share_len) state[o.id] = resize2(m, n, (m[0] || []).length, num(o.fill, 0));
        if (o.share_cols === c.share_len) state[o.id] = resize2(state[o.id], state[o.id].length, n, num(o.fill, 0));
      }
    });
  }
  function rerenderShapes() {
    var active = document.activeElement && document.activeElement.id;
    buildControls();
    update();
    if (active && document.getElementById(active)) document.getElementById(active).focus();
  }

  function buildVector(c) {
    var v = state[c.id];
    if (!Array.isArray(v)) v = state[c.id] = [num(v, 0)];
    var isProb = c.kind === 'prob';
    var cc = isProb ? { min: c.min !== undefined ? c.min : 0, max: c.max !== undefined ? c.max : 1, step: c.step !== undefined ? c.step : 0.01 } : c;
    var tbl = el('table', { class: 'grid-in' });
    var head = el('tr'), body = el('tr');
    v.forEach(function (x, i) {
      var name = labelAt(c.labels, i, isProb ? 'p' : 'x');
      head.appendChild(el('th', { scope: 'col', text: name }));
      body.appendChild(el('td', null, [numInput('ctl-' + c.id + '-' + i, num(x, 0), cc, function (val) {
        state[c.id][i] = val; if (isProb) showSum(); update();
      }, plain(c.label || c.id) + ' ' + name)]));
    });
    tbl.appendChild(head); tbl.appendChild(body);
    var kids = [el('div', { class: 'scroll' }, [tbl])];
    var tb = [];
    var sumEl = null;
    function showSum() {
      if (!sumEl) return;
      var s = state[c.id].reduce(function (a, b) { return a + num(b, 0); }, 0);
      sumEl.textContent = 'Sum = ' + fmtNum(s, 3) + (Math.abs(s - 1) > 1e-9 ? '  (should be 1)' : '');
      sumEl.className = 'sum' + (Math.abs(s - 1) > 1e-9 ? ' off' : '');
    }
    if (isProb) {
      sumEl = el('span', { class: 'sum', 'aria-live': 'polite' });
      tb.push(el('button', { class: 'btn', type: 'button', text: 'Normalize', onclick: function () {
        var s = state[c.id].reduce(function (a, b) { return a + Math.max(0, num(b, 0)); }, 0);
        var n = state[c.id].length;
        state[c.id] = state[c.id].map(function (x) { return s > 0 ? Math.max(0, num(x, 0)) / s : 1 / n; });
        rerenderShapes();
      } }));
    }
    var minL = num(c.min_len, v.length), maxL = num(c.max_len, v.length);
    if (maxL > minL) {
      tb.push(el('button', { class: 'btn', type: 'button', text: '+ entry', disabled: v.length >= maxL, onclick: function () { syncLen(c, v.length + 1); rerenderShapes(); } }));
      tb.push(el('button', { class: 'btn', type: 'button', text: '− entry', disabled: v.length <= minL, onclick: function () { syncLen(c, v.length - 1); rerenderShapes(); } }));
    }
    if (tb.length || sumEl) kids.push(el('div', { class: 'row', style: 'margin-top:6px' }, tb.concat(sumEl ? [sumEl] : [])));
    var w = wrap(c, null, kids);
    showSum();
    return w;
  }

  // ---------- visuals ----------
  function niceTicks(lo, hi, n) {
    if (!isFinite(lo) || !isFinite(hi)) return [0, 1];
    if (lo === hi) { lo -= 1; hi += 1; }
    var span = hi - lo, step = Math.pow(10, Math.floor(Math.log10(span / n)));
    var err = (n * step) / span;
    if (err <= 0.15) step *= 10; else if (err <= 0.35) step *= 5; else if (err <= 0.75) step *= 2;
    var start = Math.floor(lo / step) * step, out = [];
    for (var t = start; t <= hi + step * 0.5; t += step) out.push(Math.abs(t) < step * 1e-9 ? 0 : t);
    return out;
  }
  function tickFmt(t) { var a = Math.abs(t); return a !== 0 && (a < 1e-3 || a >= 1e5) ? t.toExponential(1) : String(+t.toPrecision(6)); }
  function finiteOnly(xs) { return xs.filter(function (x) { return typeof x === 'number' && isFinite(x); }); }

  function legend(series) {
    if (series.length < 2) return null;
    return el('div', { class: 'legend' }, series.map(function (s, i) {
      return el('span', null, [el('i', { style: 'background:' + cssVar(PALETTE[i % PALETTE.length]) }), plain(s.name || ('series ' + (i + 1)))]);
    }));
  }

  function axesFrame(g, W, H, M, yt, ys, xlab, ylab) {
    yt.forEach(function (t) {
      var y = ys(t);
      g.appendChild(sv('line', { class: 'grid', x1: M.l, x2: W - M.r, y1: y, y2: y }));
      g.appendChild(sv('text', { x: M.l - 6, y: y + 4, 'text-anchor': 'end', text: tickFmt(t) }));
    });
    g.appendChild(sv('line', { class: 'axis', x1: M.l, x2: M.l, y1: M.t, y2: H - M.b }));
    if (ylab) g.appendChild(sv('text', { x: 14, y: M.t + (H - M.t - M.b) / 2, 'text-anchor': 'middle', transform: 'rotate(-90 14 ' + (M.t + (H - M.t - M.b) / 2) + ')', text: plain(ylab) }));
    if (xlab) g.appendChild(sv('text', { x: M.l + (W - M.l - M.r) / 2, y: H - 6, 'text-anchor': 'middle', text: plain(xlab) }));
  }

  function seriesList(v, ctx) {
    if (Array.isArray(v.series) && v.series.length) {
      return v.series.map(function (s) { return { name: s.name, data: arr(dataOf(s.data, ctx)).map(Number) }; });
    }
    return [{ name: v.name || '', data: arr(dataOf(v.data, ctx)).map(Number) }];
  }

  function drawBar(v, ctx) {
    var series = seriesList(v, ctx);
    var n = Math.max.apply(null, series.map(function (s) { return s.data.length; }).concat([0]));
    var labels = arr(dataOf(v.labels, ctx));
    var W = 560, H = num(v.height, 280), M = { l: 54, r: 12, t: 16, b: v.x_label ? 48 : 34 };
    var all = finiteOnly([].concat.apply([], series.map(function (s) { return s.data; })));
    var lo = Math.min(0, v.y_min !== undefined ? Number(v.y_min) : Math.min.apply(null, all.concat([0])));
    var hi = Math.max(0, v.y_max !== undefined ? Number(v.y_max) : Math.max.apply(null, all.concat([0])));
    if (hi === lo) hi = lo + 1;
    var yt = niceTicks(lo, hi, 5); lo = Math.min(lo, yt[0]); hi = Math.max(hi, yt[yt.length - 1]);
    var ys = function (y) { return M.t + (H - M.t - M.b) * (1 - (y - lo) / (hi - lo)); };
    var svg = sv('svg', { class: 'chart', viewBox: '0 0 ' + W + ' ' + H, role: 'img', 'aria-label': plain(v.title || 'bar chart') });
    axesFrame(svg, W, H, M, yt, ys, v.x_label, v.y_label);
    var bandW = (W - M.l - M.r) / Math.max(n, 1), gap = 2;
    var bw = Math.max(2, (bandW * 0.72 - gap * (series.length - 1)) / series.length);
    var showVals = v.value_labels !== undefined ? !!v.value_labels : n * series.length <= 10;
    var hl = v.highlight !== undefined ? nval(v.highlight, ctx, -1) : -1;
    for (var i = 0; i < n; i++) {
      var x0 = M.l + i * bandW + bandW * 0.14;
      series.forEach(function (s, si) {
        var val = s.data[i];
        var x = x0 + si * (bw + gap);
        var color = cssVar(PALETTE[si % PALETTE.length]);
        if (typeof val !== 'number' || !isFinite(val)) {
          svg.appendChild(sv('text', { x: x + bw / 2, y: ys(0) - 4, 'text-anchor': 'middle', class: 'val', text: 'n/a' }));
          return;
        }
        var y1 = ys(Math.max(0, val)), y2 = ys(Math.min(0, val));
        var rect = sv('rect', { x: x, y: y1, width: bw, height: Math.max(1, y2 - y1), rx: 3, fill: color, opacity: hl >= 0 && hl !== i ? 0.45 : 1 });
        rect.appendChild(sv('title', { text: (labels[i] !== undefined ? plain(labels[i]) + ': ' : '') + (s.name ? plain(s.name) + ' ' : '') + fmtNum(val, v.decimals) }));
        svg.appendChild(rect);
        if (showVals) svg.appendChild(sv('text', { class: 'val', x: x + bw / 2, y: val >= 0 ? y1 - 4 : y2 + 13, 'text-anchor': 'middle', text: fmtNum(val, v.decimals !== undefined ? v.decimals : 2) }));
      });
      svg.appendChild(sv('text', { x: M.l + i * bandW + bandW / 2, y: H - M.b + 16, 'text-anchor': 'middle', text: labels[i] !== undefined ? plain(labels[i]) : String(i + 1) }));
    }
    svg.appendChild(sv('line', { class: 'axis', x1: M.l, x2: W - M.r, y1: ys(0), y2: ys(0) }));
    return [legend(series), svg];
  }

  function drawLine(v, ctx) {
    var series = seriesList(v, ctx);
    var n = Math.max.apply(null, series.map(function (s) { return s.data.length; }).concat([0]));
    var xs = arr(dataOf(v.x, ctx)).map(Number);
    if (xs.length < n) { xs = []; for (var q = 0; q < n; q++) xs.push(q); }
    var W = 560, H = num(v.height, 280), M = { l: 54, r: 14, t: 16, b: v.x_label ? 48 : 34 };
    var pts = arr(v.points).map(function (p) { return { x: nval(p.x, ctx, NaN), y: nval(p.y, ctx, NaN), label: p.label }; });
    var vls = arr(v.vlines).map(function (p) { return { x: nval(p.x, ctx, NaN), label: p.label }; });
    var ally = finiteOnly([].concat.apply([], series.map(function (s) { return s.data; })).concat(pts.map(function (p) { return p.y; })));
    var allx = finiteOnly(xs.concat(pts.map(function (p) { return p.x; })).concat(vls.map(function (p) { return p.x; })));
    var ylo = v.y_min !== undefined ? Number(v.y_min) : Math.min.apply(null, ally.concat([0]));
    var yhi = v.y_max !== undefined ? Number(v.y_max) : Math.max.apply(null, ally.concat([1]));
    var xlo = v.x_min !== undefined ? Number(v.x_min) : Math.min.apply(null, allx.concat([0]));
    var xhi = v.x_max !== undefined ? Number(v.x_max) : Math.max.apply(null, allx.concat([1]));
    if (xhi === xlo) xhi = xlo + 1;
    var yt = niceTicks(ylo, yhi, 5); ylo = Math.min(ylo, yt[0]); yhi = Math.max(yhi, yt[yt.length - 1]);
    if (yhi === ylo) yhi = ylo + 1;
    var xt = niceTicks(xlo, xhi, 6);
    var X = function (x) { return M.l + (W - M.l - M.r) * (x - xlo) / (xhi - xlo); };
    var Y = function (y) { return M.t + (H - M.t - M.b) * (1 - (y - ylo) / (yhi - ylo)); };
    var svg = sv('svg', { class: 'chart', viewBox: '0 0 ' + W + ' ' + H, role: 'img', 'aria-label': plain(v.title || 'line chart') });
    axesFrame(svg, W, H, M, yt, Y, v.x_label, v.y_label);
    xt.forEach(function (t) { if (t >= xlo - 1e-12 && t <= xhi + 1e-12) svg.appendChild(sv('text', { x: X(t), y: H - M.b + 16, 'text-anchor': 'middle', text: tickFmt(t) })); });
    svg.appendChild(sv('line', { class: 'axis', x1: M.l, x2: W - M.r, y1: H - M.b, y2: H - M.b }));
    if (ylo < 0 && yhi > 0) svg.appendChild(sv('line', { class: 'axis', x1: M.l, x2: W - M.r, y1: Y(0), y2: Y(0), 'stroke-dasharray': '3 3' }));
    vls.forEach(function (p) {
      if (!isFinite(p.x)) return;
      svg.appendChild(sv('line', { x1: X(p.x), x2: X(p.x), y1: M.t, y2: H - M.b, stroke: cssVar('--ink-3'), 'stroke-dasharray': '4 3' }));
      if (p.label) svg.appendChild(sv('text', { x: X(p.x) + 4, y: M.t + 10, text: interp(p.label, ctx) }));
    });
    series.forEach(function (s, si) {
      var d = '', pen = false;
      for (var i = 0; i < s.data.length; i++) {
        var y = s.data[i];
        if (typeof y !== 'number' || !isFinite(y) || !isFinite(xs[i])) { pen = false; continue; }
        d += (pen ? 'L' : 'M') + X(xs[i]).toFixed(1) + ' ' + Y(y).toFixed(1) + ' ';
        pen = true;
      }
      var color = cssVar(PALETTE[si % PALETTE.length]);
      svg.appendChild(sv('path', { d: d, fill: 'none', stroke: color, 'stroke-width': 2, 'stroke-linejoin': 'round' }));
      if (s.data.length <= 25) s.data.forEach(function (y, i) {
        if (!isFinite(y) || !isFinite(xs[i])) return;
        var c = sv('circle', { cx: X(xs[i]), cy: Y(y), r: 4, fill: color, stroke: cssVar('--panel'), 'stroke-width': 2 });
        c.appendChild(sv('title', { text: (s.name ? plain(s.name) + ': ' : '') + '(' + fmtNum(xs[i]) + ', ' + fmtNum(y) + ')' }));
        svg.appendChild(c);
      });
    });
    pts.forEach(function (p) {
      if (!isFinite(p.x) || !isFinite(p.y)) return;
      var c = sv('circle', { cx: X(p.x), cy: Y(p.y), r: 6, fill: cssVar('--s2'), stroke: cssVar('--panel'), 'stroke-width': 2 });
      c.appendChild(sv('title', { text: '(' + fmtNum(p.x) + ', ' + fmtNum(p.y) + ')' }));
      svg.appendChild(c);
      if (p.label) svg.appendChild(sv('text', { class: 'val', x: X(p.x) + 9, y: Y(p.y) - 8, text: interp(p.label, ctx) }));
    });
    return [legend(series), svg];
  }

  function cellColor(val, lo, hi) {
    if (!isFinite(val)) return { bg: cssVar('--grid'), fg: cssVar('--ink') };
    var c, t;
    if (lo < 0 && hi > 0) {
      var m = Math.max(-lo, hi);
      t = Math.min(1, Math.abs(val) / m);
      if (t < 0.12) return { bg: cssVar('--div-mid'), fg: cssVar('--ink') };
      var ramp = val < 0 ? DIV_NEG : DIV_POS;
      c = val < 0 ? ramp[Math.min(2, Math.floor((1 - t) * 3))] : ramp[Math.min(2, Math.floor(t * 3))];
      return { bg: c, fg: t > 0.66 ? '#ffffff' : '#0b0b0b' };
    }
    t = hi === lo ? 0.5 : (val - lo) / (hi - lo);
    var idx = Math.max(0, Math.min(SEQ.length - 1, Math.round(t * (SEQ.length - 1))));
    return { bg: SEQ[idx], fg: idx >= 3 ? '#ffffff' : '#0b0b0b' };
  }

  function drawHeatmap(v, ctx, colored) {
    var m = dataOf(v.data, ctx);
    if (!Array.isArray(m)) m = [];
    if (m.length && !Array.isArray(m[0])) m = [m];
    var rl = arr(dataOf(v.row_labels, ctx)), cl = arr(dataOf(v.col_labels, ctx));
    var vals = finiteOnly([].concat.apply([], m.map(function (r) { return arr(r).map(Number); })));
    var lo = v.min !== undefined ? Number(v.min) : Math.min.apply(null, vals.concat([0]));
    var hi = v.max !== undefined ? Number(v.max) : Math.max.apply(null, vals.concat([1]));
    var tbl = el('table', { class: 'mtable' });
    var cols = Math.max.apply(null, m.map(function (r) { return arr(r).length; }).concat([0]));
    var head = el('tr', null, [el('th', { text: v.corner ? plain(v.corner) : '' })]);
    for (var j = 0; j < cols; j++) head.appendChild(el('th', { scope: 'col', text: cl[j] !== undefined ? plain(cl[j]) : String(j + 1) }));
    tbl.appendChild(head);
    m.forEach(function (r, i) {
      var tr = el('tr', null, [el('th', { scope: 'row', text: rl[i] !== undefined ? plain(rl[i]) : String(i + 1) })]);
      arr(r).forEach(function (x) {
        var val = Number(x);
        var td = el('td', { text: fmtNum(val, v.decimals !== undefined ? v.decimals : 2) });
        if (colored) { var cc = cellColor(val, lo, hi); td.style.background = cc.bg; td.style.color = cc.fg; }
        tr.appendChild(td);
      });
      tbl.appendChild(tr);
    });
    var kids = [el('div', { class: 'scroll' }, [tbl])];
    if (colored) kids.push(el('div', { class: 'legend' }, [el('span', { text: (lo < 0 && hi > 0) ? 'Color: red = negative, gray ≈ 0, blue = positive' : 'Color: darker = larger value (' + fmtNum(lo, 2) + ' to ' + fmtNum(hi, 2) + ')' })]));
    return kids;
  }

  var TONES = { accent: '--s1', accent2: '--s2', accent3: '--s3', accent4: '--s4', muted: '--ink-3', ink: '--ink', good: '--good', bad: '--bad' };
  function drawSvg(v, ctx) {
    var W = num(v.width, 560), H = num(v.height, 260);
    var svg = sv('svg', { class: 'chart', viewBox: '0 0 ' + W + ' ' + H, role: 'img', 'aria-label': plain(v.title || 'diagram') });
    var defs = sv('defs');
    var mk = sv('marker', { id: 'arrowhead-' + Math.random().toString(36).slice(2, 8), viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse' });
    mk.appendChild(sv('path', { d: 'M0 0 L10 5 L0 10 z', fill: cssVar('--ink-2') }));
    defs.appendChild(mk); svg.appendChild(defs);
    var items = arr(v.items).concat(arr(dataOf(v.items_from, ctx)));
    items.forEach(function (it) {
      if (!it || typeof it !== 'object') return;
      var tone = cssVar(TONES[it.tone] || '--s1');
      var g = sv('g');
      var x = nval(it.x, ctx, 0), y = nval(it.y, ctx, 0);
      var label = it.text !== undefined ? interp(it.text, ctx) : '';
      switch (it.type) {
        case 'box': {
          var w = Math.max(0, nval(it.w, ctx, 80)), h = Math.max(0, nval(it.h, ctx, 36));
          g.appendChild(sv('rect', { x: x, y: y, width: w, height: h, rx: 6, fill: it.fill === false ? 'none' : tone, 'fill-opacity': it.fill === false ? 0 : num(it.opacity, 0.18), stroke: tone, 'stroke-width': 1.5 }));
          if (label) g.appendChild(sv('text', { class: 'val', x: x + w / 2, y: y + h / 2 + 4, 'text-anchor': 'middle', text: label }));
          break;
        }
        case 'circle': {
          var r = Math.max(0, nval(it.r, ctx, 16));
          g.appendChild(sv('circle', { cx: x, cy: y, r: r, fill: tone, 'fill-opacity': num(it.opacity, 0.25), stroke: tone, 'stroke-width': 1.5 }));
          if (label) g.appendChild(sv('text', { class: 'val', x: x, y: y + 4, 'text-anchor': 'middle', text: label }));
          break;
        }
        case 'arrow': case 'line': {
          var x2 = nval(it.x2, ctx, x), y2 = nval(it.y2, ctx, y);
          var sw = Math.max(0.5, Math.min(12, nval(it.width, ctx, 1.5)));
          var ln = sv('line', { x1: x, y1: y, x2: x2, y2: y2, stroke: it.tone ? tone : cssVar('--ink-2'), 'stroke-width': sw });
          if (it.type === 'arrow') ln.setAttribute('marker-end', 'url(#' + mk.id + ')');
          g.appendChild(ln);
          if (label) g.appendChild(sv('text', { class: 'val', x: (x + x2) / 2, y: (y + y2) / 2 - 6, 'text-anchor': 'middle', text: label }));
          break;
        }
        case 'text':
          g.appendChild(sv('text', { class: it.bold ? 'ttl' : 'val', x: x, y: y, 'text-anchor': it.anchor || 'start', 'font-size': it.size || null, text: label }));
          break;
        default: return;
      }
      if (it.tip) g.appendChild(sv('title', { text: interp(it.tip, ctx) }));
      svg.appendChild(g);
    });
    return [svg];
  }

  function renderVisuals(ctx, ok) {
    visBox.innerHTML = '';
    arr(SPEC.visuals).forEach(function (v, i) {
      var kids = [];
      try {
        if (!ok) kids = [el('p', { class: 'help', text: 'The chart will appear when the calculation succeeds.' })];
        else if (v.kind === 'bar') kids = drawBar(v, ctx);
        else if (v.kind === 'line') kids = drawLine(v, ctx);
        else if (v.kind === 'heatmap') kids = drawHeatmap(v, ctx, true);
        else if (v.kind === 'matrix') kids = drawHeatmap(v, ctx, false);
        else if (v.kind === 'svg') kids = drawSvg(v, ctx);
        else kids = [el('p', { class: 'help', text: 'Unknown visual type: ' + v.kind })];
      } catch (e) {
        kids = [el('p', { class: 'help', text: 'This visual could not be drawn: ' + (e && e.message ? e.message : e) })];
      }
      var fig = el('figure', { id: 'visual-' + (i + 1) }, [v.title ? el('div', { class: 'vtitle', html: interpHtml(v.title, ctx) }) : null].concat(kids));
      if (v.caption) fig.appendChild(el('figcaption', { html: interpHtml(v.caption, ctx) }));
      visBox.appendChild(fig);
    });
  }

  function renderValue(v, dec) {
    if (Array.isArray(v) && v.length && Array.isArray(v[0])) return drawHeatmap({ data: v, decimals: dec }, { outputs: {}, state: {} }, false)[0];
    return el('span', { text: fmt(v, dec) });
  }

  function renderIntermediates(res) {
    interBox.innerHTML = '';
    var list = res ? res.intermediates : [];
    if (!list.length) { interBox.appendChild(el('p', { class: 'help', text: res ? 'No intermediate values.' : '—' })); return; }
    var kv = el('div', { class: 'kv' });
    list.forEach(function (it) {
      if (!it || typeof it !== 'object') return;
      kv.appendChild(el('div', { class: 'k', text: plain(it.label) }));
      kv.appendChild(el('div', { class: 'v' }, [renderValue(it.value, it.decimals)]));
      if (it.note) kv.appendChild(el('div', { class: 'n', text: plain(it.note) }));
    });
    interBox.appendChild(kv);
  }

  function renderChecks(res) {
    checkBox.innerHTML = '';
    var list = res ? res.checks : [];
    if (!list.length) { checkBox.appendChild(el('p', { class: 'help', text: res ? 'No self-checks.' : '—' })); return; }
    var ul = el('ul', { class: 'checks' });
    list.forEach(function (c) {
      if (!c || typeof c !== 'object') return;
      var ok = !!c.pass;
      ul.appendChild(el('li', { class: ok ? 'pass' : 'fail' }, [
        el('span', { class: 'mark', text: ok ? '✓ PASS' : '✗ FAIL' }), plain(c.label),
        c.detail !== undefined && c.detail !== '' ? el('div', { class: 'd', text: plain(c.detail) }) : null
      ]));
    });
    checkBox.appendChild(ul);
  }

  // ---------- page ----------
  function section(id, step, title, kids) {
    return el('section', { id: id, 'aria-labelledby': id + '-h' }, [step ? el('div', { class: 'step', text: step }) : null, el('h2', { id: id + '-h', text: title })].concat(kids));
  }

  function build() {
    app.innerHTML = '';
    var g = SPEC.grounding || {};
    var header = el('header', null, [
      el('h1', { html: SPEC.title || 'Interactive explanation' }),
      SPEC.subtitle ? rich('p', SPEC.subtitle, { class: 'sub' }) : null,
      g.paper_title ? el('p', { class: 'sub' }, [el('span', { text: 'Based on: ' }), rich('span', g.paper_title), g.section ? el('span', { html: ' — ' + g.section }) : null]) : null,
      SPEC.audience ? el('span', { class: 'badge', html: 'For: ' + SPEC.audience }) : null
    ]);
    app.appendChild(header);

    var idea = SPEC.idea || {};
    app.appendChild(section('idea', 'Step 1', 'The idea', [
      rich('div', idea.what),
      idea.equation ? rich('p', idea.equation, { class: 'eq', style: 'font-size:1.1em;margin:12px 0' }) : null,
      idea.why ? el('h3', { text: 'Why it matters' }) : null,
      idea.why ? rich('div', idea.why) : null
    ]));

    var symRows = arr(SPEC.symbols).map(function (s) {
      return el('tr', null, [rich('td', s.symbol), rich('td', s.meaning), rich('td', s.units || '—')]);
    });
    app.appendChild(section('symbols', 'Step 2', 'Symbols', [
      symRows.length ? el('div', { class: 'scroll' }, [el('table', { class: 'symbols' }, [
        el('thead', null, [el('tr', null, [el('th', { scope: 'col', text: 'Symbol' }), el('th', { scope: 'col', text: 'Meaning' }), el('th', { scope: 'col', text: 'Units / shape' })])]),
        el('tbody', null, symRows)])]) : el('p', { class: 'help', text: 'No symbols listed.' })
    ]));

    ctlBox = el('div', { class: 'controls', role: 'group', 'aria-label': 'Controls' });
    visBox = el('div', { class: 'visuals' });
    interBox = el('div');
    checkBox = el('div', { 'aria-live': 'polite' });
    app.appendChild(section('playground', 'Step 3', 'Playground', [
      SPEC.playground_intro ? rich('p', SPEC.playground_intro) : null,
      el('div', { class: 'play' }, [ctlBox, visBox]),
      el('div', { class: 'panels' }, [
        el('div', { class: 'panel', id: 'intermediates' }, [el('h3', { text: 'Intermediate values' }), interBox]),
        el('div', { class: 'panel', id: 'self-checks' }, [el('h3', { text: 'Self-checks (computed live)' }), checkBox])
      ])
    ]));

    arr(SPEC.explorations).slice(0, 2).forEach(function (x, i) {
      var kids = [el('dl', null, [
        el('dt', { text: 'Change' }), rich('dd', x.change),
        el('dt', { text: 'Observe' }), rich('dd', x.observe),
        el('dt', { text: 'Why' }), rich('dd', x.why)
      ])];
      if (x.preset && typeof x.preset === 'object' && Object.keys(x.preset).length) {
        kids.push(el('button', { class: 'btn primary', type: 'button', text: 'Set up this exploration', onclick: function () {
          applyPreset(x.preset); buildControls(); update();
          var pg = document.getElementById('playground'); if (pg && pg.scrollIntoView) pg.scrollIntoView({ block: 'start' });
        } }));
      }
      app.appendChild(section('explore-' + (i + 1), 'Step ' + (4 + i), 'Exploration ' + (i + 1) + (x.title ? ': ' + plain(x.title) : ''), [el('div', { class: 'explore' }, kids)]));
    });

    var lim = SPEC.limitation || {};
    var kindName = { limitation: 'Limitation', assumption: 'Assumption', misconception: 'Common misunderstanding' }[lim.kind] || 'Limitation';
    app.appendChild(section('limitation', 'Step 6', kindName, [rich('div', lim.text, { class: 'limit' })]));

    var quotes = arr(g.from_excerpt).map(function (q) { return rich('blockquote', q); });
    var ours = arr(g.our_simplifications).map(function (q) { return rich('li', q); });
    app.appendChild(section('grounding', null, 'Source and grounding', [
      el('p', null, [el('b', { text: 'Paper: ' }), rich('span', g.paper_title || 'not stated')]),
      g.section ? el('p', null, [el('b', { text: 'Section: ' }), rich('span', g.section)]) : null,
      g.equation ? el('p', null, [el('b', { text: 'Equation: ' }), rich('span', g.equation)]) : null,
      g.source_url ? el('p', null, [el('b', { text: 'Source: ' }), el('span', { class: 'src', text: plain(g.source_url) })]) : null,
      el('h3', { text: 'From the excerpt' }),
      quotes.length ? el('div', null, quotes) : el('p', { class: 'help', text: 'No direct quotes available.' }),
      el('h3', { text: 'Our simplifications and examples (not from the paper)' }),
      ours.length ? el('ul', null, ours) : el('p', { class: 'help', text: 'None listed.' }),
      el('p', { class: 'disclaimer', text: 'This page is a small teaching demo built from the excerpt. Its numbers come from the toy inputs above. It does not reproduce the paper\'s experiments or reported results.' })
    ]));

    buildControls();
    update();
  }

  try {
    SPEC = JSON.parse(document.getElementById('spec').textContent || '{}');
    DEC = SPEC.decimals !== undefined ? num(SPEC.decimals, 3) : 3;
    resetState();
    build();
  } catch (e) {
    showError('The page could not be built.', String(e && e.message ? e.message : e));
  }

  // test hook (read-only): lets local smoke tests inspect the state
  window.__p2p = { getState: function () { return clone(state); }, getResult: function () { return clone(lastResult); } };
})();
