/* Automation builder: show the steps that apply, keyword chips, live preview. */
(function () {
  "use strict";

  var form = document.querySelector("[data-builder]");
  if (!form) return;

  var SAMPLE_MESSAGES = {
    comment: "This is amazing 😍",
    dm_keyword: "Hey! 👋",
    story_reply: "Love this 🔥",
  };

  function checkedValue(name) {
    var input = form.querySelector('input[name="' + name + '"]:checked');
    return input ? input.value : "";
  }

  function isOn(name) {
    var input = form.querySelector('input[name="' + name + '"]');
    return Boolean(input && input.checked);
  }

  function fieldText(name) {
    var field = form.querySelector('[name="' + name + '"]');
    return field ? field.value.trim() : "";
  }

  function keywords() {
    return fieldText("keywords")
      .split(/[,\n]/)
      .map(function (word) { return word.trim(); })
      .filter(Boolean);
  }

  function lines(name) {
    return fieldText(name)
      .split("\n")
      .map(function (line) { return line.trim(); })
      .filter(Boolean);
  }

  function each(selector, callback) {
    form.querySelectorAll(selector).forEach(callback);
  }

  function setText(selector, value) {
    each(selector, function (element) { element.textContent = value; });
  }

  function renderChips() {
    var box = form.querySelector("[data-keyword-chips]");
    if (!box) return;
    box.replaceChildren();
    keywords().forEach(function (word, index) {
      var chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = word;
      var remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "×";
      remove.setAttribute("aria-label", "Remove " + word);
      remove.dataset.removeKeyword = String(index);
      chip.appendChild(remove);
      box.appendChild(chip);
    });
  }

  function updateVisibility(trigger) {
    var scope = checkedValue("post_scope") || "all_posts";
    each("[data-show-for]", function (element) {
      element.hidden = element.dataset.showFor.split(" ").indexOf(trigger) === -1;
    });
    each("[data-show-for-scope]", function (element) {
      element.hidden = element.dataset.showForScope !== scope;
    });
    var audience = checkedValue("audience") || "everyone";
    each("[data-show-for-audience]", function (element) {
      element.hidden = element.dataset.showForAudience !== audience;
    });
    each("[data-reveal]", function (input) {
      var target = document.getElementById(input.dataset.reveal);
      if (target) target.hidden = !input.checked;
    });
    each("[data-reveal-off]", function (input) {
      var target = document.getElementById(input.dataset.revealOff);
      if (target) target.hidden = input.checked;
    });

    // Only show posts from the chosen account, and untick the others.
    var account = form.querySelector('[name="account"]');
    var accountId = account ? account.value : "";
    each("[data-account]", function (tile) {
      var matches = !accountId || tile.dataset.account === accountId;
      tile.hidden = !matches;
      if (!matches) tile.querySelector("input").checked = false;
    });
  }

  function updatePreview(trigger) {
    var account = form.querySelector('select[name="account"]');
    var username = account
      ? account.options[account.selectedIndex].text
      : form.dataset.accountUsername || "";
    setText("[data-preview-account]", username.replace(/^@/, "") || "your_account");

    var words = keywords();
    var sample = isOn("reply_to_all") ? SAMPLE_MESSAGES[trigger] || "" : words[0] || "…";
    setText("[data-preview-keyword]", sample);

    var publicReplies = lines("public_replies");
    each("[data-preview-public]", function (element) {
      element.hidden = !isOn("public_reply_enabled");
    });
    setText("[data-preview-public-text]", publicReplies[0] || "Sent you a DM 📩");

    var audience = checkedValue("audience") || "everyone";
    each("[data-preview-follow]", function (element) {
      element.hidden = audience !== "follow_first";
    });
    each("[data-preview-followers]", function (element) {
      element.hidden = audience !== "followers";
    });
    setText("[data-preview-follow-text]", fieldText("follow_prompt") || "Follow me first, then tap below 👇");
    setText("[data-preview-follow-button]", fieldText("follow_button") || "I'm following");

    setText("[data-preview-dm-text]", fieldText("dm_text") || "Your message will appear here.");
    var buttonTitle = fieldText("button_title");
    var hasButton = Boolean(buttonTitle || fieldText("button_url"));
    each("[data-preview-button]", function (element) {
      element.hidden = !hasButton;
      element.textContent = buttonTitle || "Open link";
    });

    each("[data-count-for]", function (counter) {
      var input = document.getElementById(counter.dataset.countFor);
      if (!input) return;
      var max = input.getAttribute("maxlength");
      counter.textContent = input.value.length + (max ? " / " + max : "");
    });
  }

  function update() {
    var trigger = checkedValue("trigger_type") || "comment";
    updateVisibility(trigger);
    updatePreview(trigger);
    renderChips();
  }

  form.addEventListener("click", function (event) {
    var remove = event.target.closest("[data-remove-keyword]");
    if (!remove) return;
    var words = keywords();
    words.splice(Number(remove.dataset.removeKeyword), 1);
    form.querySelector('[name="keywords"]').value = words.join(", ");
    update();
  });

  form.addEventListener("input", update);
  form.addEventListener("change", update);
  update();
})();
