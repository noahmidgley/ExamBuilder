/*
 * ExamBuilder's JavaScript. Everything here is an enhancement: every button
 * is a real HTML form, so the site still works if this file fails to load.
 *
 * 1. "Are you sure?" prompts on buttons with a data-confirm attribute.
 * 2. "Add to paper" without reloading the page (fetch + JSON).
 * 3. Question bank filters apply as soon as you tick a box.
 * 4. Drag-and-drop reordering in the paper builder.
 * 5. Tall question images start collapsed, with a "Show whole question" button.
 */
"use strict";

// The CSRF token from <meta name="csrf-token">: sent with every POST we make
// from JavaScript so the server knows the request came from our own page.
const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content;

async function postJSON(url, data) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken, Accept: "application/json" },
    body: JSON.stringify(data),
  });
  if (!response.ok) throw new Error(`Request failed (${response.status})`);
  return response.json();
}

function toast(message) {
  const el = document.getElementById("toast");
  if (!el) return;
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(el._timer);
  el._timer = setTimeout(() => el.classList.remove("show"), 2200);
}

function updateBadge(summary) {
  const badge = document.getElementById("basket-badge");
  if (badge) badge.textContent = `${summary.count} Q · ${summary.marks} marks`;
}

document.documentElement.classList.add("js"); // lets CSS hide no-JS-only buttons

// ---- 1. Confirmation prompts ----------------------------------------------
document.addEventListener("submit", (event) => {
  const button = event.submitter;
  if (button?.dataset.confirm && !window.confirm(button.dataset.confirm)) {
    event.preventDefault();
  }
});

// ---- 2. Add to / remove from paper ------------------------------------------
document.addEventListener("submit", async (event) => {
  const form = event.target.closest(".js-basket");
  if (!form) return;
  event.preventDefault();
  const button = form.querySelector("button");
  const added = button.dataset.added === "yes";
  const id = form.querySelector('input[name="id"]').value;
  button.disabled = true;
  try {
    const summary = await postJSON(added ? form.dataset.removeUrl : form.dataset.addUrl, { id });
    button.dataset.added = added ? "no" : "yes";
    button.textContent = added ? "+ Add to paper" : "✓ In paper (remove)";
    button.classList.toggle("added", !added);
    button.classList.toggle("primary", added);
    updateBadge(summary);
    toast(`${summary.message} Paper: ${summary.count} questions, ${summary.marks} marks.`);
  } catch (err) {
    toast("Sorry, that didn't work. Try reloading the page.");
  } finally {
    button.disabled = false;
  }
});

// ---- 3. Filters apply immediately -------------------------------------------
const filters = document.getElementById("filters");
if (filters) {
  filters.addEventListener("change", (event) => {
    if (event.target.matches('input[type="checkbox"]')) filters.submit();
  });
  // Clicking the checkbox inside a <summary> shouldn't also open/close the group.
  filters.querySelectorAll("summary input").forEach((box) =>
    box.addEventListener("click", (event) => event.stopPropagation()));
}

// ---- 4. Drag-and-drop reordering ---------------------------------------------
const list = document.getElementById("builder-list");
if (list) {
  let dragged = null;

  list.addEventListener("dragstart", (event) => {
    dragged = event.target.closest(".builder-item");
    dragged.classList.add("dragging");
    event.dataTransfer.effectAllowed = "move";
  });

  list.addEventListener("dragover", (event) => {
    event.preventDefault(); // allows dropping
    const over = event.target.closest(".builder-item");
    if (!over || over === dragged) return;
    // Insert before or after the item we're over, depending on which half we're in.
    const box = over.getBoundingClientRect();
    const after = event.clientY > box.top + box.height / 2;
    list.insertBefore(dragged, after ? over.nextSibling : over);
  });

  list.addEventListener("dragend", async () => {
    dragged.classList.remove("dragging");
    dragged = null;
    const items = [...list.querySelectorAll(".builder-item")];
    items.forEach((item, i) => (item.querySelector(".number").textContent = `Q${i + 1}`));
    try {
      await postJSON(list.dataset.reorderUrl, { ids: items.map((item) => Number(item.dataset.id)) });
      toast("Order saved.");
    } catch (err) {
      toast("Couldn't save the new order. Reloading…");
      window.location.reload();
    }
  });
}

// ---- 5. Collapse tall questions ----------------------------------------------
function markTall(frame) {
  const img = frame.querySelector("img");
  // Compare the image's displayed height with the collapsed height (380px).
  if (img.complete && img.naturalWidth) {
    frame.classList.toggle("tall", img.getBoundingClientRect().height > 420);
  }
}
document.querySelectorAll(".question-frame.collapsible").forEach((frame) => {
  const img = frame.querySelector("img");
  img.addEventListener("load", () => markTall(frame));
  markTall(frame);
  frame.querySelector(".expand-button").addEventListener("click", (event) => {
    const open = frame.classList.toggle("open");
    event.target.textContent = open ? "Show less ▴" : "Show whole question ▾";
  });
});
