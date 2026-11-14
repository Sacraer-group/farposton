(() => {
  const root = document.getElementById("advisor");
  if (!root) return;
  const form = document.getElementById("advisor-form");
  const button = document.getElementById("ask-advisor");
  const status = document.getElementById("advisor-status");
  const results = document.getElementById("advisor-results");
  const cards = document.getElementById("recommendations");
  const brief = document.getElementById("viewing-brief");
  const evidencePanel = document.getElementById("evidence");
  const question = form.elements.question;
  const decisions = window.FlatsDecisions;
  let briefText = "";
  let busy = false;
  const money = value => Number.isFinite(value) ? value.toLocaleString("ru-RU") + " ₽" : "Не указано";
  const date = value => typeof value === "string" && /^\d{4}-\d{2}-\d{2}/.test(value)
    ? value.slice(0, 10).split("-").reverse().join(".") : "дата не указана";
  const text = (tag, value, className) => {
    const node = document.createElement(tag);
    node.textContent = value;
    if (className) node.className = className;
    return node;
  };
  function safeLink(value) {
    if (typeof value !== "string" || !value.trim()) return "";
    try {
      const url = new URL(value, document.baseURI);
      return ["https:", "http:"].includes(url.protocol) ? url.href : "";
    } catch { return ""; }
  }
  function routeLink(item) {
    if (!Number.isFinite(item.lat) || !Number.isFinite(item.lon)
        || Math.abs(item.lat) > 90 || Math.abs(item.lon) > 180) return "";
    return "https://yandex.ru/maps/?rtext=~" + encodeURIComponent(item.lat + "," + item.lon);
  }
  function refreshCandidateButtons() {
    const buttons = [...cards.querySelectorAll("[data-save-candidate]")];
    if (!buttons.length) return;
    try {
      const saved = new Set(decisions.current()
        .filter(([, , action]) => action === "save")
        .map(([source, id]) => `${source}:${id}`));
      buttons.forEach(save => {
        save.disabled = saved.has(save.dataset.offerId);
        save.querySelector("[data-candidate-label]").textContent = save.disabled ? "В избранном" : "В избранное";
      });
    } catch { decisions.reportError(); }
  }
  async function post(url, body) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 42000);
    try {
      const response = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify(body), signal: controller.signal});
      if (!response.ok) throw new Error("Не удалось выполнить запрос. Проверьте условия и попробуйте ещё раз.");
      return await response.json();
    } finally { clearTimeout(timer); }
  }
  function evidenceLink(id, numbers) {
    const number = numbers.get(id);
    if (!number) return null;
    const link = text("a", "[" + number + "]", "evidence-ref");
    link.href = "#evidence-" + number;
    link.setAttribute("aria-label", "Основание " + number);
    link.addEventListener("click", event => {
      event.preventDefault();
      evidencePanel.open = true;
      document.getElementById("evidence-" + number)?.scrollIntoView({block: "center"});
    });
    return link;
  }
  function render(data) {
    const recommendations = data.recommendations || [];
    const evidence = data.evidence || [];
    const numbers = new Map(evidence.map((item, i) => [item.id, i + 1]));
    cards.replaceChildren();
    document.getElementById("results-heading").textContent = data.priority === "commute"
      ? "Адреса для проверки маршрута" : "С каких адресов начать";
    status.textContent = (data.priority === "commute" ? "Удобство дороги между вариантами ещё не сравнивалось. " : "")
      + (data.model_used ? "ИИ-подбор по найденным основаниям. " : "Подбор по данным. ")
      + (data.notice || "") + (data.total_candidates ? " Доступных кандидатов: " + data.total_candidates + "." : "");
    document.getElementById("advisor-empty").hidden = recommendations.length > 0;
    if (!recommendations.length) {
      document.querySelector("#advisor-empty h3").textContent = "Для этого запроса нет достаточных оснований";
      document.querySelector("#advisor-empty > p").textContent = data.next_step || "Уточните вопрос или измените условия. Помощник использует только доступные сведения.";
    }
    recommendations.forEach((item, index) => {
      const card = document.getElementById("recommendation-template").content.firstElementChild.cloneNode(true);
      card.querySelector(".candidate-number").textContent = String(index + 1).padStart(2, "0");
      const detail = card.querySelector("[data-detail]");
      detail.textContent = item.address || "Адрес не указан";
      detail.href = safeLink(item.detail_url) || "/";
      card.querySelector(".candidate-district").textContent = item.district || "Район не указан";
      const photo = card.querySelector(".candidate-photo");
      const photoUrl = safeLink(item.photo);
      if (item.photo && photoUrl) {
        photo.src = photoUrl;
        photo.addEventListener("error", () => { photo.hidden = true; }, {once: true});
      } else photo.remove();
      card.querySelector("[data-monthly]").textContent = money(item.monthly_known);
      card.querySelector("[data-move-in]").textContent = money(item.move_in_known);
      item.reasons.forEach(reason => {
        const li = text("li", reason.text);
        const citation = evidenceLink(reason.id, numbers);
        if (citation) li.append(" ", citation);
        card.querySelector("[data-reasons]").append(li);
      });
      (item.unknowns || []).forEach(value => card.querySelector("[data-unknowns]").append(text("li", value)));
      card.querySelector("[data-first-question]").textContent = item.questions[0]?.text || "Подтвердите актуальность объявления и все условия перед просмотром.";
      card.querySelector("[data-house]").href = detail.href.split("#")[0] + "#house";
      const route = card.querySelector("[data-route]");
      const routeUrl = routeLink(item);
      if (routeUrl) route.href = routeUrl; else route.remove();
      const save = card.querySelector("[data-save-candidate]");
      save.dataset.offerId = item.offer_id;
      const label = text("span", "В избранное");
      label.dataset.candidateLabel = "";
      save.replaceChildren(...save.querySelectorAll("svg"), label);
      save.addEventListener("click", () => {
        save.disabled = true;
        const cut = item.offer_id.indexOf(":");
        try {
          decisions.decide(item.offer_id.slice(0, cut), item.offer_id.slice(cut + 1), "save");
          decisions.refresh();
          status.textContent = "Предложение сохранено. Избранные квартиры можно сравнить вместе.";
        } catch {
          save.disabled = false;
          decisions.reportError();
          status.textContent = "Не удалось сохранить предложение в браузере. Проверьте, разрешено ли хранение данных сайта.";
        }
      });
      cards.append(card);
    });
    refreshCandidateButtons();
    const list = document.getElementById("evidence-list");
    list.replaceChildren();
    evidence.forEach((item, i) => {
      const li = text("li", "");
      li.id = "evidence-" + (i + 1);
      li.append(text("p", item.text));
      const link = text("a", item.source || "Источник", "text-link");
      const url = safeLink(item.url);
      if (url) { link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer"; }
      li.append(link, text("small", " · Наблюдение: " + date(item.observed_at)
        + (item.kind === "guide" ? " · Общая справка" : "")));
      list.append(li);
    });
    document.getElementById("evidence-count").textContent = evidence.length ? "· " + evidence.length : "";
    const local = document.getElementById("local-context");
    local.replaceChildren();
    const guides = evidence.filter(item => item.kind === "guide");
    local.hidden = !guides.length;
    if (guides.length) local.append(text("h3", "Что проверить по этому сценарию"));
    guides.forEach(item => {
      const article = text("article", "", "local-guide panel");
      article.append(text("strong", item.title || item.source), text("p", item.text));
      const citation = evidenceLink(item.id, numbers);
      if (citation) article.append(citation);
      local.append(article);
    });
    const briefItems = document.getElementById("brief-items");
    briefItems.replaceChildren();
    brief.hidden = !recommendations.length;
    const responsePriority = [...form.querySelectorAll('input[name="priority"]')].find(input => input.value === data.priority);
    const priorityLabel = responsePriority?.nextElementSibling.querySelector("strong").textContent || "Не указан";
    brief.querySelector(".section-intro").textContent = data.priority === "commute"
      ? "Это адреса для проверки пути: удобство маршрутов ещё не сравнивалось. Время встреч и ответы собственников не подтверждены."
      : "Порядок предложен по доступным данным. Время встреч и ответы собственников пока не подтверждены.";
    const lines = ["Farposton · план первых просмотров", "Приоритет: " + priorityLabel, ""];
    recommendations.forEach((item, index) => {
      const block = text("article", "", "brief-item");
      block.append(text("h3", (index + 1) + ". " + (item.address || "Адрес не указан")));
      const cost = "В месяц, известно: " + money(item.monthly_known) + ". На въезд, известно: " + money(item.move_in_known) + ".";
      block.append(text("p", cost));
      const ul = document.createElement("ul");
      item.questions.slice(0, 4).forEach(q => ul.append(text("li", q.text)));
      if (item.unknowns.length) ul.append(text("li", "Осталось уточнить: " + item.unknowns.join("; ")));
      block.append(ul);
      const source = text("a", "Объявление · " + date(item.last_seen), "text-link");
      source.href = safeLink(item.source_url) || detailPath(item);
      source.target = "_blank"; source.rel = "noopener noreferrer";
      block.append(source);
      briefItems.append(block);
      lines.push((index + 1) + ". " + (item.address || "Адрес не указан"), cost,
        ...item.questions.slice(0, 4).map(q => "— " + q.text),
        "Неизвестно: " + item.unknowns.join("; "), "Источник: " + (safeLink(item.source_url) || "не указан") + " · " + date(item.last_seen), "");
    });
    lines.push("Для Владивостока: проверьте повседневный маршрут и последний пеший участок, подъёмы и лестницы. Время пути в приложении не рассчитано.", "Сведения о доме и отключениях сверяйте по адресу и дате; отсутствие записей не означает отсутствие событий.");
    briefText = lines.join("\n");
    document.querySelectorAll(".decision-steps a").forEach((link, index) => {
      if (index === (recommendations.length ? 1 : 0)) link.setAttribute("aria-current", "step");
      else link.removeAttribute("aria-current");
    });
  }
  function detailPath(item) { return safeLink(item.detail_url) || "/"; }
  form.querySelectorAll("[data-question]").forEach(preset => preset.addEventListener("click", () => {
    question.value = preset.dataset.question;
    form.querySelector('input[name="priority"][value="' + preset.dataset.priority + '"]').checked = true;
    question.focus();
  }));
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (busy) return;
    busy = true; button.disabled = true; results.setAttribute("aria-busy", "true");
    status.textContent = "Проверяем условия, находим основания и готовим вопросы…";
    const values = new FormData(form);
    const payload = {question: values.get("question").trim(), priority: values.get("priority"),
      scope: root.dataset.scope, offer_ids: JSON.parse(root.dataset.offerIds),
      filters: {monthly_max: values.get("monthly_max") ? Number(values.get("monthly_max")) : null,
        move_in_max: values.get("move_in_max") ? Number(values.get("move_in_max")) : null,
        rooms: values.getAll("rooms").map(Number), pets_required: values.has("pets_required")}};
    try {
      try { payload.decisions = decisions.current(); }
      catch {
        decisions.reportError();
        throw new Error("Не удалось прочитать избранное и пропуски. Проверьте, разрешено ли хранение данных сайта в браузере.");
      }
      render(await post("/api/advisor", payload));
    }
    catch (error) { status.textContent = error.name === "AbortError" ? "Ответ задерживается. Повторите запрос; условия остались в форме." : error.message; }
    finally { busy = false; button.disabled = false; results.setAttribute("aria-busy", "false"); }
  });
  document.getElementById("copy-brief").addEventListener("click", async () => {
    const copyStatus = document.getElementById("brief-status");
    try { await navigator.clipboard.writeText(briefText); copyStatus.textContent = "План скопирован"; }
    catch {
      let field = brief.querySelector("textarea");
      if (!field) { field = document.createElement("textarea"); field.className = "copy-fallback"; field.readOnly = true; field.setAttribute("aria-label", "План для копирования"); brief.append(field); }
      field.value = briefText; field.focus(); field.select(); copyStatus.textContent = "Скопируйте выделенный план";
    }
  });
  document.getElementById("print-brief").addEventListener("click", () => window.print());
  document.addEventListener("decisionschange", refreshCandidateButtons);
})();
