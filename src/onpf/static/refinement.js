(() => {
  for (const input of document.querySelectorAll('.response-filter')) {
    const target = document.getElementById(input.dataset.target);
    if (!target) continue;
    const update = () => {
      const query = input.value.trim().toLocaleLowerCase();
      for (const choice of target.querySelectorAll('.response-choice')) {
        choice.hidden = Boolean(query) && !choice.dataset.search.includes(query);
      }
    };
    input.addEventListener('input', update);
    update();
  }

  const proposalChecks = [...document.querySelectorAll('.decision-proposal')];
  const proposalGroups = [...document.querySelectorAll('.proposal-evidence')];
  if (proposalChecks.length) {
    for (const checkbox of proposalChecks) {
      checkbox.addEventListener('change', () => {
        const group = proposalGroups.find(item => item.dataset.proposalId === checkbox.value);
        if (group) group.open = checkbox.checked;
      });
    }
  }

  const list = document.getElementById('coverage-list');
  if (!list) return;
  const search = document.getElementById('coverage-filter');
  const status = document.getElementById('coverage-status');
  const sort = document.getElementById('coverage-sort');
  const count = document.getElementById('coverage-count');
  const cards = [...list.querySelectorAll('.coverage-response')];
  const update = () => {
    const query = search.value.trim().toLocaleLowerCase();
    for (const card of cards) {
      card.hidden = (Boolean(query) && !card.dataset.responseSearch.includes(query)) ||
        (Boolean(status.value) && card.dataset.responseStatus !== status.value);
    }
    const ordered = [...cards].sort((a, b) => {
      if (sort.value === 'newest') return Number(b.dataset.responseNumber) - Number(a.dataset.responseNumber);
      if (sort.value === 'status') return a.dataset.responseStatus.localeCompare(b.dataset.responseStatus) ||
        Number(a.dataset.responseNumber) - Number(b.dataset.responseNumber);
      if (sort.value === 'question') return a.dataset.responseQuestion.localeCompare(b.dataset.responseQuestion) ||
        Number(a.dataset.responseNumber) - Number(b.dataset.responseNumber);
      return Number(a.dataset.responseNumber) - Number(b.dataset.responseNumber);
    });
    for (const card of ordered) list.append(card);
    count.textContent = `${cards.filter(card => !card.hidden).length} of ${cards.length} responses shown`;
  };
  search.addEventListener('input', update);
  status.addEventListener('change', update);
  sort.addEventListener('change', update);
  document.getElementById('coverage-clear').addEventListener('click', () => {
    search.value = '';
    status.value = '';
    sort.value = 'oldest';
    update();
  });
  update();
})();
