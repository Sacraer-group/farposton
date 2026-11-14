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
  const parseButton = document.getElementById("parse-request");
  const requestStatus = document.getElementById("request-status");
  const proposalPanel = document.getElementById("request-proposal");
  const draftStatus = document.getElementById("draft-status");
  const draftKey = "bereg.scenario.v1";
  let briefText = "";
  let busy = false;
  let parsing = false;
  let reviewedText = "";
  let proposal = null;
  let chosenPreset = null;
  let lastDecisionState = null;
  let revision = 0;
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
  function decisionState(values) {
    return JSON.stringify(values.filter(([, , action]) => action === "skip" || root.dataset.scope === "saved" && action === "save")
      .map(value => JSON.stringify(value)).sort());
  }
  function decisionsChanged() {
    try {
      const state = decisionState(decisions.current());
      if (lastDecisionState !== null && state !== lastDecisionState) {
        invalidateResults();
        status.textContent = root.dataset.scope === "saved"
          ? "Избранное или пропуски изменились. Подбор нужно обновить."
          : "Пропуски изменились. Подбор нужно обновить.";
      }
      lastDecisionState = state;
      refreshCandidateButtons();
    } catch {
      invalidateResults();
      decisions.reportError();
      status.textContent = "Не удалось проверить избранное и пропуски. Обновите подбор после восстановления хранилища браузера.";
    }
  }
  async function post(url, body) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 42000);
    try {
      const response = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify(body), signal: controller.signal});
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof data.error === "string" ? data.error : "Не удалось выполнить запрос. Проверьте условия и попробуйте ещё раз.");
      return data;
    } finally { clearTimeout(timer); }
  }
  function filtersFromForm() {
    const values = new FormData(form);
    return {monthly_max: values.get("monthly_max") ? Number(values.get("monthly_max")) : null,
      move_in_max: values.get("move_in_max") ? Number(values.get("move_in_max")) : null,
      rooms: values.getAll("rooms").map(Number), districts: values.getAll("districts"),
      pets_required: values.has("pets_required")};
  }
  function writeFilters(filters) {
    for (const name of ["monthly_max", "move_in_max"]) {
      if (Object.hasOwn(filters, name) && (filters[name] === null || Number.isSafeInteger(filters[name]) && filters[name] > 0)) {
        form.elements[name].value = filters[name] ?? "";
      }
    }
    for (const name of ["rooms", "districts"]) {
      if (Array.isArray(filters[name])) {
        form.querySelectorAll('input[name="' + name + '"]').forEach(input => {
          input.checked = filters[name].map(String).includes(input.value);
        });
      }
    }
    if (typeof filters.pets_required === "boolean") form.elements.pets_required.checked = filters.pets_required;
    if (Array.isArray(filters.rooms)) form.querySelector(".extra-rooms").open = filters.rooms.some(room => room > 4);
    updateDistricts();
  }
  function updateDistricts() {
    const selected = [...form.querySelectorAll('input[name="districts"]:checked')].map(input => input.value);
    document.getElementById("district-summary").textContent = selected.join(", ") || "Любые";
  }
  function rememberDraft() {
    try {
      localStorage.setItem(draftKey, JSON.stringify({question: question.value, filters: filtersFromForm(),
        priority: form.elements.priority.value, reviewedText}));
    } catch { draftStatus.textContent = "Браузер не сохранил черновик. До закрытия страницы условия останутся в форме."; }
  }
  function invalidateResults() {
    revision += 1;
    if (document.getElementById("applied-conditions").hidden) return;
    cards.replaceChildren();
    brief.hidden = true;
    briefText = "";
    brief.querySelectorAll("textarea").forEach(field => field.remove());
    document.getElementById("brief-status").textContent = "";
    document.getElementById("result-actions").hidden = true;
    document.getElementById("applied-conditions").hidden = true;
    document.getElementById("local-context").hidden = true;
    document.getElementById("evidence-list").replaceChildren(text("li", "Обновите подбор, чтобы увидеть основания по новым условиям.", "muted"));
    document.getElementById("evidence-count").textContent = "";
    document.getElementById("advisor-empty").hidden = false;
    document.querySelector("#advisor-empty h3").textContent = "Условия изменились";
    document.querySelector("#advisor-empty > p").textContent = "Нажмите «Подобрать варианты», чтобы пересчитать кандидатов. Предыдущий план больше не используется.";
    status.textContent = "Условия изменились. Подбор нужно обновить.";
  }
  function inputChanged() {
    invalidateResults();
    if (chosenPreset && chosenPreset.text !== question.value.trim()) chosenPreset = null;
    if (proposal && proposal.text !== question.value.trim()) {
      proposal = null;
      proposalPanel.hidden = true;
    }
    if (reviewedText && reviewedText !== question.value.trim()) requestStatus.textContent = "Текст изменён. Разберите его ещё раз перед подбором.";
    parseButton.disabled = parsing || !question.value.trim();
    updateDistricts();
    rememberDraft();
  }
  function displayValue(field, value) {
    if (field === "monthly_max" || field === "move_in_max") return money(value);
    if (field === "rooms") return value.map(room => room === 0 ? "Студия" : room + " комн.").join(", ");
    if (field === "pets_required") return value ? "Нужно разрешение на животных" : "Без требования к животным";
    if (field === "priority") return {budget: "Меньше платить в месяц", move_in: "Легче въехать", house: "Разобраться с домом", commute: "Проверить повседневный путь"}[value] || "Уточнить вручную";
    if (Array.isArray(value)) return value.join(", ");
    return String(value);
  }
  async function parseRequest() {
    const source = question.value.trim();
    if (!source || parsing) { question.focus(); return; }
    parsing = true;
    parseButton.disabled = true;
    proposalPanel.hidden = true;
    requestStatus.textContent = "Разбираем текст и проверяем, какие условия можно перенести в форму…";
    document.getElementById("request-panel").setAttribute("aria-busy", "true");
    try {
      const data = await post("/api/requirements", {text: source});
      if (question.value.trim() !== source) {
        requestStatus.textContent = "Текст изменился во время разбора. Разберите обновлённый запрос.";
        return;
      }
      if (!data.priority && chosenPreset?.text === source) {
        data.priority = chosenPreset.priority;
        data.recognized = [...(data.recognized || []), {field: "priority", label: "Приоритет примера",
          value: chosenPreset.priority, text: chosenPreset.label, fromPreset: true}];
      }
      proposal = {text: source, ...data};
      const list = document.getElementById("parsed-values");
      list.replaceChildren();
      for (const item of data.recognized || []) {
        const group = document.createElement("div");
        group.append(text("dt", item.label), text("dd", displayValue(item.field, item.value)),
          text("small", (item.fromPreset ? "Выбранный пример: «" : "Из текста: «") + item.text + "»"));
        list.append(group);
      }
      const notes = document.getElementById("request-notes");
      notes.replaceChildren(...(data.notes || []).map(note => text("li", note)));
      if (!(data.recognized || []).length) notes.prepend(text("li", "Фильтры не распознаны. Задайте бюджет, комнаты и район в форме; текст останется вопросом помощнику."));
      document.getElementById("apply-request").textContent = Object.keys(data.filters || {}).length ? "Перенести в форму" : "Проверить условия в форме";
      requestStatus.textContent = data.notice || "Проверьте распознанные условия перед применением.";
      proposalPanel.hidden = false;
      proposalPanel.focus({preventScroll: true});
      proposalPanel.scrollIntoView({block: "start"});
    } catch (error) {
      requestStatus.textContent = error.name === "AbortError" ? "Разбор задерживается. Повторите запрос; текст сохранён в форме." : error.message;
    } finally {
      parsing = false;
      parseButton.disabled = !question.value.trim();
      document.getElementById("request-panel").setAttribute("aria-busy", "false");
    }
  }
  parseButton.addEventListener("click", parseRequest);
  document.getElementById("apply-request").addEventListener("click", () => {
    if (!proposal || proposal.text !== question.value.trim()) return;
    writeFilters(proposal.filters || {});
    const priority = [...form.querySelectorAll('input[name="priority"]')].find(input => input.value === proposal.priority);
    if (priority) priority.checked = true;
    reviewedText = proposal.text;
    invalidateResults();
    rememberDraft();
    proposalPanel.hidden = true;
    requestStatus.textContent = "Условия перенесены. Проверьте форму и нажмите «Подобрать варианты». Остальные пожелания остаются вопросами для проверки.";
    document.getElementById("scenario").scrollIntoView({block: "start"});
    form.elements.monthly_max.focus({preventScroll: true});
  });
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
  function tripDescription(route) {
    if (route?.status === "acceptable" && Number.isFinite(route.weekly_seconds)) {
      const minutes = Math.round(route.weekly_seconds / 60);
      const duration = (minutes >= 60 ? Math.floor(minutes / 60) + " ч " : "") + minutes % 60 + " мин";
      return "По вашему графику: " + duration + " в дороге за неделю"
        + (route.week_start && route.week_end ? " · " + date(route.week_start) + "–" + date(route.week_end) : "")
        + ". Оценка 2ГИС, не гарантия прибытия.";
    }
    return route?.status === "incomplete" ? "Расчёт поездок неполный. Сравнить время за всю неделю пока нельзя."
      : "Поездки для этой квартиры ещё не рассчитаны. Проверьте свою неделю отдельно.";
  }
  function render(data, applied) {
    const recommendations = data.recommendations || [];
    const evidence = data.evidence || [];
    const numbers = new Map(evidence.map((item, i) => [item.id, i + 1]));
    brief.querySelectorAll("textarea").forEach(field => field.remove());
    document.getElementById("brief-status").textContent = "";
    cards.replaceChildren();
    document.getElementById("results-heading").textContent = data.priority === "commute"
      ? "Адреса для проверки маршрута" : "С каких адресов начать";
    status.textContent = (data.model_used ? "ИИ-подбор по найденным основаниям. " : "Подбор по данным. ")
      + (data.notice || "") + (data.total_candidates ? " Доступных кандидатов: " + data.total_candidates + "." : "");
    const conditions = document.getElementById("applied-conditions");
    conditions.replaceChildren(text("strong", "Условия этой подборки:"));
    const filters = applied.filters;
    for (const [label, value] of [["В месяц до", filters.monthly_max], ["На въезд до", filters.move_in_max]]) {
      if (value !== null) conditions.append(text("span", label + " " + money(value)));
    }
    if (filters.rooms.length) conditions.append(text("span", displayValue("rooms", filters.rooms)));
    if (filters.districts.length) conditions.append(text("span", filters.districts.join(", ")));
    if (filters.pets_required) conditions.append(text("span", "С животными"));
    if (conditions.children.length === 1) conditions.append(text("span", "Без дополнительных ограничений"));
    conditions.hidden = false;
    const actions = document.getElementById("result-actions");
    actions.hidden = !recommendations.length;
    const comparison = new URLSearchParams();
    recommendations.forEach(item => comparison.append("o", item.offer_id));
    document.getElementById("compare-candidates").href = "/compare?" + comparison;
    document.getElementById("advisor-empty").hidden = recommendations.length > 0;
    document.querySelector(".empty-recovery").hidden = recommendations.length > 0;
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
      const house = card.querySelector("[data-house]");
      house.href = safeLink(item.house_url) || detail.href.split("#")[0] + "#house";
      if (!item.house_url) house.textContent = "Сведения о доме";
      const trip = card.querySelector("[data-trip-summary]");
      trip.append(text("span", tripDescription(item.route)));
      const trips = text("a", "Мои поездки →", "text-link");
      trips.href = "/trips";
      trip.append(trips);
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
    brief.querySelector(".section-intro").textContent = data.priority === "commute" && !data.coverage?.route
      ? "Это адреса для проверки пути: расчёт поездок ещё не завершён. Время встреч и ответы собственников не подтверждены."
      : "Порядок предложен по доступным данным. Время встреч и ответы собственников пока не подтверждены.";
    const lines = ["Берег · план первых просмотров", "Приоритет: " + priorityLabel,
      "Задача: " + applied.question, ...[...conditions.querySelectorAll("span")].map(node => node.textContent), ""];
    recommendations.forEach((item, index) => {
      const block = text("article", "", "brief-item");
      block.append(text("h3", (index + 1) + ". " + (item.address || "Адрес не указан")));
      const cost = "В месяц, известно: " + money(item.monthly_known) + ". На въезд, известно: " + money(item.move_in_known) + ".";
      block.append(text("p", cost));
      block.append(text("p", tripDescription(item.route)));
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
        tripDescription(item.route),
        ...item.questions.slice(0, 4).map(q => "— " + q.text),
        "Неизвестно: " + item.unknowns.join("; "), "Источник: " + (safeLink(item.source_url) || "не указан") + " · " + date(item.last_seen), "");
      if (item.house_url) lines.push("Паспорт дома: " + safeLink(item.house_url), "");
    });
    lines.push("Для Владивостока: проверьте повседневный маршрут и последний пеший участок, подъёмы и лестницы. Оценка маршрута не гарантирует время прибытия.", "Сведения о доме и отключениях сверяйте по адресу и дате; отсутствие записей не означает отсутствие событий.");
    briefText = lines.join("\n");
    document.querySelectorAll(".decision-steps a").forEach((link, index) => {
      if (index === (recommendations.length ? 1 : 0)) link.setAttribute("aria-current", "step");
      else link.removeAttribute("aria-current");
    });
  }
  function detailPath(item) { return safeLink(item.detail_url) || "/"; }
  root.querySelectorAll("[data-question]").forEach(preset => preset.addEventListener("click", () => {
    question.value = preset.dataset.question;
    inputChanged();
    chosenPreset = {text: question.value.trim(), priority: preset.dataset.priority, label: preset.textContent.trim()};
    question.focus();
  }));
  form.addEventListener("input", inputChanged);
  question.addEventListener("input", inputChanged);
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (busy || !form.reportValidity()) return;
    if (question.value.trim() && question.value.trim() !== reviewedText) {
      await parseRequest();
      if (proposalPanel.hidden) document.getElementById("request-panel").scrollIntoView({block: "start"});
      return;
    }
    busy = true; button.disabled = true; results.setAttribute("aria-busy", "true");
    const submittedRevision = revision;
    status.textContent = "Проверяем условия, находим основания и готовим вопросы…";
    const values = new FormData(form);
    const payload = {question: question.value.trim() || "С каких квартир начать просмотры и что нужно уточнить?", priority: values.get("priority"),
      scope: root.dataset.scope, offer_ids: JSON.parse(root.dataset.offerIds),
      filters: filtersFromForm()};
    try {
      try { payload.decisions = decisions.current(); }
      catch {
        decisions.reportError();
        throw new Error("Не удалось прочитать избранное и пропуски. Проверьте, разрешено ли хранение данных сайта в браузере.");
      }
      const requestedDecisionState = decisionState(payload.decisions);
      lastDecisionState = requestedDecisionState;
      const data = await post("/api/advisor", payload);
      if (requestedDecisionState !== decisionState(decisions.current())) {
        decisionsChanged();
        status.textContent = "Избранное или пропуски изменились во время подбора. Нажмите «Подобрать варианты» ещё раз.";
      } else if (submittedRevision === revision) {
        render(data, payload);
        results.focus({preventScroll: true});
        results.scrollIntoView({block: "start"});
      } else status.textContent = "Условия изменились во время подбора. Нажмите «Подобрать варианты» ещё раз.";
    }
    catch (error) { status.textContent = error.name === "AbortError" ? "Ответ задерживается. Повторите запрос; условия остались в форме." : error.message; }
    finally { busy = false; button.disabled = false; results.setAttribute("aria-busy", "false"); }
  });
  document.getElementById("clear-scenario").addEventListener("click", () => {
    writeFilters({monthly_max: null, move_in_max: null, rooms: [], districts: [], pets_required: false});
    form.querySelector('input[name="priority"][value="budget"]').checked = true;
    question.value = "";
    reviewedText = "";
    proposal = null;
    chosenPreset = null;
    proposalPanel.hidden = true;
    parseButton.disabled = true;
    invalidateResults();
    try { localStorage.removeItem(draftKey); }
    catch { draftStatus.textContent = "Не удалось удалить черновик из хранилища браузера. В форме условия сброшены."; }
    requestStatus.textContent = "Условия и текст сброшены. Избранное сохранено.";
    form.elements.monthly_max.focus();
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
  document.addEventListener("decisionschange", decisionsChanged);
  try { lastDecisionState = decisionState(decisions.current()); }
  catch { decisions.reportError(); }
  try {
    const saved = localStorage.getItem(draftKey);
    if (saved && saved.length < 16000 && root.dataset.scope === "search" && root.dataset.hasQuery !== "true") {
      const draft = JSON.parse(saved);
      const filters = draft?.filters;
      const valid = filters && !Array.isArray(filters) && typeof filters === "object"
        && ["monthly_max", "move_in_max"].every(name => filters[name] === null || Number.isSafeInteger(filters[name]) && filters[name] > 0)
        && Array.isArray(filters.rooms) && filters.rooms.length <= 11 && filters.rooms.every(room => Number.isInteger(room) && room >= 0 && room <= 10)
        && Array.isArray(filters.districts) && filters.districts.every(district => typeof district === "string" && [...form.querySelectorAll('input[name="districts"]')].some(input => input.value === district))
        && typeof filters.pets_required === "boolean";
      if (valid && typeof draft.question === "string" && draft.question.length <= 1200) {
        question.value = draft.question;
        writeFilters(draft.filters);
        const priority = [...form.querySelectorAll('input[name="priority"]')].find(input => input.value === draft.priority);
        if (priority) priority.checked = true;
        reviewedText = typeof draft.reviewedText === "string" && draft.reviewedText === draft.question.trim() ? draft.reviewedText : "";
        draftStatus.textContent = "Восстановлен ваш черновик из этого браузера. Проверьте условия перед подбором.";
      } else draftStatus.textContent = "Сохранённый черновик больше не подходит к форме. Задайте условия заново.";
    }
  } catch { draftStatus.textContent = "Черновик недоступен. Можно заполнить форму и продолжить без сохранения."; }
  parseButton.disabled = !question.value.trim();
  if (root.dataset.hasQuery === "true" && question.value.trim()) parseRequest();
})();
