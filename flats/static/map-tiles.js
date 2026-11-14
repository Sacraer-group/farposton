window.FlatsMapTiles = () => {
  const key = document.querySelector('meta[name="2gis-map-key"]')?.content.trim();
  if (!key || !window.L) return null;
  return L.tileLayer(
    `https://tile0.maps.2gis.com/v2/tiles/online_sd/{z}/{x}/{y}.png?key=${encodeURIComponent(key)}`,
    {
      maxZoom: 19,
      attribution: '&copy; <a href="https://2gis.ru/" target="_blank" rel="noopener noreferrer">2ГИС</a>',
    },
  );
};
