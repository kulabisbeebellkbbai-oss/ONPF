'use strict';
// A submit-button fallback adds rows without JavaScript and preserves all fields.
document.querySelectorAll('.add-response').forEach(button => {
  button.addEventListener('click', event => {
    const section = button.closest('.question-responses');
    const existing = section.querySelector('.response-row');
    // Let the native submit fallback work where secure-context UUIDs are absent.
    if (!existing || !globalThis.crypto || typeof globalThis.crypto.randomUUID !== 'function') return;
    event.preventDefault();
    if (document.querySelectorAll('.response-row').length >= 500) {
      document.querySelector('#response-add-status').textContent = 'A submission may contain up to 500 responses.';
      return;
    }
    const row = existing.cloneNode(true);
    const oldId = existing.querySelector('[name="row_id"]').value;
    const newId = 'r_' + crypto.randomUUID().replaceAll('-', '');
    row.querySelectorAll('[id], [for], [aria-describedby], [name]').forEach(element => {
      ['id', 'for', 'aria-describedby', 'name'].forEach(attribute => {
        const value = element.getAttribute(attribute);
        if (value && value.endsWith('_' + oldId)) element.setAttribute(attribute, value.slice(0, -oldId.length) + newId);
      });
    });
    row.querySelector('[name="row_id"]').value = newId;
    row.querySelector('textarea').value = '';
    row.querySelector('[name="display_name_' + newId + '"]').value = '';
    row.querySelectorAll('select').forEach(select => { select.selectedIndex = 0; });
    const count = section.querySelectorAll('.response-row').length + 1;
    row.querySelector('legend').textContent = 'Response ' + count;
    section.querySelector('.response-rows').appendChild(row);
    row.querySelector('textarea').focus();
    document.querySelector('#response-add-status').textContent = 'Another response added to this question. Focus is in its text field.';
  });
});
