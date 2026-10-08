/* Progressive enhancement: browser submissions retain the same CSRF and server rules. */
document.querySelectorAll('[data-drafting-form]').forEach((form) => {
  form.addEventListener('submit', (event) => {
    if (event.submitter?.value !== 'generate') return;
    let status = form.querySelector('[data-generation-status]');
    if (!status) {
      status = document.createElement('p');
      status.setAttribute('role', 'status');
      status.dataset.generationStatus = '';
      event.submitter.after(status);
    }
    status.textContent = 'Generating an unsaved suggestion. Your current wording is included.';
    form.setAttribute('aria-busy', 'true');
  });
});
document.querySelector('[data-drafting-status]')?.focus();
document.querySelectorAll('[data-evidence-filter]').forEach((input) => {
  const list = document.querySelector('[data-evidence-list]');
  input.addEventListener('input', () => {
    const query = input.value.trim().toLocaleLowerCase();
    list.querySelectorAll('[data-evidence-item]').forEach((item) => {
      item.hidden = Boolean(query) && !item.textContent.toLocaleLowerCase().includes(query);
    });
  });
});
