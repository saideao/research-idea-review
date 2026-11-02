{# 浅色/深色切换。放在 <head> 里，先套上记住的选择再画页面，免得闪一下。没选过就跟系统走 #}
(function () {
  var root = document.documentElement, key = "idea-review-theme", dark = matchMedia("(prefers-color-scheme: dark)");
  try { var saved = localStorage.getItem(key); if (saved === "light" || saved === "dark") root.dataset.theme = saved; } catch (e) {}
  function current() { return root.dataset.theme || (dark.matches ? "dark" : "light"); }
  document.addEventListener("DOMContentLoaded", function () {
    var btn = document.getElementById("theme-toggle");
    if (!btn) return;
    function label() { btn.textContent = current() === "dark" ? btn.dataset.toLight : btn.dataset.toDark; }
    btn.onclick = function () {
      root.dataset.theme = current() === "dark" ? "light" : "dark";
      try { localStorage.setItem(key, root.dataset.theme); } catch (e) {}
      label();
    };
    if (dark.addEventListener) dark.addEventListener("change", label);
    label();
    btn.hidden = false;
  });
})();
