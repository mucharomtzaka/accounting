/* Print & export Excel (CSV) untuk halaman report.
   Export dibaca dari DOM, bukan dari spec per view: angka yang diunduh persis
   sama dengan yang tampil di layar, dan modul report baru otomatis ikut
   tanpa kode tambahan. Pemisah ';' + BOM UTF-8 supaya Excel (ID) membuka
   angka "1.000.000,00" sebagai teks, bukan tanggal. */
(function () {
  'use strict';

  var body = document.getElementById('report-body');
  if (!body) return;

  function text(node) {
    return (node.innerText || node.textContent || '').replace(/\s+/g, ' ').trim();
  }

  function quote(value) {
    var v = value === null || value === undefined ? '' : String(value);
    return /[";\n\r]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
  }

  function tableRows(table) {
    var rows = [];
    Array.prototype.forEach.call(table.querySelectorAll('tr'), function (tr) {
      var cells = tr.querySelectorAll('th,td');
      if (!cells.length) return;
      rows.push(Array.prototype.map.call(cells, text));
    });
    return rows;
  }

  function headingBefore(table) {
    var node = table.previousElementSibling;
    while (node && !/^H[1-4]$/.test(node.tagName)) node = node.previousElementSibling;
    return node ? text(node) : '';
  }

  function headerLines() {
    var header = document.getElementById('report-doc-header');
    if (!header) return [];
    var keys = ['company', 'company-name', 'company-nib', 'company-currency',
                'period', 'user', 'generated'];
    var lines = [];
    keys.forEach(function (key) {
      var value = header.getAttribute('data-' + key);
      if (value) lines.push([key, value]);
    });
    lines.push([]);
    return lines;
  }

  function buildCsv() {
    var lines = headerLines();
    Array.prototype.forEach.call(body.querySelectorAll('table'), function (table) {
      var label = headingBefore(table);
      if (label) lines.push([label]);
      tableRows(table).forEach(function (row) { lines.push(row); });
      lines.push([]);
    });
    return lines.map(function (row) { return row.map(quote).join(';'); }).join('\r\n');
  }

  function slug(value) {
    return String(value || 'report').toLowerCase()
      .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'report';
  }

  function download() {
    var header = document.getElementById('report-doc-header');
    var stamp = new Date().toISOString().slice(0, 10);
    var name = ['report', slug(location.pathname.split('/').pop()),
                header ? slug(header.getAttribute('data-company')) : '', stamp]
               .filter(Boolean).join('-') + '.csv';
    var blob = new Blob(['\ufeff' + buildCsv()], { type: 'text/csv;charset=utf-8;' });
    var link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = name;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(link.href);
  }

  var exportBtn = document.querySelector('.report-export-btn');
  if (exportBtn) {
    if (!body.querySelector('table')) {
      exportBtn.hidden = true; // cash flow & friends: tidak ada tabel, tak ada yang diekspor
    } else {
      exportBtn.addEventListener('click', download);
    }
  }

  var printBtn = document.querySelector('.report-print-btn');
  if (printBtn) printBtn.addEventListener('click', function () { window.print(); });
})();
