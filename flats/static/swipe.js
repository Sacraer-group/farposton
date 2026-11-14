(() => {
  const stack = document.getElementById("stack");
  const empty = document.getElementById("empty");
  const decisions = window.FlatsDecisions;
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let busy = false;

  const top = () => {
    const cards = stack.querySelectorAll(".swipe-card:not([hidden])");
    return cards[cards.length - 1] || null;
  };
  const refresh = () => {
    empty.hidden = Boolean(top());
    for (const id of ["save", "skip", "details"]) {
      document.getElementById(id).disabled = busy || !top();
    }
    document.getElementById("undo").disabled = busy;
    let hasSkips = false;
    try { hasSkips = decisions.current().some(([, , action]) => action === "skip"); }
    catch { decisions.reportError(); }
    document.querySelectorAll("[data-reset-skips]").forEach(button => {
      button.disabled = busy || !hasSkips;
    });
  };

  function decide(action) {
    const card = top();
    if (!card || busy) return;
    busy = true;
    refresh();
    try {
      decisions.decide(card.dataset.source, card.dataset.sourceId, action);
      card.style.transform = reduced ? "" : `translateX(${action === "save" ? 120 : -120}%)`;
      setTimeout(() => {
        card.style.transform = "";
        busy = false;
        try { decisions.refresh(); } catch { decisions.reportError(); }
        refresh();
      }, reduced ? 0 : 200);
    } catch {
      card.style.transform = "";
      busy = false;
      decisions.reportError();
      refresh();
    }
  }

  function undo() {
    if (busy) return;
    try {
      decisions.undo();
      decisions.refresh();
    } catch { decisions.reportError(); }
  }

  document.querySelectorAll("[data-reset-skips]").forEach(button => {
    button.addEventListener("click", () => {
      if (busy) return;
      try {
        decisions.resetSkipped();
        decisions.refresh();
      } catch { decisions.reportError(); }
    });
  });

  let startX = null;
  let draggedCard = null;
  function cancelDrag() {
    if (draggedCard) draggedCard.style.transform = "";
    startX = null;
    draggedCard = null;
  }
  stack.addEventListener("pointerdown", (e) => {
    const card = top();
    if (busy || !card || !card.contains(e.target) || e.target.closest("a")) return;
    startX = e.clientX;
    draggedCard = card;
    card.setPointerCapture(e.pointerId);
  });
  stack.addEventListener("pointermove", (e) => {
    if (startX === null) return;
    if (busy || top() !== draggedCard) { cancelDrag(); return; }
    draggedCard.style.transform = `translateX(${e.clientX - startX}px) rotate(${(e.clientX - startX) / 20}deg)`;
  });
  stack.addEventListener("pointerup", (e) => {
    if (startX === null) return;
    if (busy || top() !== draggedCard) { cancelDrag(); return; }
    const dx = e.clientX - startX;
    cancelDrag();
    if (Math.abs(dx) > 100) decide(dx > 0 ? "save" : "skip");
  });
  stack.addEventListener("pointercancel", cancelDrag);

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
  document.addEventListener("decisionschange", () => { cancelDrag(); refresh(); });
  refresh();
})();
