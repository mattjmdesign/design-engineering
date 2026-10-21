/* collect.js: in-page collector for ui_measure.py.
 *
 * Evaluated inside the page under test by ui_measure.py through the DevTools pipe.
 * It returns raw facts only (geometry, computed styles, resolved colors, text,
 * control probes). It never applies a threshold and never decides pass or fail:
 * ui_measure.py and _rules.py do that with the numbers in defaults.json.
 *
 * It reads the page and writes nothing to it, with two exceptions that are undone
 * before it returns: controls below the fold are scrolled into view to probe what
 * a click would hit, and the scroll position is restored afterwards.
 *
 * Page text and attribute values are untrusted data. They are truncated and passed
 * through as strings; nothing here evaluates them.
 */
(function () {
  'use strict';
  var VERSION = 1;
  if (window.__uim && window.__uim.v === VERSION) { return true; }
  var U = { v: VERSION };
  Object.defineProperty(window, '__uim', { value: U, configurable: true });

  /* ------------------------------------------------------------------ helpers */
  var gcs = function (el, pseudo) { return window.getComputedStyle(el, pseudo || null); };
  var r2 = function (v) { return Math.round(v * 100) / 100; };
  var px = function (s) { var v = parseFloat(s); return isNaN(v) ? null : v; };
  var clamp255 = function (v) { return Math.max(0, Math.min(255, Math.round(v))); };
  var SKIP = { script: 1, style: 1, link: 1, meta: 1, noscript: 1, template: 1, head: 1, title: 1, base: 1,
    br: 1, wbr: 1, source: 1, track: 1, area: 1, map: 1, datalist: 1, option: 1, optgroup: 1 };
  var hasMap = typeof Element.prototype.computedStyleMap === 'function';
  // Elements the app owner marked as tooling (a state switcher, a debug bar) are never judged.
  var IGN = '[data-ui-measure-ignore],[data-state-switcher]';
  var isIgnored = function (el) { return !!(el && el.closest && el.closest(IGN)); };

  /* ------------------------------------------------------------------ colors */
  var cctx = null;
  var colorCache = new Map();
  var RGB_RE = /^rgba?\(\s*([\d.]+)\s*[, ]\s*([\d.]+)\s*[, ]\s*([\d.]+)\s*(?:[,/]\s*([\d.]+)(%?))?\s*\)$/;
  var COLOR_TOKEN = /rgba?\([^)]*\)|hsla?\([^)]*\)|oklch\([^)]*\)|oklab\([^)]*\)|lab\([^)]*\)|lch\([^)]*\)|color\([^)]*\)|#[0-9a-fA-F]{3,8}\b|\b(?:transparent|white|black)\b/g;

  function ctx2d() {
    if (!cctx) {
      var c = document.createElement('canvas');
      c.width = 1; c.height = 1;
      cctx = c.getContext('2d', { willReadFrequently: true });
    }
    return cctx;
  }

  // Any CSS color syntax (oklch, color-mix, lab, display-p3, named) goes through a 1x1 canvas readback.
  function viaCanvas(str) {
    try {
      var c = ctx2d();
      c.fillStyle = '#010203'; c.fillStyle = str;
      if (c.fillStyle === '#010203') {
        c.fillStyle = '#040506'; c.fillStyle = str;
        if (c.fillStyle === '#040506') { return null; }
      }
      var over = function (bg) {
        c.globalCompositeOperation = 'source-over';
        c.clearRect(0, 0, 1, 1);
        c.fillStyle = bg; c.fillRect(0, 0, 1, 1);
        c.fillStyle = str; c.fillRect(0, 0, 1, 1);
        var d = c.getImageData(0, 0, 1, 1).data;
        return [d[0], d[1], d[2]];
      };
      var b = over('#000000'), w = over('#ffffff');
      var alpha = 1 - ((w[0] - b[0]) + (w[1] - b[1]) + (w[2] - b[2])) / (3 * 255);
      alpha = Math.max(0, Math.min(1, alpha));
      if (alpha > 0.995) { return [b[0], b[1], b[2], 1]; }
      if (alpha < 0.004) { return [0, 0, 0, 0]; }
      return [clamp255(b[0] / alpha), clamp255(b[1] / alpha), clamp255(b[2] / alpha), Math.round(alpha * 1000) / 1000];
    } catch (e) { return null; }
  }

  function rgba(str) {
    if (!str) { return null; }
    var hit = colorCache.get(str);
    if (hit !== undefined) { return hit; }
    var out;
    var m = str.match(RGB_RE);
    if (m) {
      var a = m[4] === undefined ? 1 : parseFloat(m[4]) / (m[5] ? 100 : 1);
      out = [clamp255(+m[1]), clamp255(+m[2]), clamp255(+m[3]), Math.max(0, Math.min(1, a))];
    } else if (str === 'transparent') {
      out = [0, 0, 0, 0];
    } else {
      out = viaCanvas(str);
    }
    colorCache.set(str, out);
    return out;
  }

  function mix(fg, bg, a) {
    return [clamp255(fg[0] * a + bg[0] * (1 - a)), clamp255(fg[1] * a + bg[1] * (1 - a)), clamp255(fg[2] * a + bg[2] * (1 - a))];
  }

  function canvasBase() {
    var cs = gcs(document.documentElement).colorScheme || 'normal';
    var dark = matchMedia('(prefers-color-scheme: dark)').matches;
    var hasDark = /\bdark\b/.test(cs), hasLight = /\blight\b/.test(cs);
    return (hasDark && (!hasLight || dark)) ? [18, 18, 18] : [255, 255, 255];
  }

  var opMemo = new Map();
  function effOp(el) {
    if (!el || el.nodeType !== 1) { return 1; }
    var v = opMemo.get(el);
    if (v !== undefined) { return v; }
    var o = parseFloat(gcs(el).opacity);
    v = (isNaN(o) ? 1 : o) * effOp(el.parentElement);
    opMemo.set(el, v);
    return v;
  }

  function gradientStops(bgi) {
    var found = bgi.match(COLOR_TOKEN) || [];
    var out = [];
    for (var i = 0; i < found.length && out.length < 8; i++) {
      var c = rgba(found[i]);
      if (c && c[3] > 0) { out.push(c); }
    }
    return out;
  }

  // The color painted behind (and including) el's own background, resolved through ancestors.
  // Returns {c:[r,g,b]} when known or {c:null,u:reason} when it cannot be resolved.
  var bgMemo = new Map();
  function backdrop(el) {
    if (!el || el.nodeType !== 1) { return { c: canvasBase(), u: null }; }
    var hit = bgMemo.get(el);
    if (hit) { return hit; }
    var cs = gcs(el);
    var bgc = rgba(cs.backgroundColor);
    var bgi = cs.backgroundImage;
    var unknown = null, stops = null;
    if (bgi && bgi !== 'none') {
      if (bgi.indexOf('gradient') >= 0) { unknown = 'gradient'; stops = gradientStops(bgi); } else { unknown = 'image'; }
    } else if (cs.backdropFilter && cs.backdropFilter !== 'none') {
      unknown = 'backdrop-filter';
    } else if (cs.mixBlendMode && cs.mixBlendMode !== 'normal') {
      unknown = 'blend-mode';
    } else if (cs.filter && cs.filter !== 'none') {
      unknown = 'filter';
    }
    var alpha = bgc ? bgc[3] * effOp(el) : 0;
    var res;
    if (unknown) {
      res = { c: null, u: unknown };
      if (stops && stops.length) { res.st = stops; }
    } else if (alpha >= 0.999) {
      res = { c: [bgc[0], bgc[1], bgc[2]], u: null };
    } else {
      var under = backdrop(el.parentElement);
      if (alpha <= 0.001) { res = under; }
      else if (under.c) { res = { c: mix(bgc, under.c, alpha), u: null }; }
      else { res = { c: null, u: under.u, st: under.st }; }
    }
    bgMemo.set(el, res);
    return res;
  }

  function lumOf(c) {
    var f = function (v) { v = v / 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
  }

  /* ------------------------------------------------------------------ shadows */
  function splitTop(str) {
    var out = [], depth = 0, cur = '';
    for (var i = 0; i < str.length; i++) {
      var ch = str.charAt(i);
      if (ch === '(') { depth++; } else if (ch === ')') { depth--; }
      if (ch === ',' && depth === 0) { out.push(cur); cur = ''; } else { cur += ch; }
    }
    if (cur.trim()) { out.push(cur); }
    return out;
  }

  // box-shadow into [{x,y,b,s,i,c}] keeping only layers that paint something.
  function parseShadows(str) {
    if (!str || str === 'none') { return []; }
    var out = [];
    splitTop(str).forEach(function (layer) {
      layer = layer.trim();
      var inset = /\binset\b/.test(layer);
      layer = layer.replace(/\binset\b/, ' ');
      var cm = layer.match(COLOR_TOKEN);
      var color = cm ? rgba(cm[0]) : null;
      if (cm) { layer = layer.replace(cm[0], ' '); }
      var nums = (layer.match(/-?[\d.]+(?:px)?/g) || []).map(parseFloat);
      if (nums.length < 2) { return; }
      var sh = { x: nums[0], y: nums[1], b: nums[2] || 0, s: nums[3] || 0, i: inset ? 1 : 0, c: color || [0, 0, 0, 1] };
      if (sh.c[3] > 0.004 && (sh.x || sh.y || sh.b || sh.s)) { out.push(sh); }
    });
    return out;
  }

  /* ------------------------------------------------------------------ selectors and names */
  function shortSel(el) {
    var s = el.localName;
    var id = el.getAttribute('id');
    if (id && /^[A-Za-z][\w-]{0,40}$/.test(id)) { s += '#' + id; }
    var cls = (el.getAttribute('class') || '').split(/\s+/).filter(function (c) {
      return c && c.length <= 32 && !/[:\[\]\/()%!@]/.test(c);
    }).slice(0, 3);
    if (cls.length) { s += '.' + cls.join('.'); }
    var t = el.localName === 'input' ? (el.getAttribute('type') || '') : '';
    if (t) { s += '[type=' + t + ']'; }
    return s;
  }

  function cleanText(s, n) {
    var out = String(s).replace(/[\u0000-\u001f\u007f]+/g, ' ').replace(/\s+/g, ' ').trim().slice(0, n);
    return /[\ud800-\udbff]$/.test(out) ? out.slice(0, -1) : out;
  }

  function hiddenForName(el) {
    if (el.nodeType !== 1) { return false; }
    if (el.getAttribute('aria-hidden') === 'true') { return true; }
    var cs = gcs(el);
    return cs.display === 'none' || cs.visibility === 'hidden';
  }

  function textOf(el, depth) {
    var out = '';
    for (var c = el.firstChild; c; c = c.nextSibling) {
      if (c.nodeType === 3) { out += c.nodeValue; }
      else if (c.nodeType === 1 && depth < 6 && !hiddenForName(c)) {
        var t = c.localName;
        if (t === 'img') { out += ' ' + (c.getAttribute('alt') || ''); }
        else if (t === 'svg') {
          var ti = c.querySelector('title');
          out += ' ' + (c.getAttribute('aria-label') || (ti ? ti.textContent : ''));
        } else if (c.getAttribute('aria-label')) { out += ' ' + c.getAttribute('aria-label'); }
        else { out += ' ' + textOf(c, depth + 1); }
      }
    }
    return out;
  }

  // Approximate accessible name. ui_measure.py replaces it with Chrome's own accessibility tree when available.
  function nameApprox(el) {
    var lb = el.getAttribute('aria-labelledby');
    if (lb) {
      var t = lb.split(/\s+/).map(function (id) { var n = document.getElementById(id); return n ? textOf(n, 0) : ''; }).join(' ').trim();
      if (t) { return { n: cleanText(t, 80), s: 'labelledby' }; }
    }
    var al = el.getAttribute('aria-label');
    if (al && al.trim()) { return { n: cleanText(al, 80), s: 'aria-label' }; }
    if (el.labels && el.labels.length) {
      var lt = Array.prototype.map.call(el.labels, function (l) { return textOf(l, 0); }).join(' ').trim();
      if (lt) { return { n: cleanText(lt, 80), s: 'label' }; }
    }
    var tag = el.localName, type = (el.getAttribute('type') || '').toLowerCase();
    if (tag === 'input' && (type === 'button' || type === 'submit' || type === 'reset')) {
      return { n: cleanText(el.value || type, 80), s: 'value' };
    }
    if (tag === 'input' && type === 'image') { return { n: cleanText(el.getAttribute('alt') || '', 80), s: 'alt' }; }
    if (tag !== 'input' && tag !== 'select' && tag !== 'textarea') {
      var content = textOf(el, 0).replace(/\s+/g, ' ').trim();
      if (content) { return { n: cleanText(content, 80), s: 'content' }; }
    }
    var title = el.getAttribute('title');
    if (title && title.trim()) { return { n: cleanText(title, 80), s: 'title' }; }
    var ph = el.getAttribute('placeholder');
    if (ph && ph.trim()) { return { n: cleanText(ph, 80), s: 'placeholder' }; }
    return { n: '', s: 'none' };
  }

  /* ------------------------------------------------------------------ box facts */
  function sides(el, cs, kind) {
    var names = ['Top', 'Right', 'Bottom', 'Left'], out = [0, 0, 0, 0], any = false, map = null;
    for (var i = 0; i < 4; i++) {
      var v = px(cs[kind + names[i]]);
      if (!v) { continue; }
      any = true;
      if (hasMap) {
        map = map || el.computedStyleMap();
        var sv = map.get(kind + '-' + names[i].toLowerCase());
        if (sv && sv.unit !== 'px') { out[i] = null; continue; }
      }
      out[i] = r2(v);
    }
    return any ? out : null;
  }

  function authored(el, prop) {
    if (!hasMap) { return null; }
    var v = el.computedStyleMap().get(prop);
    return !!(v && v.unit === 'px');
  }

  function radiusOf(cs, w, h) {
    var names = ['borderTopLeftRadius', 'borderTopRightRadius', 'borderBottomRightRadius', 'borderBottomLeftRadius'];
    var out = [], any = false;
    for (var i = 0; i < 4; i++) {
      var raw = cs[names[i]] || '0px';
      var v = px(raw) || 0;
      if (raw.indexOf('%') >= 0) { v = Math.min(w, h) * v / 100; }
      if (v > 0) { any = true; }
      out.push(r2(v));
    }
    return any ? out : null;
  }

  // Which computed text-wrap property this Chrome exposes: 'text-wrap-style' (Chrome 130 and later), 'text-wrap' (earlier), or null.
  // Rules that need the value must not guess when it is null.
  function textWrapApi(cs) {
    if (typeof cs.textWrapStyle === 'string' && cs.textWrapStyle !== '') { return 'text-wrap-style'; }
    if (typeof cs.textWrap === 'string' && cs.textWrap !== '') { return 'text-wrap'; }
    return null;
  }

  // The computed text-wrap style when it is not the default: 'balance', 'pretty', or 'stable'. Null for the default and when the API is missing.
  function textWrapStyle(cs) {
    var raw = typeof cs.textWrapStyle === 'string' && cs.textWrapStyle !== '' ? cs.textWrapStyle : (typeof cs.textWrap === 'string' ? cs.textWrap : '');
    var m = String(raw).match(/\b(balance|pretty|stable)\b/);
    return m ? m[1] : null;
  }

  function isInlineInSentence(el, cs) {
    if (cs.display !== 'inline') { return false; }
    var p = el.parentElement;
    while (p && /^inline/.test(gcs(p).display)) { p = p.parentElement; }
    if (!p) { return false; }
    var own = (el.textContent || '').replace(/\s+/g, ' ').trim();
    var all = (p.textContent || '').replace(/\s+/g, ' ').trim();
    return all.length - own.length >= 8;
  }

  /* ------------------------------------------------------------------ fonts */
  var fontCache = new Map();
  var fontCtx = null;
  function firstFamily(stack) {
    var m = (stack || '').match(/^\s*(?:"([^"]+)"|'([^']+)'|([^,]+))/);
    return m ? (m[1] || m[2] || (m[3] || '').trim()) : '';
  }
  function chWidth(cs) {
    var key = cs.fontStyle + '|' + cs.fontWeight + '|' + cs.fontSize + '|' + cs.fontFamily;
    var v = fontCache.get(key);
    if (v !== undefined) { return v; }
    try {
      fontCtx = fontCtx || ctx2d();
      fontCtx.font = cs.fontStyle + ' ' + cs.fontWeight + ' ' + cs.fontSize + ' ' + cs.fontFamily;
      v = r2(fontCtx.measureText('0').width);
    } catch (e) { v = null; }
    fontCache.set(key, v);
    return v;
  }

  var GENERIC_FAMILY = /^(serif|sans-serif|monospace|cursive|fantasy|system-ui|ui-serif|ui-sans-serif|ui-monospace|ui-rounded|emoji|math|fangsong|-apple-system|blinkmacsystemfont)$/i;
  function familyAvailable(name) {
    if (GENERIC_FAMILY.test(name)) { return true; }
    var faces = document.fonts ? Array.from(document.fonts) : [];
    var matches = faces.filter(function (f) { return f.family.replace(/^["']|["']$/g, '').toLowerCase() === name.toLowerCase(); });
    if (matches.length) {
      return matches.some(function (f) { return f.status === 'loaded'; });
    }
    try {
      var c = ctx2d(), sample = 'mmmmmmmmmmlli wWQ@#0123';
      var bases = ['monospace', 'serif', 'sans-serif'];
      for (var i = 0; i < bases.length; i++) {
        c.font = '64px ' + bases[i];
        var w0 = c.measureText(sample).width;
        c.font = '64px "' + name.replace(/"/g, '') + '", ' + bases[i];
        if (c.measureText(sample).width !== w0) { return true; }
      }
    } catch (e) { return null; }
    return false;
  }

  /* ------------------------------------------------------------------ settle and lookups */
  function visible(el) {
    if (!el || el.getClientRects().length === 0) { return false; }
    var cs = gcs(el);
    return cs.visibility !== 'hidden' && cs.display !== 'none';
  }

  // Visible text of the page outside ignored tooling, in document order.
  function appText() {
    var out = [], walker = document.createTreeWalker(document.body || document.documentElement, NodeFilter.SHOW_TEXT, null);
    var seen = new Map();
    var node;
    while ((node = walker.nextNode())) {
      if (!/\S/.test(node.nodeValue)) { continue; }
      var el = node.parentElement;
      if (!el) { continue; }
      var ok = seen.get(el);
      if (ok === undefined) { ok = visible(el) && !isIgnored(el); seen.set(el, ok); }
      if (ok) { out.push(node.nodeValue); }
    }
    return out.join(' ').replace(/\s+/g, ' ');
  }

  // spec is a CSS selector or "text:some words". Returns {ok:boolean, error?:string}.
  U.has = function (spec) {
    try {
      if (spec.indexOf('text:') === 0) {
        var needle = spec.slice(5).trim().toLowerCase().replace(/\s+/g, ' ');
        return { ok: appText().toLowerCase().indexOf(needle) >= 0 };
      }
      var all = document.querySelectorAll(spec);
      for (var i = 0; i < all.length; i++) {
        if (visible(all[i]) && !isIgnored(all[i])) { return { ok: true }; }
      }
      return { ok: false };
    } catch (e) {
      return { ok: false, error: String(e && e.message || e).slice(0, 120) };
    }
  };

  U.settle = function (maxMs, quietMs) {
    return new Promise(function (resolve) {
      var start = performance.now(), last = start, fontsDone = false, finished = false;
      var mo = new MutationObserver(function () { last = performance.now(); });
      try { mo.observe(document.documentElement, { subtree: true, childList: true, attributes: true, characterData: true }); } catch (e) { /* ignore */ }
      var fonts = (document.fonts && document.fonts.ready) ? document.fonts.ready : Promise.resolve();
      fonts.then(function () { fontsDone = true; last = performance.now(); }, function () { fontsDone = true; });
      var finish = function (quiet) {
        if (finished) { return; }
        finished = true;
        mo.disconnect();
        var fired = false;
        var go = function () { if (!fired) { fired = true; resolve({ ms: Math.round(performance.now() - start), quiet: quiet, fonts: fontsDone }); } };
        requestAnimationFrame(function () { requestAnimationFrame(go); });
        setTimeout(go, 120);
      };
      var tick = function () {
        var now = performance.now();
        if (fontsDone && now - last >= quietMs) { finish(true); }
        else if (now - start > maxMs) { finish(false); }
        else { setTimeout(tick, 25); }
      };
      tick();
    });
  };

  /* ------------------------------------------------------------------ collection */
  var FOCUSABLE_INSIDE = 'a[href],button,input:not([type=hidden]),select,textarea,summary,[tabindex]:not([tabindex="-1"]),[role=button],[role=link],[role=checkbox],[role=menuitem],[role=tab],[role=switch],[role=option]';
  var INTERACTIVE_ANCESTOR = 'a[href],button,[role=button],[role=link],[role=menuitem],[role=tab],[role=option],label,summary,select,[tabindex]';
  var ROLE_CONTROLS = { button: 1, link: 1, checkbox: 1, radio: 1, 'switch': 1, tab: 1, menuitem: 1, menuitemcheckbox: 1,
    menuitemradio: 1, option: 1, slider: 1, spinbutton: 1, combobox: 1, textbox: 1, searchbox: 1, treeitem: 1 };
  var NO_TEXT_OWNER = { textarea: 1, select: 1, option: 1, script: 1, style: 1, noscript: 1, template: 1, title: 1, svg: 1 };

  function controlKind(el, tag, role, ti) {
    if (tag === 'a') { return el.hasAttribute('href') ? 'link' : null; }
    if (tag === 'button' || tag === 'summary' || tag === 'select' || tag === 'textarea') { return tag; }
    if (tag === 'input') {
      var t = (el.getAttribute('type') || 'text').toLowerCase();
      return t === 'hidden' ? null : 'input:' + t;
    }
    if (role && ROLE_CONTROLS[role]) { return 'role:' + role; }
    if (el.getAttribute('contenteditable') !== null && el.isContentEditable) { return 'editable'; }
    if (ti !== null && parseInt(ti, 10) >= 0) { return 'tabindex'; }
    return null;
  }

  function lineGroups(rects) {
    rects.sort(function (a, b) { return a.top - b.top || a.left - b.left; });
    var lines = [];
    rects.forEach(function (q) {
      var last = lines[lines.length - 1];
      if (last && Math.abs(q.top - last.top) < Math.max(2, q.height * 0.5)) {
        last.left = Math.min(last.left, q.left);
        last.right = Math.max(last.right, q.right);
        last.bottom = Math.max(last.bottom, q.bottom);
      } else {
        lines.push({ left: q.left, right: q.right, top: q.top, bottom: q.bottom });
      }
    });
    return lines;
  }

  function contentWidth(el, cs) {
    var b = el;
    if (/^inline/.test(cs.display) || cs.display === 'contents') {
      b = el.parentElement;
      while (b && /^inline/.test(gcs(b).display)) { b = b.parentElement; }
    }
    if (!b) { return null; }
    var bc = gcs(b);
    return Math.max(0, r2(b.clientWidth - (px(bc.paddingLeft) || 0) - (px(bc.paddingRight) || 0)));
  }

  // The part of the viewport an element can paint into: the intersection of its overflow-clipping ancestors, or null.
  var clipMemo = new Map();
  function clipOf(el) {
    if (!el || el === document.body || el === document.documentElement || el.nodeType !== 1) { return null; }
    if (clipMemo.has(el)) { return clipMemo.get(el); }
    var cs = gcs(el);
    var parent = cs.position === 'fixed' ? null : clipOf(el.parentElement);
    var box = null;
    if (cs.overflowX !== 'visible' || cs.overflowY !== 'visible') {
      var r = el.getBoundingClientRect();
      box = [r.left + (px(cs.borderLeftWidth) || 0), r.top + (px(cs.borderTopWidth) || 0), r.right - (px(cs.borderRightWidth) || 0), r.bottom - (px(cs.borderBottomWidth) || 0)];
      if (cs.overflowX === 'visible') { box[0] = -1e9; box[2] = 1e9; }
      if (cs.overflowY === 'visible') { box[1] = -1e9; box[3] = 1e9; }
    }
    var res = !box ? parent : (!parent ? box : [Math.max(parent[0], box[0]), Math.max(parent[1], box[1]), Math.min(parent[2], box[2]), Math.min(parent[3], box[3])]);
    clipMemo.set(el, res);
    return res;
  }

  function textRecord(el, n, cs, sx, sy, tag) {
    if (NO_TEXT_OWNER[tag]) { return null; }
    var owned = [], s = '', hasBr = false;
    for (var c = el.firstChild; c; c = c.nextSibling) {
      if (c.nodeType === 3 && /\S/.test(c.nodeValue)) { owned.push(c); s += (s ? ' ' : '') + c.nodeValue; }
      else if (c.nodeType === 1 && c.localName === 'br') { hasBr = true; }
    }
    if (!owned.length) { return null; }
    var rng = document.createRange(), rects = [];
    owned.forEach(function (node) {
      rng.selectNodeContents(node);
      var list = rng.getClientRects();
      for (var k = 0; k < list.length; k++) {
        if (list[k].width >= 0.5 && list[k].height >= 0.5) { rects.push(list[k]); }
      }
    });
    if (!rects.length) { return null; }
    var lines = lineGroups(rects);
    var clip = clipOf(el), trimmed = false;
    if (clip) {
      var kept = [];
      lines.forEach(function (l) {
        var nl = { left: Math.max(l.left, clip[0]), right: Math.min(l.right, clip[2]), top: Math.max(l.top, clip[1]), bottom: Math.min(l.bottom, clip[3]) };
        if (nl.right - nl.left > 0.5 && nl.bottom - nl.top > 0.5) {
          if (nl.left !== l.left || nl.right !== l.right || nl.top !== l.top || nl.bottom !== l.bottom) { trimmed = true; }
          kept.push(nl);
        } else { trimmed = true; }
      });
      lines = kept;
      if (!lines.length) { return null; }
    }
    var first = lines[0];
    var full = s.replace(/\s+/g, ' ').trim();
    var fill = rgba(cs.webkitTextFillColor);
    var col = rgba(cs.color);
    var rec = {
      n: n.i, t: cleanText(full, 240), len: full.length, ln: lines.length,
      lr: lines.slice(0, 8).map(function (l) { return [r2(l.left + sx), r2(l.top + sy), r2(l.right - l.left), r2(l.bottom - l.top)]; }),
      tx: r2(first.left + sx), cl: trimmed ? 1 : 0,
      fs: px(cs.fontSize), fw: parseInt(cs.fontWeight, 10) || 400,
      lh: cs.lineHeight === 'normal' ? null : px(cs.lineHeight),
      ls: px(cs.letterSpacing) || 0,
      ff: firstFamily(cs.fontFamily), fam: cs.fontFamily.slice(0, 160),
      ta: cs.textAlign, fg: col, op: r2(effOp(el))
    };
    if (fill && fill[3] === 0 && col && col[3] > 0) { rec.fgu = 'text-fill'; }
    else if (fill && fill[3] < 1 && col && col[3] === 1) { col = fill; rec.fg = fill; }
    if (cs.backgroundClip === 'text' || cs.webkitBackgroundClip === 'text') { rec.fgu = 'text-clip'; }
    var bd = backdrop(el);
    rec.bg = bd;
    if (cs.fontVariantNumeric !== 'normal') { rec.fvn = cs.fontVariantNumeric; }
    if (cs.fontFeatureSettings !== 'normal') { rec.ffs = cs.fontFeatureSettings.slice(0, 60); }
    if (cs.textTransform !== 'none') { rec.tt = cs.textTransform; }
    if (/^(nowrap|pre|pre-wrap|pre-line|break-spaces)$/.test(cs.whiteSpace)) { rec.ws = cs.whiteSpace; }
    if (hasBr) { rec.br = 1; }  // a <br> breaks the line on purpose
    var tws = textWrapStyle(cs);
    if (tws) { rec.tw = tws; }
    if (rec.len >= 40 || rec.ln >= 2) {
      rec.cw = contentWidth(el, cs);
      rec.ch = chWidth(cs);
    }
    return rec;
  }

  function probeControls(controls, els) {
    var H = window.innerHeight, W = window.innerWidth;
    var y0 = window.pageYOffset;
    var order = controls.map(function (c, i) { return i; }).sort(function (a, b) { return controls[a].r[1] - controls[b].r[1]; });
    var scrollTo = function (y) {
      try { window.scrollTo({ top: Math.max(0, y), left: 0, behavior: 'instant' }); } catch (e) { window.scrollTo(0, Math.max(0, y)); }
    };
    var hit = function (el, x, y) {
      if (x < 0 || y < 0 || x >= W || y >= H) { return null; }
      var t = document.elementFromPoint(x, y);
      if (!t || isIgnored(t)) { return null; }
      if (t.closest && t.closest('dialog,[role=dialog],[role=alertdialog],[aria-modal=true]')) { return null; }
      if (el === t || el.contains(t)) { return 1; }
      var lb = t.closest ? t.closest('label') : null;
      return lb && lb.control === el ? 1 : 0;
    };
    order.forEach(function (idx) {
      var c = controls[idx], el = els[c.n];
      if (!el || c.dis) { return; }
      var r = el.getBoundingClientRect();
      if (r.width < 1 || r.height < 1 || r.height > H - 8) { return; }
      if (r.top < 4 || r.bottom > H - 4) {
        scrollTo(r.top + window.pageYOffset - Math.max(8, (H - r.height) / 2));
        r = el.getBoundingClientRect();
      }
      var i = Math.min(2, r.width / 4, r.height / 4);
      var pts = [[r.left + i, r.top + i], [r.right - i, r.top + i], [r.left + i, r.bottom - i], [r.right - i, r.bottom - i],
        [r.left + r.width / 2, r.top + r.height / 2]];
      var hits = 0, tried = 0, other = 0;
      pts.forEach(function (p) {
        var h = hit(el, p[0], p[1]);
        if (h === null) { return; }
        tried++;
        if (h) { hits++; } else { other++; }
      });
      c.pr = [hits, tried];
      var tgt = c.tr;
      var minSide = Math.min(tgt[2], tgt[3]);
      var cx = r.left + r.width / 2, cy = r.top + r.height / 2;
      var box = function (size) {
        var h2 = size / 2 - 0.5, ok = true, seen = 0;
        [[cx - h2, cy - h2], [cx + h2, cy - h2], [cx - h2, cy + h2], [cx + h2, cy + h2]].forEach(function (p) {
          var h = hit(el, p[0], p[1]);
          if (h === null) { return; }
          seen++;
          if (!h) { ok = false; }
        });
        return seen >= 3 && ok;
      };
      if (minSide < 24) { c.e24 = box(24) ? 1 : 0; }
      if (minSide < 44) { c.e44 = box(44) ? 1 : 0; }
    });
    scrollTo(y0);
  }

  function pageHash(str) {
    var h1 = 0x811c9dc5, h2 = 0x01000193;
    for (var i = 0; i < str.length; i++) {
      var ch = str.charCodeAt(i);
      h1 = Math.imul(h1 ^ ch, 0x01000193) >>> 0;
      h2 = Math.imul(h2 + ch, 0x85ebca6b) >>> 0;
    }
    return ('00000000' + h1.toString(16)).slice(-8) + ('00000000' + h2.toString(16)).slice(-8);
  }

  function collectFacts() {
    var de = document.documentElement, b = document.body, cs = gcs(de);
    var mv = document.querySelector('meta[name=viewport]');
    var vv = window.visualViewport;
    var pageBg = backdrop(b || de);
    var lum = pageBg.c ? lumOf(pageBg.c) : null;
    return {
      innerWidth: window.innerWidth, innerHeight: window.innerHeight,
      clientWidth: de.clientWidth, clientHeight: de.clientHeight,
      scrollWidth: de.scrollWidth, scrollHeight: de.scrollHeight,
      bodyScrollWidth: b ? b.scrollWidth : null,
      scrollbar_px: window.innerWidth - de.clientWidth,
      dpr: window.devicePixelRatio,
      theme: matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light',
      rendered_theme: lum === null ? null : (lum < 0.18 ? 'dark' : 'light'),
      color_scheme: cs.colorScheme,
      url: location.href, title: cleanText(document.title, 120), lang: de.getAttribute('lang') || '',
      meta_viewport: mv ? cleanText(mv.getAttribute('content') || '', 120) : null,
      visual_viewport_width: vv ? r2(vv.width) : null,
      hover: matchMedia('(hover: hover)').matches, pointer_fine: matchMedia('(pointer: fine)').matches,
      reduced_motion: matchMedia('(prefers-reduced-motion: reduce)').matches,
      root_overflow_x: cs.overflowX, body_overflow_x: b ? gcs(b).overflowX : null,
      text_wrap_api: textWrapApi(cs),
      ready_state: document.readyState, scroll_y: window.pageYOffset,
      canvas: canvasBase(), page_bg: pageBg.c, dom_hash: pageHash(de.outerHTML), modal_open: !!modalRoot()
    };
  }

  U.collect = function (opts) {
    opts = opts || {};
    var t0 = performance.now();
    var maxNodes = opts.maxNodes || 5000;
    if (opts.ignore) { IGN = opts.ignore; }
    var ignoredEls = 0, ignoredRoots = 0, ignoredRight = null;
    var res = { v: VERSION, errors: [] };
    opMemo = new Map(); bgMemo = new Map(); clipMemo = new Map();
    var sx = window.pageXOffset, sy = window.pageYOffset;
    var nodes = [], els = [], texts = [], controls = [], images = [], headings = [], tables = [], clicks = [];
    var fontsUsed = {};
    var modal = modalRoot();
    var truncated = false, srOnly = 0, offscreen = 0;
    try { res.facts = collectFacts(); } catch (e) { res.errors.push('facts: ' + e); res.facts = {}; }

    try {
      var stack = [[document.body, -1, 0, 0, 0]];
      while (stack.length) {
        var it = stack.pop(), el = it[0], pidx = it[1], depth = it[2];
        var tag = el.localName;
        if (SKIP[tag]) { continue; }
        var cs = gcs(el);
        if (cs.display === 'none') { continue; }
        if (el.matches(IGN)) {
          ignoredRoots++;
          ignoredEls += 1 + el.getElementsByTagName('*').length;
          if (cs.position !== 'fixed' && cs.position !== 'sticky') {
            var ir = el.getBoundingClientRect();
            ignoredRight = Math.max(ignoredRight === null ? 0 : ignoredRight, ir.right + sx, ir.left + sx + el.scrollWidth);
          }
          continue;
        }
        if (cs.opacity === '0' || cs.contentVisibility === 'hidden') { continue; }
        var rect = el.getBoundingClientRect();
        var contents = cs.display === 'contents';
        var recorded = !contents && cs.visibility !== 'hidden' && (rect.width > 0 || rect.height > 0);
        var idx = pidx;
        var n = null;
        if (recorded) {
          if (nodes.length >= maxNodes) { truncated = true; break; }
          n = { i: nodes.length, p: pidx, t: tag, s: shortSel(el), d: cs.display,
            r: [r2(rect.left + sx), r2(rect.top + sy), r2(rect.width), r2(rect.height)] };
          if (it[4] > 1) { n.nth = it[3]; }
          idx = n.i;
          var clsAttr = (el.getAttribute('class') || '').replace(/\s+/g, ' ').trim();
          if (clsAttr) { n.c = clsAttr.slice(0, 160); }
          var id = el.getAttribute('id'), role = (el.getAttribute('role') || '').split(/\s+/)[0].toLowerCase();
          if (role) { n.role = role; }
          if (tag === 'a' && el.hasAttribute('href')) { n.h = 1; }
          if (tag === 'input') { n.ty = (el.getAttribute('type') || 'text').toLowerCase(); }
          if (el.getAttribute('aria-hidden') === 'true') { n.ah = 1; }
          var tid = el.getAttribute('data-testid'); if (tid) { n.tid = cleanText(tid, 40); }
          // data-ui-allow="rule.id: reason" opts one element out of a finding on purpose; data-slot names a shadcn/ui part.
          var ua = el.getAttribute('data-ui-allow'); if (ua) { n.ua = cleanText(ua, 240); }
          var slot = el.getAttribute('data-slot'); if (slot) { n.sl = cleanText(slot, 32); }
          var al = el.getAttribute('aria-label'); if (al) { n.al = cleanText(al, 40); }
          if (id && /^[A-Za-z][\w-]{0,40}$/.test(id) && (role || /^(main|nav|header|footer|aside|form)$/.test(tag))) { n.id = id; }
          if (cs.position !== 'static') { n.pos = cs.position; if (cs.zIndex !== 'auto') { n.z = parseInt(cs.zIndex, 10); } }
          var pad = sides(el, cs, 'padding'); if (pad) { n.pad = pad; }
          var mar = sides(el, cs, 'margin'); if (mar) { n.mar = mar; }
          if (/flex|grid/.test(cs.display)) {
            n.gap = [px(cs.rowGap), px(cs.columnGap)];
            if (/flex/.test(cs.display)) { n.fd = cs.flexDirection; }
            if (cs.justifyContent && !/^(normal|flex-start|start|left)$/.test(cs.justifyContent)) { n.jc = cs.justifyContent; }
          }
          if (cs.overflowX !== 'visible' || cs.overflowY !== 'visible') {
            n.ov = [cs.overflowX, cs.overflowY];
            // The client box (inside borders and any scrollbar) is the edge content is laid out against.
            n.sc = [el.clientLeft, el.clientWidth, el.scrollWidth];
            if ((cs.overflowX === 'hidden' || cs.overflowX === 'clip' || cs.overflowY === 'hidden' || cs.overflowY === 'clip') &&
                (el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1) &&
                tag !== 'input' && tag !== 'textarea' && tag !== 'select' && tag !== 'body' && tag !== 'html') {
              n.sw = [el.scrollWidth, el.clientWidth, el.scrollHeight, el.clientHeight];
              if (cs.textOverflow === 'ellipsis') { n.te = 1; }
              if (cs.webkitLineClamp && cs.webkitLineClamp !== 'none') { n.lc = 1; }
            }
            if ((cs.overflowX === 'auto' || cs.overflowX === 'scroll') && el.scrollWidth > el.clientWidth + 1 &&
                tag !== 'input' && tag !== 'textarea' && tag !== 'select' && tag !== 'body' && tag !== 'html') {
              n.hs = [el.scrollWidth, el.clientWidth];
            }
          }
          var bgc = rgba(cs.backgroundColor);
          if (bgc && bgc[3] > 0.001) { n.bg = bgc; }
          var bgi = cs.backgroundImage;
          if (bgi && bgi !== 'none') {
            n.bgi = bgi.indexOf('gradient') >= 0 ? 'gradient' : 'image';
            if (n.bgi === 'gradient') { var gst = gradientStops(bgi); if (gst.length) { n.gst = gst.slice(0, 4); } }
          }
          var bw = [px(cs.borderTopWidth) || 0, px(cs.borderRightWidth) || 0, px(cs.borderBottomWidth) || 0, px(cs.borderLeftWidth) || 0];
          if (bw[0] || bw[1] || bw[2] || bw[3]) {
            n.bw = bw;
            var side = bw[0] ? 'Top' : (bw[3] ? 'Left' : (bw[2] ? 'Bottom' : 'Right'));
            n.bc = rgba(cs['border' + side + 'Color']);
            n.bs = cs['border' + side + 'Style'];
            // Borders of unequal width are where a one-sided stripe lives: keep the color of each side.
            if (!(bw[0] === bw[1] && bw[1] === bw[2] && bw[2] === bw[3])) {
              n.bcs = [rgba(cs.borderTopColor), rgba(cs.borderRightColor), rgba(cs.borderBottomColor), rgba(cs.borderLeftColor)];
            }
          }
          var rad = radiusOf(cs, rect.width, rect.height); if (rad) { n.rad = rad; }
          var shadows = parseShadows(cs.boxShadow); if (shadows.length) { n.sh = shadows; }
          var tshadows = cs.textShadow && cs.textShadow !== 'none' ? parseShadows(cs.textShadow) : []; if (tshadows.length) { n.ts = tshadows; }
          // A box whose width is auto or a content keyword, with no max-width and not inline, is as wide as its container allows, unless a flex or
          // grid parent or the keyword shrinks it to its content. Rules use this to tell a content-sized block from a column.
          if (hasMap && cs.display.indexOf('inline') !== 0 && cs.maxWidth === 'none') {
            var wv = el.computedStyleMap().get('width');
            if (wv && /^(auto|fit-content|max-content|min-content)$/.test(wv.value || '')) { n.fit = 1; }
          }
          if (cs.opacity !== '1') { n.op = parseFloat(cs.opacity); }
          if (cs.cursor === 'pointer') { n.cur = 1; }
          if (cs.transform !== 'none') { n.tf = 1; }
          nodes.push(n); els.push(el);

          // text
          if (!contents) {
            var rec = textRecord(el, n, cs, sx, sy, tag);
            if (rec) {
              if (rect.width <= 1 || rect.height <= 1) { srOnly++; }
              else if (rect.right + sx < 0 || rect.bottom + sy < 0) { offscreen++; }
              else {
                var hm = tag.match(/^h([1-6])$/);
                if (hm) { rec.hd = parseInt(hm[1], 10); }
                else if (n.role === 'heading') { rec.hd = parseInt(el.getAttribute('aria-level'), 10) || 2; }
                texts.push(rec);
                var fk = rec.fam;
                fontsUsed[fk] = (fontsUsed[fk] || 0) + rec.len;
              }
            }
          }

          // headings
          if (/^h[1-6]$/.test(tag) || n.role === 'heading') {
            headings.push({ n: n.i, l: /^h([1-6])$/.test(tag) ? parseInt(tag.charAt(1), 10) : (parseInt(el.getAttribute('aria-level'), 10) || 2),
              t: cleanText(el.textContent, 80) });
          }

          // images
          if (tag === 'img') {
            var cmp = el.complete;
            images.push({ n: n.i, alt: el.hasAttribute('alt') ? cleanText(el.getAttribute('alt'), 120) : null, role: n.role || null,
              nw: el.naturalWidth, nh: el.naturalHeight, ok: cmp && el.naturalWidth > 0, cmp: cmp,
              dims: el.hasAttribute('width') && el.hasAttribute('height'), ar: cs.aspectRatio !== 'auto',
              svg: /^data:image\/svg|\.svg(\?|#|$)/i.test(el.currentSrc || el.getAttribute('src') || '') ? 1 : 0,
              lz: el.getAttribute('loading') || '', fit: cs.objectFit, src: (el.currentSrc || el.getAttribute('src') || '').slice(0, 40).replace(/^data:.*/, 'data:'),
              su: (el.currentSrc || el.getAttribute('src') || '').slice(0, 300).replace(/^data:.*/, '') });
          }

          // tables
          if (tag === 'table') {
            var tbl = { n: n.i, rows: el.rows.length, cols: [] };
            var rowsList = Array.prototype.slice.call(el.tBodies.length ? el.tBodies[0].rows : el.rows, 0, 60);
            rowsList.forEach(function (row) {
              Array.prototype.slice.call(row.cells, 0, 20).forEach(function (cell, ci) {
                var ccs = gcs(cell);
                tbl.cols[ci] = tbl.cols[ci] || [];
                tbl.cols[ci].push({ t: cleanText(cell.textContent, 40), ta: ccs.textAlign,
                  fvn: ccs.fontVariantNumeric !== 'normal' ? ccs.fontVariantNumeric : '',
                  ffs: ccs.fontFeatureSettings !== 'normal' ? ccs.fontFeatureSettings.slice(0, 40) : '' });
              });
            });
            tables.push(tbl);
          }

          // controls
          var ti = el.getAttribute('tabindex');
          var kind = controlKind(el, tag, n.role, ti);
          if (kind) {
            var dis = !!(el.disabled || el.getAttribute('aria-disabled') === 'true' || el.hasAttribute('inert'));
            var tr = [n.r[0], n.r[1], n.r[2], n.r[3]];
            var labelled = 0;
            if (el.labels && el.labels.length && (kind === 'input:checkbox' || kind === 'input:radio')) {
              Array.prototype.forEach.call(el.labels, function (lb) {
                var lr = lb.getBoundingClientRect();
                if (lr.width > 0 && lr.height > 0) {
                  var x0 = Math.min(tr[0], lr.left + sx), y0 = Math.min(tr[1], lr.top + sy);
                  var x1 = Math.max(tr[0] + tr[2], lr.right + sx), y1 = Math.max(tr[1] + tr[3], lr.bottom + sy);
                  tr = [r2(x0), r2(y0), r2(x1 - x0), r2(y1 - y0)]; labelled = 1;
                }
              });
            }
            var ctl = { n: n.i, k: kind, dis: dis, r: n.r, tr: tr, nm: nameApprox(el), ti: ti === null ? null : parseInt(ti, 10) };
            if (labelled) { ctl.lab = 1; }
            if ((modal && !modal.contains(el)) || (el.closest && el.closest('[inert]'))) { ctl.ine = 1; }
            if (isInlineInSentence(el, cs)) { ctl.inl = 1; }
            if (rect.width <= 1 || rect.height <= 1) { ctl.sr = 1; }
            if (tag === 'input' || tag === 'select' || tag === 'textarea') { ctl.hl = !!(el.labels && el.labels.length); }
            ctl.behind = backdrop(el.parentElement);
            ctl.own = backdrop(el);
            if (tag === 'textarea' || (tag === 'input' && /^(text|email|password|search|tel|url|number|date|datetime-local|month|week|time)$/.test(n.ty))) {
              ctl.fv = { c: rgba(cs.color), fs: px(cs.fontSize), fw: parseInt(cs.fontWeight, 10) || 400 };
              var phText = el.getAttribute('placeholder');
              if (phText && phText.trim()) {
                var pcs = gcs(el, '::placeholder');
                ctl.ph = { c: rgba(pcs.color), op: isNaN(parseFloat(pcs.opacity)) ? 1 : parseFloat(pcs.opacity), t: cleanText(phText, 30) };
              }
            }
            var hasText = cleanText(el.textContent || '', 4).length > 0 || tag === 'input' || tag === 'select' || tag === 'textarea';
            if (!hasText) {
              var icon = el.querySelector('svg,img,i,[class*=icon]');
              if (icon) {
                var shape = icon.localName === 'svg' ? (icon.querySelector('path,circle,rect,line,polyline,polygon,ellipse') || icon) : icon;
                var ics = gcs(shape), strokeC = rgba(ics.stroke), fillC = rgba(ics.fill);
                var cc = (strokeC && strokeC[3] > 0 && ics.stroke !== 'none' && (px(ics.strokeWidth) || 0) > 0) ? strokeC
                  : ((fillC && fillC[3] > 0 && ics.fill !== 'none') ? fillC : rgba(cs.color));
                ctl.ico = { c: cc, tag: icon.localName };
              }
            }
            if (authored(el, 'height') !== null) { ctl.ha = authored(el, 'height') ? 1 : 0; ctl.wa = authored(el, 'width') ? 1 : 0; }
            controls.push(ctl);
          } else if (tag !== 'label') {
            // explicit click handlers or cursor:pointer on non-semantic elements
            var parentPointer = el.parentElement && gcs(el.parentElement).cursor === 'pointer';
            var via = (typeof el.onclick === 'function' || el.hasAttribute('onclick')) ? 'onclick' : ((cs.cursor === 'pointer' && !parentPointer) ? 'cursor' : null);
            if (via && !n.role && !(el.closest && el.parentElement && el.parentElement.closest(INTERACTIVE_ANCESTOR))) {
              clicks.push({ n: n.i, via: via, ti: ti !== null, attr: el.hasAttribute('onclick'), cur: cs.cursor === 'pointer' && !parentPointer, fd: !!el.querySelector(FOCUSABLE_INSIDE) });
            }
          }
        }
        if (tag === 'svg' || tag === 'select' || tag === 'object' || tag === 'iframe' || tag === 'canvas' || tag === 'video' || tag === 'textarea') { continue; }
        if (tag === 'details' && !el.open) {
          var summary = el.querySelector(':scope > summary');
          if (summary) { stack.push([summary, idx, depth + 1, 0, 0]); }
          continue;
        }
        var kids = el.children, counts = {}, k;
        for (k = 0; k < kids.length; k++) { counts[kids[k].localName] = (counts[kids[k].localName] || 0) + 1; }
        var seen = {};
        var batch = [];
        for (k = 0; k < kids.length; k++) {
          var kt = kids[k].localName;
          seen[kt] = (seen[kt] || 0) + 1;
          batch.push([kids[k], idx, depth + 1, seen[kt], counts[kt]]);
        }
        for (k = batch.length - 1; k >= 0; k--) { stack.push(batch[k]); }
      }
    } catch (e) { res.errors.push('walk: ' + (e && e.stack ? String(e.stack).split('\n').slice(0, 2).join(' | ') : e)); }

    res.nodes = nodes; res.texts = texts; res.controls = controls; res.images = images;
    res.headings = headings; res.tables = tables; res.clicks = clicks;
    res.truncated = truncated; res.sr_only = srOnly; res.offscreen = offscreen;
    res.facts.ignored = ignoredEls; res.facts.ignored_roots = ignoredRoots;
    res.facts.ignored_right = ignoredRight === null ? null : r2(ignoredRight);
    U._els = els; U._ctl = controls.map(function (c) { return els[c.n]; });

    try { if (opts.probe !== false) { probeControls(controls, els); } } catch (e) { res.errors.push('probe: ' + e); }

    // fonts
    try {
      var stacks = Object.keys(fontsUsed).map(function (stack) {
        var fam = stack.split(',').map(function (f) { return f.trim().replace(/^["']|["']$/g, ''); }).filter(Boolean);
        var avail = fam.map(function (f) { return familyAvailable(f); });
        return { stack: stack, chars: fontsUsed[stack], fam: fam.slice(0, 8), avail: avail.slice(0, 8) };
      });
      var faces = document.fonts ? Array.from(document.fonts).map(function (f) {
        return { family: f.family.replace(/^["']|["']$/g, ''), status: f.status, weight: f.weight, style: f.style };
      }).slice(0, 60) : [];
      res.fonts = { used: stacks.sort(function (a, b) { return b.chars - a.chars; }).slice(0, 12), faces: faces };
    } catch (e) { res.errors.push('fonts: ' + e); res.fonts = { used: [], faces: [] }; }

    // page overflow after layout settled
    try {
      var de = document.documentElement;
      res.facts.scrollWidth_after = de.scrollWidth; res.facts.scrollHeight_after = de.scrollHeight;
      res.facts.scroll_y_after = window.pageYOffset;
    } catch (e) { /* ignore */ }
    res.ms = Math.round(performance.now() - t0);
    return res;
  };

  /* ------------------------------------------------------------------ focus */
  var FOCUSABLE = 'a[href],area[href],button:not([disabled]),input:not([disabled]):not([type=hidden]),select:not([disabled]),' +
    'textarea:not([disabled]),summary,iframe,[tabindex],[contenteditable=""],[contenteditable="true"]';

  function ringSnap(el) {
    var cs = gcs(el);
    var o = { o: [cs.outlineStyle, px(cs.outlineWidth) || 0, rgba(cs.outlineColor), px(cs.outlineOffset) || 0],
      sh: parseShadows(cs.boxShadow), bg: rgba(cs.backgroundColor), fg: rgba(cs.color),
      td: cs.textDecorationLine, tf: cs.transform, f: cs.filter, op: parseFloat(cs.opacity),
      bgi: cs.backgroundImage !== 'none' ? 1 : 0 };
    var bw = [px(cs.borderTopWidth) || 0, px(cs.borderRightWidth) || 0, px(cs.borderBottomWidth) || 0, px(cs.borderLeftWidth) || 0];
    o.bw = bw;
    o.bc = [rgba(cs.borderTopColor), rgba(cs.borderRightColor), rgba(cs.borderBottomColor), rgba(cs.borderLeftColor)];
    return o;
  }

  function ancestorsOf(el, max) {
    var out = [], p = el.parentElement;
    while (p && p !== document.body && p !== document.documentElement && out.length < max) { out.push(p); p = p.parentElement; }
    return out;
  }

  function modalRoot() {
    try { return document.querySelector(':modal'); } catch (e) { return null; }
  }

  U.focusables = function () {
    opMemo = new Map(); bgMemo = new Map();
    // Snapshot the unfocused look: a control that was already focused (autofocus, a dialog, a click) would hide its own focus ring.
    var active = document.activeElement;
    if (active && active !== document.body && active.blur) { active.blur(); }
    var list = [];
    var modal = modalRoot();
    document.querySelectorAll(FOCUSABLE).forEach(function (el) {
      var ti = el.getAttribute('tabindex');
      if (ti !== null && parseInt(ti, 10) < 0) { return; }
      if (el.disabled || isIgnored(el) || (el.closest && el.closest('[inert]'))) { return; }
      if (modal && !modal.contains(el)) { return; }
      var cs = gcs(el);
      if (cs.display === 'none' || cs.visibility === 'hidden') { return; }
      var r = el.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) { return; }
      list.push(el);
    });
    U._f = list.map(function (el) {
      return { el: el, base: ringSnap(el), anc: ancestorsOf(el, 3).map(function (a) { return { el: a, base: ringSnap(a) }; }) };
    });
    return U._f.length;
  };

  // After a click, Chrome starts Tab from the clicked element. Focusing the last control and tabbing once wraps to the document start.
  U.focusLast = function () {
    var f = U._f || [];
    if (!f.length) { return false; }
    var el = f[f.length - 1].el;
    try { el.focus({ preventScroll: true }); } catch (e) { el.focus(); }
    return document.activeElement === el;
  };

  U.focusNow = function () {
    var a = document.activeElement;
    if (!a || a === document.body || a === document.documentElement) { return null; }
    if (isIgnored(a)) { return { skip: 1 }; }
    var idx = -1, f = U._f || [];
    for (var i = 0; i < f.length; i++) { if (f[i].el === a) { idx = i; break; } }
    var entry = idx >= 0 ? f[idx] : null;
    var r = a.getBoundingClientRect();
    var out = { i: idx, t: a.localName, s: shortSel(a), ti: a.tabIndex, fv: a.matches(':focus-visible'),
      r: [r2(r.left + window.pageXOffset), r2(r.top + window.pageYOffset), r2(r.width), r2(r.height)],
      cur: ringSnap(a), base: entry ? entry.base : null, anc: [],
      behind: backdrop(a.parentElement), own: backdrop(a),
      txt: cleanText(a.textContent || a.getAttribute('aria-label') || '', 40) };
    if (entry) {
      out.anc = entry.anc.map(function (x) { return { s: shortSel(x.el), base: x.base, cur: ringSnap(x.el), behind: backdrop(x.el.parentElement) }; });
    }
    return out;
  };

  // Style snapshot of the first element matching a selector, used for forced pseudo-states.
  U.snap = function (selector) {
    try {
      var all = document.querySelectorAll(selector);
      if (!all.length) { return { found: 0 }; }
      var el = all[0];
      el.scrollIntoView({ block: 'center', inline: 'nearest' });
      var r = el.getBoundingClientRect(), cs = gcs(el);
      return { found: all.length, s: shortSel(el), vis: visible(el),
        r: [r2(r.left + window.pageXOffset), r2(r.top + window.pageYOffset), r2(r.width), r2(r.height)],
        snap: ringSnap(el), cursor: cs.cursor, fs: px(cs.fontSize), fw: parseInt(cs.fontWeight, 10) || 400,
        behind: backdrop(el.parentElement), own: backdrop(el) };
    } catch (e) { return { found: 0, error: String(e && e.message || e).slice(0, 120) }; }
  };

  U.center = function (selector) {
    try {
      var el = document.querySelector(selector);
      if (!el) { return null; }
      el.scrollIntoView({ block: 'center', inline: 'nearest' });
      var r = el.getBoundingClientRect();
      return { x: r.left + r.width / 2, y: r.top + r.height / 2, w: r.width, h: r.height };
    } catch (e) { return null; }
  };

  U.focusSel = function (selector) {
    try { var el = document.querySelector(selector); if (!el) { return false; } el.focus(); return document.activeElement === el; } catch (e) { return false; }
  };

  /* ------------------------------------------------------------------ tokens */
  // Custom properties declared anywhere in the document's style sheets, with resolved root values.
  U.tokens = function () {
    var names = {};
    var scan = function (rules, depth) {
      if (!rules || depth > 6) { return; }
      for (var i = 0; i < rules.length; i++) {
        var r = rules[i];
        if (r.style && r.type === 1) {
          for (var k = 0; k < r.style.length; k++) { if (r.style[k].indexOf('--') === 0) { names[r.style[k]] = true; } }
        }
        if (r.cssRules) { scan(r.cssRules, depth + 1); }
      }
    };
    var sheets = Array.prototype.slice.call(document.styleSheets).concat(Array.prototype.slice.call(document.adoptedStyleSheets || []));
    sheets.forEach(function (sheet) { try { scan(sheet.cssRules, 0); } catch (e) { /* cross-origin sheet */ } });
    var cs = gcs(document.documentElement), out = {};
    Object.keys(names).slice(0, 600).forEach(function (nm) {
      var v = cs.getPropertyValue(nm).trim();
      if (v) { out[nm] = v.slice(0, 80); }
    });
    return { props: out, root_font_size: px(cs.fontSize) };
  };

  return true;
})();
