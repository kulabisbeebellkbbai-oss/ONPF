(() => {
  const tour = document.querySelector('[data-tour]');
  if (!tour) return;
  const slides = Array.from(tour.querySelectorAll('.tour-slide'));
  const previous = tour.querySelector('[data-tour-prev]');
  const next = tour.querySelector('[data-tour-next]');
  const progress = tour.querySelector('[data-tour-progress]');
  let current = Math.max(0, slides.findIndex(slide => '#' + slide.id === location.hash));
  const show = (number, focus = false) => {
    current = Math.max(0, Math.min(number, slides.length - 1));
    slides.forEach((slide, index) => { slide.hidden = index !== current; });
    previous.disabled = current === 0;
    next.disabled = current === slides.length - 1;
    progress.textContent = `Slide ${current + 1} of ${slides.length}`;
    history.replaceState(null, '', '#' + slides[current].id);
    if (focus) { slides[current].setAttribute('tabindex', '-1'); slides[current].focus(); }
  };
  previous.addEventListener('click', () => show(current - 1, true));
  next.addEventListener('click', () => show(current + 1, true));
  window.addEventListener('hashchange', () => {
    const index = slides.findIndex(slide => '#' + slide.id === location.hash);
    if (index >= 0) show(index, true);
  });
  tour.addEventListener('keydown', event => {
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(event.target.tagName)) return;
    if (event.key === 'ArrowRight' && current < slides.length - 1) { event.preventDefault(); show(current + 1, true); }
    if (event.key === 'ArrowLeft' && current > 0) { event.preventDefault(); show(current - 1, true); }
  });
  show(current);
})();
