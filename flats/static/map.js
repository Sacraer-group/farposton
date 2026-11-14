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

  function photoUrl(value) {
    if (!value) return null;
    try {
      const url = new URL(value);
      return ["http:", "https:"].includes(url.protocol) ? url.href : null;
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
        photo: photoUrl(link.dataset.photo),
        details: [link.dataset.rooms, link.dataset.area, link.dataset.floor].filter(Boolean).join(" · "),
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
    let closeTimer, popupContent, activePill, activeGroup, activeClose;
    const keepPopup = () => clearTimeout(closeTimer);
    const closePopup = () => {
      keepPopup();
      popup?.destroy();
      popup = popupContent = activePill = activeGroup = activeClose = null;
    };
    const scheduleClose = () => {
      keepPopup();
      closeTimer = setTimeout(() => {
        if (document.activeElement === activePill || popupContent?.contains(document.activeElement)) return;
        closePopup();
      }, 200);
    };
    const positionPopup = () => {
      if (!popupContent?.isConnected || !activePill) return;
      const markerBounds = activePill.getBoundingClientRect?.();
      const mapBounds = container.getBoundingClientRect?.();
      if (!markerBounds || !mapBounds) return;
      const top = Math.max(0, mapBounds.top) + 8;
      const bottom = Math.min(window.innerHeight, mapBounds.bottom) - 8;
      const above = markerBounds.top - top - 24;
      const below = bottom - markerBounds.bottom - 24;
      popupContent.dataset.placement = below > above ? "below" : "above";
      popupContent.style.maxHeight = `${Math.max(0, Math.min(360, bottom - top))}px`;
      const left = Math.max(0, mapBounds.left), right = Math.min(window.innerWidth, mapBounds.right);
      const width = Math.max(0, Math.min(300, right - left - 16));
      const anchor = markerBounds.left + markerBounds.width / 2;
      const center = Math.max(left + width / 2 + 8, Math.min(right - width / 2 - 8, anchor));
      popupContent.style.maxWidth = `${width}px`;
      popupContent.style.marginLeft = `${center - anchor}px`;
      popupContent.style.marginTop = "0px";
      const bounds = popupContent.getBoundingClientRect?.();
      if (bounds) popupContent.style.marginTop = `${Math.max(top, Math.min(bottom - bounds.height, bounds.top)) - bounds.top}px`;
    };
    map.on("click", closePopup);
    map.on("idle", positionPopup);
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
        let dismissing = false;
        group.open = (focus = true) => {
          keepPopup();
          if (activeGroup === group) {
            if (focus) requestAnimationFrame(() => {
              if (activeGroup === group) activeClose?.focus();
            });
            return;
          }
          closePopup();
          activeGroup = group;
          activePill = pill;
          const content = document.createElement("div");
          content.className = "map-popup";
          popupContent = content;
          content.addEventListener("pointerenter", keepPopup);
          content.addEventListener("pointerleave", scheduleClose);
          content.addEventListener("focusin", keepPopup);
          content.addEventListener("focusout", scheduleClose);
          content.setAttribute("role", "group");
          content.setAttribute("aria-label", point.address);
          const close = document.createElement("button");
          activeClose = close;
          close.type = "button";
          close.className = "text-button map-popup-close";
          close.textContent = "Закрыть";
          const dismiss = () => { closePopup(); dismissing = true; pill.focus(); dismissing = false; };
          close.addEventListener("click", dismiss);
          content.addEventListener("keydown", event => { if (event.key === "Escape") dismiss(); });
          content.addEventListener("click", event => event.stopPropagation());
          const list = document.createElement("div");
          list.className = "map-popup-list";
          group.offers.forEach(offer => {
            const card = document.createElement("article");
            card.className = "map-popup-card";
            const photo = document.createElement("div");
            photo.className = "map-popup-photo";
            photo.textContent = "Фото нет";
            if (offer.photo) {
              const img = document.createElement("img");
              img.alt = "";
              img.loading = "lazy";
              img.referrerPolicy = "no-referrer";
              img.addEventListener("error", () => { img.remove(); });
              img.src = offer.photo;
              photo.append(img);
            }
            const info = document.createElement("div");
            info.className = "map-popup-info";
            const price = document.createElement("strong");
            price.className = "map-popup-price";
            price.textContent = offer.price === "Цена не указана" ? offer.price : `${offer.price} / месяц`;
            const details = document.createElement("p");
            details.className = "map-popup-details";
            details.textContent = offer.details || "Параметры не указаны";
            const address = document.createElement("p");
            address.className = "map-popup-address";
            address.textContent = offer.address;
            const link = document.createElement("a");
            link.className = "map-popup-link";
            link.href = offer.href;
            link.textContent = "Подробнее о квартире →";
            info.append(price, details, address, link);
            card.append(photo, info);
            list.append(card);
          });
          content.append(close, list);
          if (point.houseUrl || group.offers.length === 1) {
            const house = document.createElement("a");
            house.className = "map-popup-house";
            house.href = group.offers.length === 1 ? `${point.href.split("#")[0]}#house` : point.houseUrl;
            house.textContent = "Паспорт дома →";
            content.append(house);
          }
          popup = new window.mapgl.HtmlMarker(map, { coordinates, html: content, interactive: true, zIndex: 2 });
          // MapGL attaches markers and applies setCenter on the next frame.
          requestAnimationFrame(() => {
            if (popupContent !== content || !content.isConnected) return;
            positionPopup();
            if (focus) close.focus();
          });
        };
        pill.addEventListener("click", () => group.open());
        pill.addEventListener("pointerenter", event => { if (event.pointerType !== "touch") group.open(false); });
        pill.addEventListener("pointerleave", scheduleClose);
        pill.addEventListener("focus", () => { if (!dismissing) group.open(false); });
        pill.addEventListener("blur", scheduleClose);
        pill.addEventListener("keydown", event => {
          if (event.key === "Escape" && activeGroup === group) {
            event.preventDefault();
            closePopup();
          }
        });
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
