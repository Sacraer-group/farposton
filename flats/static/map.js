(() => {
  const maps = [];
  const cards = new Map([...document.querySelectorAll("[data-listing-key]")]
    .map(card => [card.dataset.listingKey, card]));
  function localUrl(value) {
    if (!value) return null;
    try {
      const url = new URL(value, location.href);
      return url.origin === location.origin && ["http:", "https:"].includes(url.protocol) ? url.href : null;
    } catch { return null; }
  }

  document.querySelectorAll(".map-shell").forEach(shell => {
    const container = shell.querySelector("[data-map]");
    if (!container) return;
    const fallback = shell.querySelector(".map-data");
    const reset = shell.querySelector("[data-map-reset]");
    const status = shell.querySelector("[data-map-status]");
    const report = (state, message) => {
      shell.dataset.mapState = state;
      container.dataset.mapState = state;
      if (status) status.textContent = message;
      if (fallback) fallback.hidden = state !== "error" && state !== "empty";
    };
    const points = [];
    shell.querySelectorAll("[data-map-point]").forEach(link => {
      const rawLat = link.dataset.lat, rawLon = link.dataset.lon;
      if (!rawLat?.trim() || !rawLon?.trim()) return;
      const lat = Number(rawLat), lon = Number(rawLon);
      if (!Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) return;
      const href = localUrl(link.href);
      if (!href) return;
      points.push({
        link, key: link.dataset.key, lat, lon, href,
        price: link.dataset.price || "Цена не указана",
        address: link.dataset.address || "Адрес не указан",
        houseId: link.dataset.houseId || null,
        houseUrl: localUrl(link.dataset.houseUrl),
      });
    });
    const selectPoints = () => points.filter(point => {
      point.link.hidden = cards.get(point.key)?.hidden || false;
      return !point.link.hidden;
    });
    let visiblePoints = selectPoints();
    if (!points.length) {
      container.hidden = true;
      if (reset) reset.disabled = true;
      report("empty", "Нет координат для отображения на карте. Адрес можно проверить в карточке квартиры.");
      return;
    }
    const groups = new Map(), markers = new Map();
    let state = "loading", popup = null;
    const reportState = () => {
      if (!visiblePoints.length) report("empty", "Для текущей подборки нет точек на карте.");
      else if (state === "error") report("error", "Карта 2ГИС недоступна. Проверьте подключение, ключ и поддержку WebGL. Адреса и ссылки на квартиры доступны ниже.");
      else if (state === "ready") report("ready", `Домов на карте: ${groups.size} · квартир: ${visiblePoints.length}`);
      else report("loading", "Загружаем карту 2ГИС…");
    };
    const map = window.FlatsMap?.(container, [points[0].lon, points[0].lat], next => {
      state = next;
      reportState();
    });
    if (!map) {
      container.hidden = true;
      if (reset) reset.disabled = true;
      state = "error";
      reportState();
      document.addEventListener("decisionschange", selectPoints);
      return;
    }
    const closePopup = () => { popup?.destroy(); popup = null; };
    map.on("click", closePopup);
    const fit = () => {
      if (!visiblePoints.length || !container.clientWidth || !container.clientHeight) return;
      map.invalidateSize();
      const lons = visiblePoints.map(point => point.lon), lats = visiblePoints.map(point => point.lat);
      map.fitBounds({
        southWest: [Math.min(...lons), Math.min(...lats)],
        northEast: [Math.max(...lons), Math.max(...lats)],
      }, {
        padding: { top: 40, right: 40, bottom: 40, left: 40 },
        maxZoom: visiblePoints.length === 1 ? 16 : 14,
        animation: { animate: false },
      });
    };
    function renderPoints() {
      visiblePoints = selectPoints();
      container.hidden = !visiblePoints.length;
      if (reset) reset.disabled = !visiblePoints.length;
      closePopup();
      groups.forEach(group => group.marker.destroy());
      markers.clear();
      groups.clear();
      visiblePoints.forEach(point => {
        const key = point.houseId ? `house:${point.houseId}` : `offer:${point.key}`;
        if (!groups.has(key)) groups.set(key, { offers: [] });
        const offers = groups.get(key).offers;
        if (!offers.some(offer => offer.key === point.key)) offers.push(point);
      });
      groups.forEach(group => {
        const point = group.offers[0], coordinates = [point.lon, point.lat];
        const pill = document.createElement("button");
        pill.type = "button";
        pill.className = "map-price-pill";
        pill.textContent = group.offers.length > 1 ? `${group.offers.length} квартиры` : point.price;
        pill.setAttribute("aria-label", `${point.address}. Вариантов: ${group.offers.length}`);
        group.marker = new window.mapgl.HtmlMarker(map, { coordinates, html: pill, interactive: true });
        group.coordinates = coordinates;
        group.open = () => {
          closePopup();
          const content = document.createElement("div");
          content.className = "map-popup";
          content.setAttribute("role", "group");
          content.setAttribute("aria-label", point.address);
          const close = document.createElement("button");
          close.type = "button";
          close.className = "text-button map-popup-close";
          close.textContent = "Закрыть";
          const dismiss = () => { closePopup(); pill.focus(); };
          close.addEventListener("click", dismiss);
          content.addEventListener("keydown", event => { if (event.key === "Escape") dismiss(); });
          const address = document.createElement("strong");
          address.textContent = point.address;
          content.append(close, address);
          if (point.houseUrl) {
            const house = document.createElement("a");
            house.className = "map-popup-link";
            house.href = point.houseUrl;
            house.textContent = "Паспорт дома →";
            content.append(house);
          }
          group.offers.forEach(offer => {
            const link = document.createElement("a");
            link.className = "map-popup-link";
            link.href = offer.href;
            link.textContent = `${offer.price} · квартира →`;
            content.append(link);
          });
          popup = new window.mapgl.HtmlMarker(map, { coordinates, html: content, interactive: true, zIndex: 2 });
          // MapGL attaches marker content on the next frame.
          requestAnimationFrame(() => { if (content.isConnected) close.focus(); });
        };
        pill.addEventListener("click", group.open);
        group.offers.forEach(offer => markers.set(offer.key, group));
      });
      reportState();
    }
    renderPoints();
    fit();
    document.addEventListener("decisionschange", () => {
      const wasEmpty = !visiblePoints.length;
      renderPoints();
      if (wasEmpty) fit();
    });
    container.dataset.mapReady = "true";
    if (reset) reset.addEventListener("click", fit);
    maps.push({ map, markers, fit, container, visible: container.clientWidth > 0 && container.clientHeight > 0 });
  });

  document.addEventListener("farposton:layout", () => {
    maps.forEach(entry => {
      const visible = entry.container.clientWidth > 0 && entry.container.clientHeight > 0;
      entry.map.invalidateSize();
      if (visible && !entry.visible) entry.fit();
      entry.visible = visible;
    });
  });
  document.addEventListener("farposton:map-focus", event => {
    maps.forEach(({ map, markers }) => {
      const group = markers.get(event.detail?.key);
      if (!group) return;
      map.invalidateSize();
      map.setCenter(group.coordinates, { animate: false });
      map.setZoom(16, { animate: false });
      group.open();
    });
  });
})();
