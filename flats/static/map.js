(() => {
  const maps = [];
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

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
        key: link.dataset.key,
        lat,
        lon,
        price: link.dataset.price || "Цена не указана",
        address: link.dataset.address || "Адрес не указан",
        href: url.href,
      });
    });
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
      return;
    }

    const map = L.map(container, {
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
      map.invalidateSize({pan: false});
      map.fitBounds(points.map((point) => [point.lat, point.lon]), {
        padding: [40, 40],
        maxZoom: points.length === 1 ? 16 : 14,
        animate: false,
      });
    };
    fit();
    const markers = new Map();
    points.forEach((point) => {
      const pill = document.createElement("span");
      pill.className = "map-price-pill";
      pill.textContent = point.price;
      const marker = L.marker([point.lat, point.lon], {
        icon: L.divIcon({className: "map-price-marker", html: pill, iconSize: null, iconAnchor: [0, 0]}),
        title: `${point.price} · ${point.address}`,
        keyboard: true,
        riseOnHover: true,
      }).addTo(map);
      marker.getElement().setAttribute("aria-label", `${point.price} · ${point.address}. Подробнее о квартире`);
      const popup = document.createElement("div");
      popup.className = "map-popup";
      const price = document.createElement("strong");
      price.className = "map-popup-price";
      price.textContent = point.price;
      const address = document.createElement("p");
      address.className = "map-popup-address";
      address.textContent = point.address;
      const link = document.createElement("a");
      link.className = "map-popup-link";
      link.href = point.href;
      link.textContent = "Открыть квартиру";
      popup.append(price, address, link);
      marker.bindPopup(popup, {maxWidth: 260});
      markers.set(point.key, marker);
    });

    container.dataset.mapReady = "true";
    report("loading", "Загружаем карту…");
    let tileErrors = false;
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
    }).on("loading", () => { tileErrors = false; })
      .on("tileerror", () => {
        tileErrors = true;
        report("error", "Не удалось загрузить карту. Адреса и ссылки на квартиры доступны ниже.");
      })
      .on("load", () => {
        if (!tileErrors) report("ready", `Вариантов на карте: ${points.length}`);
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
