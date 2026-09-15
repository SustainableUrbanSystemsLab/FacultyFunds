// Loaded in <head> before first paint: apply the saved theme so pages don't flash.
// No saved value means "follow the system setting".
(function () {
  try {
    var theme = localStorage.getItem("fb-theme");
    if (theme === "light" || theme === "dark") {
      document.documentElement.setAttribute("data-theme", theme);
    }
  } catch (error) {
    // Storage unavailable (private mode): fall back to the system setting.
  }
})();
