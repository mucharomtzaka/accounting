// Drawer user (Ganti sandi / Keluar) di layar kecil: toggle, tutup via
// backdrop, tombol X, atau Escape.
(function () {
  var btn = document.querySelector('.user-tools-toggle');
  var drawer = document.getElementById('user-links');
  var backdrop = document.querySelector('.user-drawer-backdrop');
  var closeBtn = document.querySelector('.user-drawer-close');
  if (!btn || !drawer) return;

  function setOpen(open) {
    drawer.classList.toggle('is-open', open);
    btn.classList.toggle('is-open', open);
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (backdrop) backdrop.hidden = !open;
  }

  btn.addEventListener('click', function () {
    setOpen(!drawer.classList.contains('is-open'));
  });
  if (backdrop) backdrop.addEventListener('click', function () { setOpen(false); });
  if (closeBtn) closeBtn.addEventListener('click', function () { setOpen(false); });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && drawer.classList.contains('is-open')) setOpen(false);
  });
})();

// Filter kartu menu client-side. NOL dep, pointer-events aman (no overlay traps).
// Markup cukup: input.menu-search-input di dalam .menu-search, item memiliki data-search.
(function () {
  var root = document.querySelector('.menu-search');
  if (!root) return;
  var input = root.querySelector('.menu-search-input');
  var empty = root.querySelector('.menu-search-empty');
  // index.html memakai .app-card, app_index.html memakai .nav-card di dalam .app-card-grid
  var items = Array.prototype.slice.call(
    document.querySelectorAll('.app-cards .app-card, .app-card-grid .nav-card')
  );
  if (!items.length) return;

  var norm = function (s) {
    return (s || '').toLowerCase().trim();
  };

  input.addEventListener('input', function () {
    var q = norm(input.value);
    var shown = 0;
    items.forEach(function (el) {
      var hay = norm(el.getAttribute('data-search'));
      var match = !q || hay.indexOf(q) !== -1;
      el.hidden = !match;
      if (match) shown += 1;
    });
    if (empty) empty.hidden = shown > 0;
  });
})();
