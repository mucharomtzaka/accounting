/* Isi form login dari kartu akun demo di halaman login admin.
   Password diambil dari data-password kartu, jadi daftar akun cukup ditulis
   sekali di template. Tanpa dependency. */
(function () {
  'use strict';

  var form = document.querySelector('#login-form');
  if (!form) return;

  var username = form.querySelector('input[name="username"]');
  var password = form.querySelector('input[name="password"]');
  if (!username || !password) return;

  Array.prototype.forEach.call(document.querySelectorAll('[data-demo-user]'), function (card) {
    card.addEventListener('click', function (event) {
      event.preventDefault();
      username.value = card.getAttribute('data-demo-user');
      password.value = card.getAttribute('data-password') || '';
      username.dispatchEvent(new Event('input', { bubbles: true }));
      password.dispatchEvent(new Event('input', { bubbles: true }));
      username.focus();
      var submit = form.querySelector('input[type="submit"], button[type="submit"]');
      if (submit) submit.focus();
    });
  });
})();
