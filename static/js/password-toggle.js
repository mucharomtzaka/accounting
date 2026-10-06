/* Toggle mata untuk field password (login admin, dsb).
   Tanpa dependency: tombol + ikon SVG ditulis di JS, field yang sudah ada
   dibungkus tanpa mengubah markup form. Label diambil dari data-* di tag
   <script> supaya tetap ikut terjemahan template. */
(function () {
  'use strict';

  var script = document.querySelector('script[data-password-toggle]');
  var showLabel = (script && script.getAttribute('data-show')) || 'Show password';
  var hideLabel = (script && script.getAttribute('data-hide')) || 'Hide password';

  var EYE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z"/><circle cx="12" cy="12" r="3"/></svg>';
  var EYE_OFF = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17.9 17.9A10 10 0 0 1 12 20c-7 0-11-8-11-8a18.5 18.5 0 0 1 5.1-5.9"/><path d="M9.9 4.2A9 9 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.2 3.2"/><line x1="1" y1="1" x2="23" y2="23"/></svg>';

  function attach(input) {
    if (input.dataset.passwordToggle === 'on') return;
    input.dataset.passwordToggle = 'on';

    var wrap = document.createElement('span');
    wrap.className = 'password-toggle-wrap';
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);

    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'password-toggle';
    button.innerHTML = EYE;
    button.setAttribute('aria-label', showLabel);
    button.setAttribute('aria-pressed', 'false');
    wrap.appendChild(button);

    button.addEventListener('click', function () {
      var revealed = input.type === 'text';
      input.type = revealed ? 'password' : 'text';
      button.innerHTML = revealed ? EYE : EYE_OFF;
      button.setAttribute('aria-pressed', String(!revealed));
      button.setAttribute('aria-label', revealed ? showLabel : hideLabel);
      input.focus();
    });
  }

  var inputs = document.querySelectorAll('input[type="password"]');
  Array.prototype.forEach.call(inputs, attach);
})();
