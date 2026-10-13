/* Favourites and "Read next": the star and + buttons of a listing, and the two boxes on the home page.
   Every button carries the address it talks to (data-url); nothing else is needed. */
(function () {
  "use strict";
  function call(method, url) {
    return fetch(url, { method: method, credentials: "same-origin" }).then(function (r) { return r.ok; }).catch(function () { return false; });
  }
  function hideEmptyBox(list) {
    if (list && !list.querySelector("li")) { var box = list.closest("section"); if (box) box.hidden = true; }
  }
  document.addEventListener("click", function (e) {
    var button = e.target.closest && e.target.closest(".pl-shelf-btn, .pl-shelf-remove, .pl-shelf-move");
    if (!button) return;
    e.preventDefault();
    var url = button.dataset.url;
    if (button.classList.contains("pl-shelf-btn")) {                    // a star or + in a listing: on <-> off
      var on = button.getAttribute("aria-pressed") === "true";
      call(on ? "DELETE" : "POST", url).then(function (ok) { if (ok) button.setAttribute("aria-pressed", on ? "false" : "true"); });
    } else if (button.classList.contains("pl-shelf-remove")) {          // the x in a home page box
      var row = button.closest("li"), list = row && row.parentNode;
      call("DELETE", url).then(function (ok) { if (ok && row) { row.remove(); hideEmptyBox(list); } });
    } else {                                                            // the arrows in the "Read next" box
      var item = button.closest("li"), up = /move=up/.test(url);
      call("POST", url).then(function (ok) {
        if (!ok || !item) return;
        var other = up ? item.previousElementSibling : item.nextElementSibling;
        if (other) { if (up) item.parentNode.insertBefore(item, other); else item.parentNode.insertBefore(other, item); }
      });
    }
  });
})();
