// AcarPlus app family: shared page behaviour. Same file in every app's site/ folder.
// <body data-app="metriq" data-repo="AcarPlus/metriq" data-title-tr="…" data-title-en="…">
(function () {
  var d = document, h = d.documentElement, b = d.body;
  var reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  var fine = window.matchMedia && matchMedia('(hover: hover) and (pointer: fine)').matches;

  // Language: the <head> snippet already picked one (?lang= > saved > browser); the button flips it.
  // All family sites live on the same origin, so the choice carries over between them.
  var langBtn = d.getElementById('dil');
  function setLang(l, save) {
    h.lang = l;
    if (langBtn) {
      langBtn.textContent = l === 'tr' ? 'EN' : 'TR';
      langBtn.setAttribute('aria-label', l === 'tr' ? 'Switch to English' : 'Türkçeye geç');
    }
    var t = b.getAttribute('data-title-' + l);
    if (t) d.title = t;
    if (save) { try { localStorage.setItem('dil', l); } catch (e) {} }
    d.dispatchEvent(new CustomEvent('dil', { detail: l }));
  }
  setLang(h.lang === 'en' ? 'en' : 'tr', false);
  if (langBtn) langBtn.addEventListener('click', function () { setLang(h.lang === 'tr' ? 'en' : 'tr', true); });

  // Sticky bar gets its glass background once the page scrolls.
  var nav = d.querySelector('.nav');
  function onScroll() { if (nav) nav.classList.toggle('stuck', window.scrollY > 8); }
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();

  // Family cards: the other apps, rendered from one list so every site shows the same thing.
  var APPS = [
    { id: 'metriq', name: 'metriQ', url: 'https://acarplus.github.io/metriq/', c1: '#4fe3cf', c2: '#7aa8ff',
      glyph: '<path d="M4 16.5a8 8 0 0 1 16 0"/><path d="M12 16.5l4.2-5"/>',
      tr: 'Her zaman üstte duran sensör paneli', en: 'Always-on-top sensor dashboard' },
    { id: 'surround', name: 'Surround', url: 'https://acarplus.github.io/surround/', c1: '#8ea2ff', c2: '#5cc8f2',
      glyph: '<circle cx="12" cy="12" r="2.4" fill="currentColor" stroke="none"/><path d="M8 8a5.7 5.7 0 0 0 0 8M16 8a5.7 5.7 0 0 1 0 8M5 5a10 10 0 0 0 0 14M19 5a10 10 0 0 1 0 14"/>',
      tr: 'Tüm hoparlörler tek çevresel ses sistemi', en: 'Every speaker, one surround system' },
    { id: 'stremio-offline', name: 'Stremio Offline', url: 'https://acarplus.github.io/stremio-offline/', c1: '#22c55e', c2: '#8b7cf6',
      glyph: '<path d="M12 4v11m-4.5-4.5L12 15l4.5-4.5M6 20h12"/>',
      tr: "Stremio'yu internetsiz izle", en: 'Watch Stremio offline' }
  ];
  var fam = d.getElementById('aile');
  if (fam) {
    fam.innerHTML = APPS.filter(function (a) { return a.id !== b.getAttribute('data-app'); }).map(function (a) {
      return '<a class="card fam" href="' + a.url + '" style="--c1:' + a.c1 + ';--c2:' + a.c2 + '">' +
        '<span class="mark"><svg viewBox="0 0 24 24" aria-hidden="true">' + a.glyph + '</svg></span>' +
        '<span><b>' + a.name + '</b><small data-tr>' + a.tr + '</small><small data-en>' + a.en + '</small></span>' +
        '<span class="go"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14m-5-5 5 5-5 5"/></svg></span></a>';
    }).join('');
  }

  // Scroll reveal with a small stagger among siblings.
  var rv = d.querySelectorAll('.rv');
  if ('IntersectionObserver' in window && !reduce) {
    rv.forEach(function (el) {
      var sib = Array.prototype.filter.call(el.parentNode.children, function (c) { return c.classList.contains('rv'); });
      el.style.transitionDelay = Math.min(sib.indexOf(el), 5) * 70 + 'ms';
    });
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } });
    }, { rootMargin: '0px 0px -6% 0px', threshold: 0.08 });
    rv.forEach(function (el) { io.observe(el); });
  } else {
    rv.forEach(function (el) { el.classList.add('in'); });
  }

  // Cursor spotlight on cards.
  if (fine) {
    d.addEventListener('pointermove', function (e) {
      var c = e.target.closest && e.target.closest('.card');
      if (!c) return;
      var r = c.getBoundingClientRect();
      c.style.setProperty('--mx', (e.clientX - r.left) + 'px');
      c.style.setProperty('--my', (e.clientY - r.top) + 'px');
    }, { passive: true });
  }

  // Gentle 3D tilt of the hero scene.
  var stage = d.querySelector('.stage'), win = stage && stage.querySelector('.win');
  if (stage && win && fine && !reduce) {
    stage.addEventListener('pointermove', function (e) {
      var r = stage.getBoundingClientRect();
      var x = (e.clientX - r.left) / r.width - 0.5, y = (e.clientY - r.top) / r.height - 0.5;
      win.style.transform = 'rotateY(' + (x * 7).toFixed(2) + 'deg) rotateX(' + (-y * 6).toFixed(2) + 'deg)';
    });
    stage.addEventListener('pointerleave', function () { win.style.transform = ''; });
  }

  // Latest release: fill [data-ver] with the tag, point <a data-asset="regex"> at the matching file,
  // and write the file size into any other [data-asset] element. Links already work without this.
  var repo = b.getAttribute('data-repo');
  if (repo && window.fetch) {
    fetch('https://api.github.com/repos/' + repo + '/releases/latest')
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (rel) {
        if (!rel || !rel.tag_name) return;
        var assets = rel.assets || [];
        d.querySelectorAll('[data-ver]').forEach(function (el) { el.textContent = 'v' + rel.tag_name.replace(/^v/, ''); });
        d.querySelectorAll('[data-asset]').forEach(function (el) {
          var re = new RegExp(el.getAttribute('data-asset'), 'i');
          var f = assets.filter(function (a) { return re.test(a.name); })[0];
          if (!f) return;
          if (el.tagName === 'A') el.href = f.browser_download_url;
          else el.textContent = Math.max(1, Math.round(f.size / 1048576)) + ' MB';
          var hid = el.closest('[hidden]');
          if (hid) hid.hidden = false;
        });
      })
      .catch(function () {});
  }

  var y = d.getElementById('yil');
  if (y) y.textContent = new Date().getFullYear();
})();
