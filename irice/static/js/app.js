'use strict';
(() => {
  const toggle = document.querySelector('[data-toggle-nav]');
  const closeNav = () => { document.body.classList.remove('nav-open'); toggle?.setAttribute('aria-expanded', 'false'); };
  toggle?.addEventListener('click', () => { const opened = document.body.classList.toggle('nav-open'); toggle.setAttribute('aria-expanded', String(opened)); });
  document.querySelector('[data-close-nav]')?.addEventListener('click', closeNav);
  document.addEventListener('keydown', event => { if (event.key === 'Escape') closeNav(); });
  document.querySelectorAll('[data-dismiss-alert]').forEach(button => button.addEventListener('click', () => button.closest('.alert').remove()));
  document.querySelectorAll('[data-confirm]').forEach(form => form.addEventListener('submit', event => { if (!window.confirm(form.dataset.confirm)) event.preventDefault(); }));

})();
