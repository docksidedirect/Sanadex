/* Sanadex.wanis.online — UI behaviour: rail, drawer, palette, filters. */
(function () {
  'use strict';

  var doc = document;
  var body = doc.body;
  var KEY_RAIL = 'sanadex.rail';

  /* ---------------------------------------------------------- sidebar rail */
  function applyRail(collapsed) {
    body.classList.toggle('sb-collapsed', collapsed);
    try { localStorage.setItem(KEY_RAIL, collapsed ? '1' : '0'); } catch (e) {}
  }
  try {
    if (localStorage.getItem(KEY_RAIL) === '1') body.classList.add('sb-collapsed');
  } catch (e) {}

  doc.addEventListener('click', function (event) {
    var toggle = event.target.closest('[data-toggle-sidebar]');
    if (toggle) {
      event.preventDefault();
      applyRail(!body.classList.contains('sb-collapsed'));
      return;
    }
    if (event.target.closest('[data-open-nav]')) {
      body.classList.add('nav-open');
      return;
    }
    if (event.target.closest('[data-close-nav]')) {
      body.classList.remove('nav-open');
      return;
    }
    var close = event.target.closest('.flash .close');
    if (close) close.closest('.flash').remove();
  });

  doc.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') body.classList.remove('nav-open');
  });

  /* keep the current page visible in a long sidebar */
  var currentLink = doc.querySelector('.sb-nav .nav-link.active');
  if (currentLink) {
    var nav = doc.querySelector('.sb-nav');
    if (nav) {
      var top = currentLink.offsetTop - nav.clientHeight / 2;
      nav.scrollTop = Math.max(0, top);
    }
  }

  /* ---------------------------------------------------------- autosubmit */
  doc.addEventListener('change', function (event) {
    var el = event.target;
    if (el.matches('[data-autosubmit]')) {
      var form = el.form;
      if (form) form.submit();
    }
  });

  /* ---------------------------------------------------------- confirm */
  doc.addEventListener('submit', function (event) {
    var form = event.target;
    var message = form.getAttribute('data-confirm');
    if (message && !window.confirm(message)) {
      event.preventDefault();
      return;
    }
    var button = form.querySelector('button[type="submit"], button:not([type])');
    if (button && form.getAttribute('data-busy') !== 'off') {
      setTimeout(function () { button.disabled = true; }, 0);
    }
  });
  doc.addEventListener('click', function (event) {
    var link = event.target.closest('a[data-confirm]');
    if (link && !window.confirm(link.getAttribute('data-confirm'))) {
      event.preventDefault();
    }
  });

  /* ---------------------------------------------------------- table filter */
  doc.addEventListener('input', function (event) {
    var input = event.target;
    if (!input.matches('[data-filter]')) return;
    var scope = doc.querySelector(input.getAttribute('data-filter'));
    if (!scope) return;
    var term = input.value.trim().toLowerCase();
    var rows = scope.querySelectorAll('tbody tr');
    var shown = 0;
    rows.forEach(function (row) {
      if (row.querySelector('td[colspan]')) return;
      var match = !term || row.textContent.toLowerCase().indexOf(term) !== -1;
      row.hidden = !match;
      if (match) shown += 1;
    });
    var counter = doc.querySelector(input.getAttribute('data-counter') || '');
    if (counter) counter.textContent = shown;
  });

  /* ---------------------------------------------------------- number inputs */
  doc.addEventListener('focusin', function (event) {
    if (event.target.matches('input[type="number"]')) event.target.select();
  });

  /* ---------------------------------------------------------- print */
  doc.addEventListener('click', function (event) {
    var trigger = event.target.closest('[data-print]');
    if (trigger) {
      event.preventDefault();
      window.print();
    }
  });

  /* ---------------------------------------------------------- flash autohide */
  doc.querySelectorAll('.flash[data-auto]').forEach(function (flash) {
    setTimeout(function () {
      flash.style.transition = 'opacity .3s, transform .3s';
      flash.style.opacity = '0';
      flash.style.transform = 'translateY(-4px)';
      setTimeout(function () { flash.remove(); }, 320);
    }, 5200);
  });

  /* ---------------------------------------------------------- command palette */
  var cmd = doc.getElementById('cmd');
  if (!cmd) return;
  var input = cmd.querySelector('.cmd-input');
  var results = cmd.querySelector('.cmd-results');
  var lastFocus = null;
  var timer = null;
  var items = [];
  var selected = 0;
  var iconMap = { page: 'grid', item: 'box', party: 'users', invoice: 'file-text' };

  function escapeHtml(text) {
    return String(text || '').replace(/[&<>"]/g, function (ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch];
    });
  }

  function render(groups) {
    items = [];
    var html = '';
    groups.forEach(function (group) {
      if (!group.rows.length) return;
      html += '<div class="cmd-group">' + escapeHtml(group.title) + '</div>';
      group.rows.forEach(function (row) {
        var index = items.length;
        items.push(row);
        html += '<a class="cmd-item" data-index="' + index + '" href="' + escapeHtml(row.href) + '">' +
          '<svg class="ic"><use href="#i-' + (iconMap[group.key] || 'arrow-right') + '"></use></svg>' +
          '<span>' + escapeHtml(row.label) + '</span>' +
          (row.meta ? '<small>' + escapeHtml(row.meta) + '</small>' : '') + '</a>';
      });
    });
    if (!items.length) {
      html = '<div class="empty" style="margin:8px"><b>' +
        escapeHtml(cmd.getAttribute('data-empty') || 'No results') + '</b></div>';
    }
    results.innerHTML = html;
    selected = 0;
    highlight();
  }

  function highlight() {
    results.querySelectorAll('.cmd-item').forEach(function (node, index) {
      node.classList.toggle('is-sel', index === selected);
    });
  }

  function open() {
    lastFocus = doc.activeElement;
    cmd.hidden = false;
    input.value = '';
    render([]);
    setTimeout(function () { input.focus(); }, 10);
  }

  function close() {
    cmd.hidden = true;
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  doc.addEventListener('click', function (event) {
    if (event.target.closest('[data-open-cmd]')) {
      event.preventDefault();
      open();
    } else if (event.target.closest('.cmd-backdrop')) {
      close();
    }
  });

  doc.addEventListener('keydown', function (event) {
    var isPaletteKey = (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k';
    if (isPaletteKey) {
      event.preventDefault();
      if (cmd.hidden) { open(); } else { close(); }
      return;
    }
    if (cmd.hidden) return;
    if (event.key === 'Escape') { close(); return; }
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      selected = Math.min(selected + 1, items.length - 1);
      highlight();
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      selected = Math.max(selected - 1, 0);
      highlight();
    } else if (event.key === 'Enter') {
      var current = items[selected];
      if (current) { event.preventDefault(); window.location.href = current.href; }
    }
  });

  input.addEventListener('input', function () {
    var term = input.value.trim();
    clearTimeout(timer);
    if (!term) { render([]); return; }
    timer = setTimeout(function () {
      fetch(cmd.getAttribute('data-src') + '?q=' + encodeURIComponent(term), {
        headers: { 'Accept': 'application/json' }
      }).then(function (response) { return response.json(); }).then(function (data) {
        if (!data.ok) return;
        render([
          { key: 'page', title: cmd.getAttribute('data-pages') || 'Pages', rows: data.nav || [] },
          { key: 'item', title: cmd.getAttribute('data-items') || 'Items', rows: data.items || [] },
          { key: 'party', title: cmd.getAttribute('data-parties') || 'Customers & suppliers', rows: data.parties || [] },
          { key: 'invoice', title: cmd.getAttribute('data-invoices') || 'Invoices', rows: data.invoices || [] }
        ]);
      }).catch(function () { render([]); });
    }, 170);
  });


  /* ---------------------------------------------------------- helpers */
  window.Sana = {
    money: function (value) {
      var number = parseFloat(value) || 0;
      return number.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    },
    confirm: function (message) { return window.confirm(message); }
  };
})();
