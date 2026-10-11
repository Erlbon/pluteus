/* The CSV/TSV table viewer: click a column header to sort, type in the box to filter rows. Nothing here touches the
   server; the table is already in the page. Numbers (columns marked data-num) sort as numbers. */
(function () {
  "use strict";
  var table = document.getElementById("table");
  if (!table || !table.tBodies.length) return;
  var body = table.tBodies[0];
  var rows = Array.prototype.slice.call(body.rows);
  var heads = Array.prototype.slice.call(table.tHead.rows[0].cells);
  var filter = document.getElementById("filter");
  var count = document.getElementById("count");
  var baseCount = count ? count.innerHTML : "";
  var text = rows.map(function (r) { return r.textContent.toLowerCase(); });
  var sorted = -1, ascending = true;

  function number(s) {
    var n = parseFloat(String(s).replace(/[\s']/g, "").replace(",", "."));
    return isNaN(n) ? null : n;
  }

  function sortBy(col) {
    ascending = sorted === col ? !ascending : true;
    sorted = col;
    var numeric = heads[col].dataset.num === "1";
    var keyed = rows.map(function (r, i) { return { r: r, i: i, t: r.cells[col].textContent }; });
    keyed.sort(function (a, b) {
      var x, y, d;
      if (numeric) {
        x = number(a.t); y = number(b.t);
        if (x === null && y === null) d = 0;
        else if (x === null) return 1;                      // empty cells always last
        else if (y === null) return -1;
        else d = x - y;
      } else {
        if (a.t === "" && b.t !== "") return 1;
        if (b.t === "" && a.t !== "") return -1;
        d = a.t.localeCompare(b.t, undefined, { numeric: true, sensitivity: "base" });
      }
      return (ascending ? d : -d) || a.i - b.i;
    });
    var fragment = document.createDocumentFragment();
    keyed.forEach(function (k) { fragment.appendChild(k.r); });
    body.appendChild(fragment);
    heads.forEach(function (h, i) {
      h.removeAttribute("aria-sort");
      h.classList.toggle("pl-sorted-asc", i === col && ascending);
      h.classList.toggle("pl-sorted-desc", i === col && !ascending);
    });
    heads[col].setAttribute("aria-sort", ascending ? "ascending" : "descending");
  }

  heads.forEach(function (h, i) {
    h.tabIndex = 0;
    h.addEventListener("click", function () { sortBy(i); });
    h.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); sortBy(i); }
    });
  });

  if (filter) {
    filter.addEventListener("input", function () {
      var needle = filter.value.trim().toLowerCase();
      var shown = 0;
      rows.forEach(function (r, i) {
        var hit = needle === "" || text[i].indexOf(needle) !== -1;
        r.hidden = !hit;
        if (hit) shown += 1;
      });
      if (count) count.innerHTML = needle === "" ? baseCount : baseCount + " &middot; " + shown + " match";
    });
  }
})();
