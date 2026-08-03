// Submit the parent form when a [data-autosubmit] control changes.
document.addEventListener("change", (event) => {
  const control = event.target.closest("[data-autosubmit]");
  if (control && control.form) {
    control.form.requestSubmit();
  }
});
