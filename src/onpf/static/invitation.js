/* Fragment credentials never enter server URLs. Remove them before exchange. */
(() => {
  const code = window.location.hash.slice(1);
  if (!code) return;
  window.history.replaceState(null, '', window.location.pathname);
  const form = document.getElementById('invitation-access');
  if (!form || !/^[A-Za-z0-9_-]{20,256}$/.test(code)) return;
  form.elements.token.value = code;
  form.submit();
})();
