(function () {
  "use strict";

  // Ask before destructive actions: <button data-confirm="Sure?">
  document.addEventListener(
    "click",
    function (event) {
      var target = event.target.closest("[data-confirm]");
      if (target && !window.confirm(target.getAttribute("data-confirm"))) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    },
    true
  );

  // Forms that must not be submitted twice (e.g. import commit): disable buttons after the first click.
  document.addEventListener("submit", function (event) {
    var form = event.target.closest("form[data-single-submit]");
    if (!form) return;
    if (form.dataset.submitted) {
      event.preventDefault();
      return;
    }
    form.dataset.submitted = "1";
    form.querySelectorAll("button[type=submit]").forEach(function (button) {
      button.disabled = true;
      button.setAttribute("aria-disabled", "true");
    });
  });

  // "Select all" checkbox inside a bulk-edit form.
  document.addEventListener("change", function (event) {
    var toggle = event.target.closest("[data-select-all]");
    if (!toggle) return;
    var scope = toggle.closest("[data-bulk]") || document;
    scope.querySelectorAll('input[type="checkbox"][name="ids"]').forEach(function (box) {
      box.checked = toggle.checked;
    });
  });

  // Theme toggle: system -> light -> dark -> system (saved per browser).
  var root = document.documentElement;
  var describeTheme = function (button, theme) {
    var text = "Theme: " + theme + " (click to change)";
    button.title = text;
    button.setAttribute("aria-label", text);
  };
  document.querySelectorAll("[data-theme-toggle]").forEach(function (button) {
    describeTheme(button, root.getAttribute("data-theme") || "system");
  });
  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-theme-toggle]");
    if (!button) return;
    var current = root.getAttribute("data-theme") || "system";
    var next = current === "system" ? "light" : current === "light" ? "dark" : "system";
    root.classList.add("no-transitions");
    if (next === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", next);
    window.getComputedStyle(root).color; // apply the new colors before transitions come back
    window.setTimeout(function () {
      root.classList.remove("no-transitions");
    }, 0);
    try {
      if (next === "system") localStorage.removeItem("fb-theme");
      else localStorage.setItem("fb-theme", next);
    } catch (error) {
      // Not persisted; the choice still applies to this page.
    }
    describeTheme(button, next);
  });

  // Show a field (or a whole section) only for certain values of another field in the same form:
  //   data-when="type=grant"   data-when="kind=regular,summer"
  var conditional = Array.prototype.slice.call(document.querySelectorAll("[data-when]"));
  if (conditional.length) {
    var applyConditions = function () {
      conditional.forEach(function (element) {
        var form = element.closest("form");
        if (!form) return;
        var parts = element.getAttribute("data-when").split("=");
        var control = form.elements[parts[0]];
        var current = control ? String(control.value || "") : "";
        var visible = parts[1].split(",").indexOf(current) !== -1;
        var isInput = /^(INPUT|SELECT|TEXTAREA)$/.test(element.tagName);
        var target = isInput ? element.closest(".field") || element : element;
        target.hidden = !visible;
      });
    };
    document.addEventListener("change", applyConditions);
    applyConditions();
  }

  // Running total on the fund budget form.
  var output = document.querySelector("[data-sum-output]");
  if (output) {
    var inputs = Array.prototype.slice.call(document.querySelectorAll('input[name^="budget-cat_"]'));
    var format = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
    var update = function () {
      var total = inputs.reduce(function (sum, input) {
        return sum + (parseFloat(input.value) || 0);
      }, 0);
      output.textContent = format.format(total);
    };
    inputs.forEach(function (input) {
      input.addEventListener("input", update);
    });
    update();
  }
})();
