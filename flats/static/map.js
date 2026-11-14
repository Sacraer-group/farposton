(() => {
  const maps = [];
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const cards = new Map([...document.querySelectorAll("[data-listing-key]")]
    .map(card => [card.dataset.listingKey, card]));

  document.querySelectorAll(".map-shell").forEach((shell) => {
    const container = shell.querySelector("[data-map]");
    if (!container) return;
    const fallback = shell.querySelector(".map-data");
    const reset = shell.querySelector("[data-map-reset]");
    let status = shell.querySelector("[data-map-status]");
    if (!status) {
      status = document.createElement("p");
      status.dataset.mapStatus = "";
      status.setAttribute("role", "status");
      shell.append(status);
    }
    const report = (state, message) => {
      shell.dataset.mapState = state;
      container.dataset.mapState = state;
      status.textContent = message;
      if (fallback) fallback.hidden = state !== "error" && state !== "empty";
    };
    const points = [];
    shell.querySelectorAll("[data-map-point]").forEach((link) => {
      const rawLat = link.dataset.lat;
      const rawLon = link.dataset.lon;
      if (!rawLat?.trim() || !rawLon?.trim()) return;
      const lat = Number(rawLat);
      const lon = Number(rawLon);
      if (!Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) return;
      const url = new URL(link.href, location.href);
      if (url.origin !== location.origin || !["http:", "https:"].includes(url.protocol)) return;
      points.push({
        link,
        key: link.dataset.key,
        lat,
        lon,
        price: link.dataset.price || "Цена не указана",
        address: link.dataset.address || "Адрес не указан",
        href: url.href,
        houseId: link.dataset.houseId || null,
        houseUrl: link.dataset.houseUrl || null,
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
    if (!window.L) {
      container.hidden = true;
      if (reset) reset.disabled = true;
      report("error", "Карта не загрузилась. Адреса и ссылки на квартиры доступны ниже.");
      document.addEventListener("decisionschange", selectPoints);
      return;
    }

    const map = L.map(container, {
      center: [points[0].lat, points[0].lon],
      zoom: 14,
      scrollWheelZoom: false,
      zoomControl: false,
      zoomAnimation: !reducedMotion,
      fadeAnimation: !reducedMotion,
      markerZoomAnimation: !reducedMotion,
    });
    L.control.zoom({
      position: "bottomright",
      zoomInTitle: "Приблизить карту",
      zoomOutTitle: "Отдалить карту",
    }).addTo(map);
    const fit = () => {
      if (!visiblePoints.length) return;
      map.invalidateSize({pan: false});
      map.fitBounds(visiblePoints.map((point) => [point.lat, point.lon]), {
        padding: [40, 40],
        maxZoom: visiblePoints.length === 1 ? 16 : 14,
        animate: false,
      });
    };
    const markers = new Map();
    const groups = new Map();
    const markerLayer = L.layerGroup().addTo(map);
    let tileErrors = false;
    let tilesReady = false;
    function reportState() {
      if (!visiblePoints.length) report("empty", "Для текущей подборки нет точек на карте.");
      else if (tileErrors) report("error", "Не удалось загрузить карту. Адреса и ссылки на квартиры доступны ниже.");
      else if (!tilesReady) report("loading", "Загружаем карту…");
      else report("ready", `Домов на карте: ${groups.size} · квартир: ${visiblePoints.length}`);
    }
    function renderPoints() {
      visiblePoints = selectPoints();
      container.hidden = !visiblePoints.length;
      if (reset) reset.disabled = !visiblePoints.length;
      markerLayer.clearLayers();
      markers.clear();
      groups.clear();
      visiblePoints.forEach(point => {
        const key = point.houseId ? `house:${point.houseId}` : `offer:${point.key}`;
        if (!groups.has(key)) groups.set(key, []);
        if (!groups.get(key).some(p => p.key === point.key)) groups.get(key).push(point);
      });
      groups.forEach(offers => {
        const point = offers[0];
        const pill = document.createElement('span');
        pill.className = 'map-price-pill';
        pill.textContent = offers.length > 1 ? `${offers.length} квартиры` : point.price;
        const marker = L.marker([point.lat, point.lon], {
          icon: L.divIcon({className: 'map-price-marker', html: pill, iconSize: null, iconAnchor: [0, 0]}),
          title: `${point.address} · вариантов: ${offers.length}`,
          keyboard: true, riseOnHover: true,
        }).addTo(markerLayer);
        marker.getElement().setAttribute('aria-label', `${point.address}. Вариантов: ${offers.length}`);
        const popup = document.createElement('div'); popup.className = 'map-popup';
        const address = document.createElement('strong'); address.textContent = point.address; popup.append(address);
        if (point.houseUrl) {
          const house = document.createElement('a'); house.className = 'map-popup-link';
          house.href = point.houseUrl; house.textContent = 'Паспорт дома →'; popup.append(house);
        }
        offers.forEach(offer => {
          const link = document.createElement('a'); link.className = 'map-popup-link';
          link.href = offer.href; link.textContent = `${offer.price} · квартира →`; popup.append(link);
          markers.set(offer.key, marker);
        });
        marker.bindPopup(popup, {maxWidth: 280});
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
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
    }).on("loading", () => { tileErrors = false; tilesReady = false; reportState(); })
      .on("tileerror", () => {
        tileErrors = true;
        reportState();
      })
      .on("load", () => {
        tilesReady = true;
        reportState();
      }).addTo(map);
    if (reset) reset.addEventListener("click", fit);
    maps.push({map, markers, fit, container, visible: container.clientWidth > 0 && container.clientHeight > 0});
  });

  document.addEventListener("farposton:layout", () => {
    maps.forEach((entry) => {
      const visible = entry.container.clientWidth > 0 && entry.container.clientHeight > 0;
      entry.map.invalidateSize({pan: false});
      if (visible && !entry.visible) entry.fit();
      entry.visible = visible;
    });
  });
  document.addEventListener("farposton:map-focus", (event) => {
    maps.forEach(({map, markers}) => {
      const marker = markers.get(event.detail?.key);
      if (!marker) return;
      map.invalidateSize({pan: false});
      map.setView(marker.getLatLng(), 16, {animate: false});
      marker.openPopup();
    });
  });
})();
