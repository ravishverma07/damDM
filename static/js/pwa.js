/*
 * DamDM as an installable app: registers the service worker, offers
 * "Install app", and manages push notifications on the Settings page.
 */
(function () {
  "use strict";

  var root = document.documentElement;
  var standalone = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  var isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  root.classList.toggle("is-standalone", standalone);

  function all(selector) { return Array.prototype.slice.call(document.querySelectorAll(selector)); }
  function meta(name) { var el = document.querySelector('meta[name="' + name + '"]'); return el ? el.content : ""; }

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function () {});
  }

  // --- install ---------------------------------------------------------
  var installPrompt = null;
  function showInstall(show) {
    all("[data-install-app], [data-install-block]").forEach(function (el) { el.hidden = !show; });
  }
  window.addEventListener("beforeinstallprompt", function (event) {
    event.preventDefault();
    installPrompt = event;
    showInstall(true);
  });
  window.addEventListener("appinstalled", function () { installPrompt = null; showInstall(false); });
  all("[data-install-app]").forEach(function (button) {
    button.addEventListener("click", function () {
      if (!installPrompt) return;
      installPrompt.prompt();
      installPrompt.userChoice.finally(function () { installPrompt = null; showInstall(false); });
    });
  });
  if (isIOS && !standalone) {
    all("[data-ios-install-tip]").forEach(function (el) { el.hidden = false; });
  }

  // --- push notifications ----------------------------------------------
  var publicKey = meta("vapid-public-key");
  if (!publicKey) return; // push is off, or nobody is signed in

  var supported = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

  function post(url, body) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": meta("csrf-token") },
      body: JSON.stringify(body || {}),
    }).then(function (response) {
      if (!response.ok) throw new Error("HTTP " + response.status);
      return response.json();
    });
  }

  function keyBytes(base64) {
    var padded = (base64 + "===".slice((base64.length + 3) % 4)).replace(/-/g, "+").replace(/_/g, "/");
    var raw = atob(padded);
    var bytes = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    return bytes;
  }

  function sameKey(subscription) {
    var key = subscription.options && subscription.options.applicationServerKey;
    if (!key) return true;
    var a = new Uint8Array(key), b = keyBytes(publicKey);
    if (a.length !== b.length) return false;
    for (var i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
    return true;
  }

  function subscribe(registration) {
    return registration.pushManager.getSubscription().then(function (existing) {
      if (existing && sameKey(existing)) return existing;
      return (existing ? existing.unsubscribe() : Promise.resolve()).then(function () {
        return registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(publicKey) });
      });
    }).then(function (subscription) {
      return post(meta("push-subscribe-url"), subscription.toJSON()).then(function () { return subscription; });
    });
  }

  // Keep the server's copy current (it may have changed, or belong to someone who logged out).
  if (supported && Notification.permission === "granted") {
    navigator.serviceWorker.ready.then(function (registration) {
      return registration.pushManager.getSubscription().then(function (subscription) {
        var synced = "";
        try { synced = sessionStorage.getItem("damdm-push-synced") || ""; } catch (e) {}
        if (subscription && synced !== subscription.endpoint) {
          return subscribe(registration).then(function (s) {
            try { sessionStorage.setItem("damdm-push-synced", s.endpoint); } catch (e) {}
          });
        }
      });
    }).catch(function () {});
  }

  var toggle = document.querySelector("[data-push-toggle]");
  if (!toggle) return;
  var status = document.querySelector("[data-push-status]");
  var testButton = document.querySelector("[data-push-test]");

  function say(text) { if (status) status.textContent = text; }
  function render(on) {
    toggle.checked = on;
    if (testButton) testButton.hidden = !on;
    say(on ? "On for this device." : "Get alerts when a payment fails, an automation stops or you earn a reward.");
  }

  if (!supported) {
    say(isIOS && !standalone
      ? "On iPhone, first add DamDM to your Home Screen, then turn this on from the app."
      : "This browser doesn't support notifications.");
    return;
  }
  if (Notification.permission === "denied") {
    say("Notifications are blocked. Allow them for this site in your browser settings, then reload.");
    return;
  }

  navigator.serviceWorker.ready.then(function (registration) {
    return registration.pushManager.getSubscription().then(function (subscription) {
      render(Boolean(subscription) && Notification.permission === "granted");
      toggle.disabled = false;

      toggle.addEventListener("change", function () {
        var wantOn = toggle.checked;
        toggle.disabled = true;
        var work = wantOn
          ? Notification.requestPermission().then(function (permission) {
              if (permission !== "granted") throw new Error("denied");
              return subscribe(registration).then(function () { render(true); });
            })
          : registration.pushManager.getSubscription().then(function (current) {
              if (!current) return;
              return post(meta("push-unsubscribe-url"), { endpoint: current.endpoint }).then(function () {
                return current.unsubscribe();
              });
            }).then(function () { render(false); });
        work.catch(function (error) {
          render(!wantOn);
          say(error && error.message === "denied"
            ? "Notifications weren't allowed. You can allow them in your browser settings."
            : "Something went wrong. Please try again.");
        }).finally(function () { toggle.disabled = false; });
      });
    });
  });

  if (testButton) {
    testButton.addEventListener("click", function () {
      testButton.disabled = true;
      post(meta("push-test-url")).then(function (result) {
        say(result.sent ? "Test sent. It should arrive in a few seconds." : "Couldn't reach this device. Turn notifications off and on again.");
      }).catch(function () { say("Something went wrong. Please try again."); })
        .finally(function () { testButton.disabled = false; });
    });
  }
})();
