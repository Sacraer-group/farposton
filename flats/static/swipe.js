(() => {
  const stack = document.getElementById("stack");
  const empty = document.getElementById("empty");
  const error = document.getElementById("swipe-error");
  const undone = [];
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let busy = false;

  const top = () => {
    const cards = stack.querySelectorAll(".swipe-card");
    return cards[cards.length - 1] || null;
  };
  const refresh = () => { empty.hidden = Boolean(top()); };

  async function post(url, body) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    if (!response.ok) throw new Error("Не удалось сохранить действие, попробуйте ещё раз");
    return response.json();
  }

  async function decide(action) {
    const card = top();
    if (!card || busy) return;
    busy = true;
    error.textContent = "";
    try {
      await post("/api/decide", { source: card.dataset.source, source_id: card.dataset.sourceId, action });
      card.style.transform = reduced ? "" : `translateX(${action === "save" ? 120 : -120}%)`;
      setTimeout(() => { card.remove(); undone.push(card); card.style.transform = ""; refresh(); }, reduced ? 0 : 200);
    } catch (e) {
      card.style.transform = "";
      error.textContent = e.message;
    } finally {
      busy = false;
    }
  }

  async function undo() {
    if (busy) return;
    busy = true;
    error.textContent = "";
    try {
      const result = await post("/api/undo");
      const card = undone.pop();
      if (result.undone && card) stack.insertBefore(card, empty);
      refresh();
    } catch (e) {
      error.textContent = e.message;
    } finally {
      busy = false;
    }
  }

  let startX = null;
  stack.addEventListener("pointerdown", (e) => {
    const card = top();
    if (!card || !card.contains(e.target) || e.target.closest("a")) return;
    startX = e.clientX;
    card.setPointerCapture(e.pointerId);
  });
  stack.addEventListener("pointermove", (e) => {
    if (startX === null) return;
    top().style.transform = `translateX(${e.clientX - startX}px) rotate(${(e.clientX - startX) / 20}deg)`;
  });
  stack.addEventListener("pointerup", (e) => {
    if (startX === null) return;
    const dx = e.clientX - startX;
    startX = null;
    if (Math.abs(dx) > 100) decide(dx > 0 ? "save" : "skip");
    else top().style.transform = "";
  });

  document.getElementById("save").addEventListener("click", () => decide("save"));
  document.getElementById("skip").addEventListener("click", () => decide("skip"));
  document.getElementById("undo").addEventListener("click", undo);
  document.getElementById("details").addEventListener("click", () => {
    const card = top();
    if (card) location.href = card.querySelector(".details").href;
  });
  document.addEventListener("keydown", (e) => {
    if (e.target.closest("input, select, textarea")) return;
    if (e.key === "ArrowRight") decide("save");
    else if (e.key === "ArrowLeft") decide("skip");
    else if (e.key === "z" || e.key === "Z" || e.key === "я" || e.key === "Я") undo();
  });
})();
