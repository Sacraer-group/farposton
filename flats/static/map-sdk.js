window.FlatsMap = (container, center, report, zoom = 14) => {
  const key = document.querySelector('meta[name="2gis-map-key"]')?.content.trim();
  const update = state => { container.dataset.mapState = state; report(state); };
  if (!key || !window.mapgl) { update("error"); return null; }
  try {
    update("loading");
    const map = new window.mapgl.Map(container, {
      key, center, zoom, lang: "ru",
      zoomControl: "bottomRight",
      disableZoomOnScroll: true,
    });
    const timeout = setTimeout(() => update("error"), 15000);
    map.on("idle", () => { clearTimeout(timeout); update("ready"); });
    map.on("styleloaderror", () => { clearTimeout(timeout); update("error"); });
    map.on("destroy", () => clearTimeout(timeout));
    return map;
  } catch {
    update("error");
    return null;
  }
};
