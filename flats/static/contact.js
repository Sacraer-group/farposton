(() => {
  const dialog = document.getElementById("contact-dialog");
  if (!dialog) return;
  const form = dialog.querySelector("form");
  const message = dialog.querySelector("textarea");
  const success = dialog.querySelector("[data-contact-success]");
  const address = dialog.querySelector("[data-contact-address]");
  const offer = dialog.querySelector("[data-contact-offer]");

  document.querySelectorAll("[data-contact-open]").forEach(button => {
    button.addEventListener("click", () => {
      address.textContent = button.dataset.contactAddress;
      offer.textContent = `Предложение ${button.dataset.contactOffer}`;
      const questions = JSON.parse(button.dataset.contactQuestions);
      message.value = `Здравствуйте! Интересует квартира: ${button.dataset.contactAddress}. Объявление ещё актуально? Когда можно посмотреть?` +
        (questions.length ? `\n\nХотелось бы уточнить:\n${questions.map(q => `• ${q}`).join("\n")}` : "");
      message.setCustomValidity("");
      form.hidden = false;
      success.hidden = true;
      dialog.showModal();
    });
  });
  dialog.querySelector("[data-contact-close]").addEventListener("click", () => dialog.close());
  message.addEventListener("input", () => message.setCustomValidity(""));
  form.addEventListener("submit", event => {
    event.preventDefault();
    if (!message.value.trim()) {
      message.setCustomValidity("Напишите сообщение о квартире.");
      message.reportValidity();
      return;
    }
    // This is a demonstration: never send the message or contact details anywhere.
    dialog.querySelector("[data-contact-sent-text]").textContent = message.value;
    form.hidden = true;
    success.hidden = false;
    success.focus();
  });
})();
