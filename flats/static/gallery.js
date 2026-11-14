(() => {
  document.querySelectorAll('[data-gallery]').forEach(gallery => {
    const photos = JSON.parse(gallery.dataset.photos);
    const image = gallery.querySelector('[data-gallery-image]');
    const error = gallery.querySelector('[data-gallery-error]');
    let index = Number(gallery.dataset.index);
    const prev = gallery.querySelector('[data-gallery-prev]');
    const next = gallery.querySelector('[data-gallery-next]');
    const count = gallery.querySelector('[data-gallery-count]');
    const unavailable = () => { image.hidden = true; error.hidden = false; };
    image.addEventListener('error', unavailable);
    image.addEventListener('load', () => { image.hidden = false; error.hidden = true; });
    if (image.complete && image.currentSrc && !image.naturalWidth) unavailable();
    if (!prev || !next) return;
    function show(offset) {
      index = (index + offset + photos.length) % photos.length;
      image.hidden = false; error.hidden = true;
      image.src = photos[index];
      count.textContent = `${index + 1} / ${photos.length}`;
    }
    prev.addEventListener('click', () => show(-1));
    next.addEventListener('click', () => show(1));
    count.textContent = `${index + 1} / ${photos.length}`;
    prev.hidden = next.hidden = count.hidden = false;
  });
})();
