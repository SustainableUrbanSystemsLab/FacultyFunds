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

  // "Select all" checkbox inside a bulk-edit form.
  document.addEventListener("change", function (event) {
    var toggle = event.target.closest("[data-select-all]");
    if (!toggle) return;
    var scope = toggle.closest("[data-bulk]") || document;
    scope.querySelectorAll('input[type="checkbox"][name="ids"]').forEach(function (box) {
      box.checked = toggle.checked;
    });
  });

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
