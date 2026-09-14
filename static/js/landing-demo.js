/* Landing hero: a looping Instagram comment → public reply → DM demo. */
(function () {
  "use strict";

  var demo = document.querySelector("[data-demo]");
  if (!demo) return;

  // [milliseconds from start, class to add ("-" prefix removes it)]
  var TIMELINE = [
    [700, "is-1"],
    [1900, "is-2"],
    [3100, "is-3"],
    [4000, "is-4"],
    [5200, "is-notice"],
    [6500, "is-dm"],
    [6600, "-is-notice"],
    [7000, "is-typing"],
    [8400, "is-6"],
    [9900, "is-7"],
  ];
  var LOOP_MS = 13000;
  var STATES = ["is-1", "is-2", "is-3", "is-4", "is-notice", "is-dm", "is-typing", "is-6", "is-7"];
  var timers = [];

  function apply(step) {
    if (step.charAt(0) === "-") demo.classList.remove(step.slice(1));
    else demo.classList.add(step);
  }

  function stop() {
    timers.forEach(clearTimeout);
    timers = [];
  }

  function play() {
    stop();
    STATES.forEach(function (state) { demo.classList.remove(state); });
    TIMELINE.forEach(function (entry) {
      timers.push(setTimeout(function () { apply(entry[1]); }, entry[0]));
    });
    timers.push(setTimeout(play, LOOP_MS));
  }

  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    demo.classList.add("is-1", "is-2", "is-3", "is-4");
    return;
  }

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) stop();
    else play();
  });
  play();
})();
