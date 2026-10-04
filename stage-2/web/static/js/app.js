(function () {
  "use strict";

  // ---------------------------------------------------------------------
  // API client: a bearer token lives in localStorage; network failures are
  // distinguished from HTTP error responses so the UI can tell "refused" from
  // "uncertain" (spec: lost responses vs confirmed rejections).
  // ---------------------------------------------------------------------

  const TOKEN_KEY = "tk_token";
  const NAME_KEY = "tk_display_name";

  const Session = {
    token() { return localStorage.getItem(TOKEN_KEY); },
    displayName() { return localStorage.getItem(NAME_KEY); },
    set(token, displayName) {
      localStorage.setItem(TOKEN_KEY, token);
      localStorage.setItem(NAME_KEY, displayName);
    },
    clear() {
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(NAME_KEY);
    },
    signedIn() { return !!Session.token(); },
  };

  async function apiRequest(method, path, opts) {
    opts = opts || {};
    const headers = Object.assign({}, opts.headers || {});
    let body;
    if (opts.json !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(opts.json);
    }
    const token = Session.token();
    if (token) headers["Authorization"] = "Bearer " + token;
    if (opts.idempotencyKey) headers["Idempotency-Key"] = opts.idempotencyKey;

    let response;
    try {
      response = await fetch(path, { method: method, headers: headers, body: body });
    } catch (networkError) {
      const err = new Error("network failure");
      err.isNetworkError = true;
      throw err;
    }
    const text = await response.text();
    let parsed = null;
    if (text) {
      try { parsed = JSON.parse(text); } catch (e) { parsed = null; }
    }
    return { status: response.status, body: parsed, ok: response.status < 400 };
  }

  function errorMessage(body, fallback) {
    if (body && body.error && body.error.message) return body.error.message;
    return fallback || "Something went wrong. Please try again.";
  }

  // ---------------------------------------------------------------------
  // Shared header: current-user / logout-button / nav links.
  // ---------------------------------------------------------------------

  function renderHeader() {
    const nameEl = document.querySelector('[data-testid="current-user"]');
    const logoutBtn = document.querySelector('[data-testid="logout-button"]');
    const navLinks = document.querySelectorAll("[data-auth-nav]");
    if (!nameEl || !logoutBtn) return;
    if (Session.signedIn()) {
      nameEl.textContent = Session.displayName() || "";
      nameEl.hidden = false;
      logoutBtn.hidden = false;
      navLinks.forEach((a) => { a.hidden = true; });
    } else {
      nameEl.hidden = true;
      logoutBtn.hidden = true;
      navLinks.forEach((a) => { a.hidden = false; });
    }
  }

  function wireHeader() {
    renderHeader();
    const logoutBtn = document.querySelector('[data-testid="logout-button"]');
    if (logoutBtn) {
      logoutBtn.addEventListener("click", () => {
        Session.clear();
        renderHeader();
        if (document.body.dataset.page === "lookup" && window.onSessionCleared) {
          window.onSessionCleared();
        }
        if (document.body.dataset.page === "search" && window.onSessionCleared) {
          window.onSessionCleared();
        }
      });
    }
  }

  // ---------------------------------------------------------------------
  // Small helpers
  // ---------------------------------------------------------------------

  function newKey() {
    if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
    return "key-" + Date.now() + "-" + Math.random().toString(36).slice(2);
  }

  function clearChildren(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function friendlyTime(hhmm) {
    const parts = hhmm.split(":");
    let hour = parseInt(parts[0], 10);
    const minute = parts[1];
    const suffix = hour >= 12 ? "PM" : "AM";
    let hour12 = hour % 12;
    if (hour12 === 0) hour12 = 12;
    return hour12 + ":" + minute + " " + suffix;
  }

  function tableLabel(tableId, tablesById) {
    const t = tablesById[tableId];
    return t ? "Table " + t.label : tableId;
  }

  function labelsFor(tableIds, tablesById) {
    return tableIds.map((id) => tableLabel(id, tablesById)).join(" + ");
  }

  function arraysEqual(a, b) {
    return a.length === b.length && a.every((v, i) => v === b[i]);
  }

  function setsEqual(a, b) {
    if (a.length !== b.length) return false;
    const sa = a.slice().sort();
    const sb = b.slice().sort();
    return arraysEqual(sa, sb);
  }

  // Some elements must be absent from the DOM (not merely hidden) when not
  // applicable, e.g. `reservation-cancel-button` "absent once cancelled".
  function makePresenceToggle(el) {
    const parent = el.parentNode;
    const next = el.nextSibling;
    el.hidden = false; // DOM presence is now the only signal; never leave `hidden` masking a present node
    return function (show) {
      const inDom = parent.contains(el);
      if (show && !inDom) parent.insertBefore(el, next);
      else if (!show && inDom) parent.removeChild(el);
    };
  }

  // ---------------------------------------------------------------------
  // Signup / Login
  // ---------------------------------------------------------------------

  function initAuthForm(kind) {
    const form = document.getElementById(kind + "-form");
    const errorEl = document.querySelector('[data-testid="auth-error"]');
    const submitBtn = document.querySelector('[data-testid="' + kind + '-submit"]');
    if (!form) return;
    const toggleError = makePresenceToggle(errorEl);
    toggleError(false);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      toggleError(false);
      errorEl.textContent = "";
      submitBtn.disabled = true;
      const email = document.querySelector('[data-testid="' + kind + '-email"]').value;
      const password = document.querySelector('[data-testid="' + kind + '-password"]').value;
      const payload = { email: email, password: password };
      if (kind === "signup") {
        payload.display_name = document.querySelector('[data-testid="signup-display-name"]').value;
      }
      try {
        const resp = await apiRequest("POST", "/auth/" + kind, { json: payload });
        if (resp.ok) {
          Session.set(resp.body.token, resp.body.display_name);
          window.location.href = "/";
          return;
        }
        errorEl.textContent = errorMessage(resp.body, "Could not sign you in.");
        toggleError(true);
      } catch (err) {
        errorEl.textContent = "Could not reach the server. Please try again.";
        toggleError(true);
      } finally {
        submitBtn.disabled = false;
      }
    });
  }

  // ---------------------------------------------------------------------
  // Lookup
  // ---------------------------------------------------------------------

  function initLookupPage() {
    const form = document.getElementById("lookup-form");
    const input = document.querySelector('[data-testid="lookup-reference-input"]');
    const detail = document.querySelector('[data-testid="reservation-detail"]');
    const errorEl = document.querySelector('[data-testid="reservation-error"]');
    const statusEl = document.querySelector('[data-testid="reservation-status"]');
    const tablesEl = document.querySelector('[data-testid="reservation-tables"]');
    const summaryEl = document.getElementById("reservation-summary");
    const cancelBtn = document.querySelector('[data-testid="reservation-cancel-button"]');
    const toggleError = makePresenceToggle(errorEl);
    const toggleCancelBtn = makePresenceToggle(cancelBtn);
    toggleError(false);
    toggleCancelBtn(false);
    let current = null;
    const restaurantCache = {};

    async function restaurantDetail(id) {
      if (restaurantCache[id]) return restaurantCache[id];
      const resp = await apiRequest("GET", "/restaurants/" + encodeURIComponent(id));
      if (resp.ok) restaurantCache[id] = resp.body;
      return resp.body;
    }

    function showError(message) {
      detail.hidden = true;
      errorEl.textContent = message;
      toggleError(true);
    }

    async function showReservation(body) {
      current = body;
      toggleError(false);
      statusEl.textContent = body.status;
      const restaurant = await restaurantDetail(body.restaurant_id);
      const tablesById = {};
      (restaurant ? restaurant.tables : []).forEach((t) => { tablesById[t.id] = t; });
      const labels = labelsFor(body.table_ids || [body.table_id], tablesById);
      tablesEl.textContent = labels;
      summaryEl.textContent = (restaurant ? restaurant.name : body.restaurant_id) +
        " — " + labels + ", " + friendlyTime(body.starts_at_local.split("T")[1]);
      toggleCancelBtn(body.status === "confirmed");
      detail.hidden = false;
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const ref = input.value.trim();
      if (!ref) return;
      try {
        const resp = await apiRequest("GET", "/reservations/" + encodeURIComponent(ref));
        if (resp.ok) await showReservation(resp.body);
        else showError(errorMessage(resp.body, "That reservation could not be found."));
      } catch (err) {
        showError("Could not reach the server. Please try again.");
      }
    });

    cancelBtn.addEventListener("click", async () => {
      if (!current) return;
      try {
        const resp = await apiRequest("POST", "/reservations/" + encodeURIComponent(current.reference) + "/cancel");
        if (resp.ok) await showReservation(resp.body);
        else showError(errorMessage(resp.body, "That booking could not be cancelled."));
      } catch (err) {
        showError("Could not reach the server. Please try again.");
      }
    });

    window.onSessionCleared = () => {
      detail.hidden = true;
      current = null;
    };
  }

  // ---------------------------------------------------------------------
  // Search / availability / booking
  // ---------------------------------------------------------------------

  function initSearchPage() {
    const restaurantSelect = document.querySelector('[data-testid="restaurant-select"]');
    const dateInput = document.querySelector('[data-testid="date-input"]');
    const partySizeInput = document.querySelector('[data-testid="party-size-input"]');
    const searchButton = document.querySelector('[data-testid="search-button"]');
    const gridStatusLine = document.getElementById("grid-status-line");
    const gridTable = document.getElementById("availability-grid");
    const gridThead = document.getElementById("grid-thead");
    const gridTbody = document.getElementById("grid-tbody");
    const noSlotsEl = document.querySelector('[data-testid="no-slots"]');
    const bookingPanel = document.getElementById("booking-panel");
    const panelHome = bookingPanel.parentNode; // where the panel lives at desktop widths

    const MOBILE_BREAKPOINT = 768;
    let panelPlacementKey = null; // identifies which row (if any) currently holds the panel

    let searchGeneration = 0;
    let lastRowCount = 6;
    let lastSlots = [];
    let restaurantDetail = null;
    const restaurantCache = {};
    let chosen = null; // { restaurantId, tableIds, startsAtLocal, startsAt, partySize, key, outcome, data }

    const today = new Date();
    dateInput.value = today.toISOString().slice(0, 10);
    partySizeInput.value = "2";

    async function loadRestaurants() {
      const resp = await apiRequest("GET", "/restaurants");
      if (!resp.ok) return;
      clearChildren(restaurantSelect);
      (resp.body.restaurants || []).forEach((r) => {
        const option = document.createElement("option");
        option.value = r.id;
        option.textContent = r.name;
        restaurantSelect.appendChild(option);
      });
      if (restaurantSelect.options.length) {
        restaurantSelect.value = restaurantSelect.options[0].value;
      }
    }

    async function loadRestaurantDetail(id) {
      if (restaurantCache[id]) return restaurantCache[id];
      const resp = await apiRequest("GET", "/restaurants/" + encodeURIComponent(id));
      if (resp.ok) restaurantCache[id] = resp.body;
      return resp.body;
    }

    function tablesById() {
      const map = {};
      if (restaurantDetail) restaurantDetail.tables.forEach((t) => { map[t.id] = t; });
      return map;
    }

    function columns() {
      if (!restaurantDetail) return [];
      const cols = restaurantDetail.tables.map((t) => ({ ids: [t.id], capacity: t.capacity }));
      (restaurantDetail.combinable || []).forEach((pair) => {
        const map = tablesById();
        const capacity = pair.reduce((sum, id) => sum + (map[id] ? map[id].capacity : 0), 0);
        cols.push({ ids: pair.slice(), capacity: capacity });
      });
      return cols;
    }

    function cellTestId(ids, hhmm) {
      return "slot-" + ids.join("+") + "-" + hhmm;
    }

    // Shared by the real grid and the skeleton, so the header row (table
    // labels and seat counts) is identical and never reflows between them.
    function buildHeaderRow() {
      clearChildren(gridThead);
      const headRow = document.createElement("tr");
      const timeHeader = document.createElement("th");
      timeHeader.textContent = "Time";
      headRow.appendChild(timeHeader);
      const map = tablesById();
      columns().forEach((col) => {
        const th = document.createElement("th");
        const label = col.ids.map((id) => (map[id] ? map[id].label : id)).join(" + ");
        const strong = document.createElement("div");
        strong.textContent = label;
        const small = document.createElement("div");
        small.className = "cell-meta";
        small.textContent = col.capacity + " seats";
        th.appendChild(strong);
        th.appendChild(small);
        headRow.appendChild(th);
      });
      gridThead.appendChild(headRow);
    }

    // Skeleton rows keep the real header and the previous row count so the
    // grid never jumps size when results replace them (same <table>, same
    // column widths). Carries none of the specification's data-testids.
    function showSkeleton(rowCount) {
      noSlotsEl.hidden = true;
      buildHeaderRow();
      clearChildren(gridTbody);
      const colCount = Math.max(columns().length, 1);
      for (let r = 0; r < rowCount; r++) {
        const row = document.createElement("tr");
        const timeCell = document.createElement("td");
        timeCell.className = "time-cell";
        const timeBar = document.createElement("div");
        timeBar.className = "skeleton-bar short";
        timeCell.appendChild(timeBar);
        row.appendChild(timeCell);
        for (let c = 0; c < colCount; c++) {
          const td = document.createElement("td");
          const cell = document.createElement("div");
          cell.className = "cell cell--skeleton";
          const bar1 = document.createElement("div");
          bar1.className = "skeleton-bar";
          const bar2 = document.createElement("div");
          bar2.className = "skeleton-bar short";
          cell.appendChild(bar1);
          cell.appendChild(bar2);
          td.appendChild(cell);
          row.appendChild(td);
        }
        gridTbody.appendChild(row);
      }
      gridTable.hidden = false;
    }

    function cellState(slot, ids, partySize) {
      const map = tablesById();
      const capacity = ids.reduce((sum, id) => sum + (map[id] ? map[id].capacity : 0), 0);
      if (capacity < partySize) return "too-small";
      if (ids.length === 1) {
        return slot.available_table_ids.indexOf(ids[0]) !== -1 ? "available" : "taken";
      }
      const found = (slot.available_options || []).some((o) => arraysEqual(o.table_ids, ids));
      return found ? "available" : "taken";
    }

    function renderGrid(slots, partySize) {
      lastSlots = slots;
      lastRowCount = Math.max(slots.length, 1);
      if (!slots.length) {
        clearChildren(gridTbody);
        gridTable.hidden = true;
        noSlotsEl.hidden = false;
        positionPanel();
        return;
      }
      noSlotsEl.hidden = true;
      const cols = columns();
      const map = tablesById();

      buildHeaderRow();

      clearChildren(gridTbody);
      slots.forEach((slot) => {
        const hhmm = slot.starts_at_local.split("T")[1];
        const row = document.createElement("tr");
        row.dataset.startsAtLocal = slot.starts_at_local;
        const timeCell = document.createElement("td");
        timeCell.className = "time-cell";
        timeCell.textContent = hhmm;
        row.appendChild(timeCell);

        cols.forEach((col) => {
          const td = document.createElement("td");
          const state = cellState(slot, col.ids, partySize);
          const isChosenCell = chosen && chosen.startsAtLocal === slot.starts_at_local
            && setsEqual(chosen.tableIds, col.ids);
          const button = document.createElement("button");
          button.type = "button";
          button.className = "cell";
          const testId = cellTestId(col.ids, hhmm);
          button.setAttribute("data-testid", testId);
          const available = state === "available";
          button.setAttribute("data-available", available ? "true" : "false");

          const labelEl = document.createElement("span");
          labelEl.className = "cell-label";
          labelEl.textContent = col.ids.map((id) => (map[id] ? map[id].label : id)).join(" + ");
          const metaEl = document.createElement("span");
          metaEl.className = "cell-meta";
          metaEl.textContent = col.capacity + " seats";

          if (isChosenCell && chosen.outcome === "booked") {
            button.classList.add("cell--yours");
            metaEl.textContent = "Yours";
          } else if (isChosenCell && chosen.outcome === "refused" && chosen.refusalReason === "table_unavailable") {
            button.classList.add("cell--just-taken");
            metaEl.textContent = "Just taken";
          } else if (isChosenCell && chosen.outcome === "unsure") {
            button.classList.add("cell--unsure");
            metaEl.textContent = "Not confirmed";
          } else if (isChosenCell && !chosen.outcome) {
            button.classList.add("cell--chosen");
          } else if (state === "taken") {
            button.classList.add("cell--taken");
            metaEl.textContent = "Taken";
          } else if (state === "too-small") {
            button.classList.add("cell--too-small");
            metaEl.textContent = "Too small";
          }

          button.appendChild(labelEl);
          button.appendChild(metaEl);

          if (available || isChosenCell) {
            button.addEventListener("click", () => onCellClick(col.ids, slot, partySize));
          }
          td.appendChild(button);
          row.appendChild(td);
        });
        gridTbody.appendChild(row);
      });
      gridTable.hidden = false;
      positionPanel();
    }

    // Under 768px the panel lives under the grid until a cell is chosen;
    // then it moves into a row directly beneath that slot's time, as a
    // table row of its own, so the message, form and neighbouring times
    // stay on screen together. At desktop widths it stays beside the grid.
    function positionPanel() {
      const mobile = window.innerWidth < MOBILE_BREAKPOINT;
      const targetRow = (mobile && chosen && !gridTable.hidden)
        ? gridTbody.querySelector('tr[data-starts-at-local="' + chosen.startsAtLocal + '"]')
        : null;

      if (bookingPanel.parentNode !== panelHome) panelHome.appendChild(bookingPanel);

      if (!targetRow) {
        panelPlacementKey = null;
        return;
      }

      // The grid is fully rebuilt on every render, so the row that used to
      // hold the panel is already gone; always reattach, but only move
      // focus when the chosen slot itself has actually changed, so re-renders
      // triggered by typing or a retry don't steal focus away.
      const key = chosen.startsAtLocal + "|" + chosen.tableIds.join("+");
      const isNewPlacement = panelPlacementKey !== key;

      const colCount = Math.max(columns().length, 1) + 1;
      const row = document.createElement("tr");
      row.className = "panel-row";
      const td = document.createElement("td");
      td.colSpan = colCount;
      td.appendChild(bookingPanel);
      row.appendChild(td);
      targetRow.parentNode.insertBefore(row, targetRow.nextSibling);
      panelPlacementKey = key;
      if (isNewPlacement) bookingPanel.focus();
    }

    function onCellClick(ids, slot, partySize) {
      if (!Session.signedIn()) {
        showPanelAuthError();
        return;
      }
      chosen = {
        restaurantId: restaurantSelect.value,
        tableIds: ids.slice(),
        startsAtLocal: slot.starts_at_local,
        startsAt: slot.starts_at,
        partySize: partySize,
        key: newKey(),
        outcome: null,
        refusalReason: null,
        data: null,
      };
      renderGrid(lastSlots, partySize);
      renderPanel();
    }

    function showPanelAuthError() {
      clearChildren(bookingPanel);
      const msg = document.createElement("p");
      msg.setAttribute("data-testid", "auth-error");
      msg.className = "panel-hint";
      msg.textContent = "Sign in to book a table.";
      bookingPanel.appendChild(msg);
      const link = document.createElement("a");
      link.href = "/login";
      link.textContent = "Go to login";
      bookingPanel.appendChild(link);
    }

    function renderPanelIdle(searching) {
      clearChildren(bookingPanel);
      const hint = document.createElement("p");
      hint.className = "panel-hint";
      hint.textContent = searching ? "Searching for tables…" : "Pick a table to book it.";
      bookingPanel.appendChild(hint);
    }

    function renderPanel() {
      if (!chosen) {
        renderPanelIdle(false);
        return;
      }
      clearChildren(bookingPanel);
      const map = tablesById();
      const labels = labelsFor(chosen.tableIds, map);
      const hhmm = chosen.startsAtLocal.split("T")[1];
      const timeText = friendlyTime(hhmm);

      if (chosen.outcome === "booked" && chosen.data) {
        const card = document.createElement("div");
        card.className = "booked-card";
        card.setAttribute("data-testid", "confirmation");
        const title = document.createElement("p");
        title.className = "booked-title";
        title.textContent = "Reserved";
        const ref = document.createElement("span");
        ref.setAttribute("data-testid", "confirmation-reference");
        ref.textContent = chosen.data.reference;
        const details = document.createElement("div");
        details.setAttribute("data-testid", "confirmation-details");
        details.textContent = (restaurantDetail ? restaurantDetail.name : "") +
          ", " + labels + ", " + timeText + ", party of " + chosen.data.party_size;
        const tablesText = document.createElement("div");
        tablesText.className = "form-note";
        tablesText.setAttribute("data-testid", "confirmation-tables");
        tablesText.textContent = labels;
        const link = document.createElement("a");
        link.className = "booked-link";
        link.href = "/lookup";
        link.textContent = "View or cancel this booking";
        card.appendChild(title);
        card.appendChild(ref);
        card.appendChild(details);
        card.appendChild(tablesText);
        card.appendChild(link);
        bookingPanel.appendChild(card);
      } else if (chosen.outcome === "refused") {
        const card = document.createElement("div");
        card.className = "status-card status-card--refused";
        card.setAttribute("data-testid", "booking-error");
        const text = document.createElement("span");
        text.className = "status-first";
        text.textContent = chosen.refusalReason === "table_unavailable"
          ? "That table was just taken. Pick another time or table."
          : (chosen.errorText || "That booking could not be completed.");
        card.appendChild(text);
        bookingPanel.appendChild(card);
      } else if (chosen.outcome === "unsure") {
        const card = document.createElement("div");
        card.className = "status-card status-card--unsure";
        card.setAttribute("data-testid", "booking-uncertain");
        const text = document.createElement("span");
        text.className = "status-first";
        text.textContent = "We didn't get an answer. Your table may already be booked. Try again: you won't be booked twice.";
        card.appendChild(text);
        bookingPanel.appendChild(card);
      }

      const form = document.createElement("form");
      form.setAttribute("data-testid", "booking-form");
      form.id = "booking-form";

      const summary = document.createElement("p");
      summary.setAttribute("data-testid", "booking-summary");
      let summaryText = labels + ", " + timeText;
      if (chosen.outcome === "booked") summaryText = "✓ " + summaryText;
      summary.textContent = summaryText;
      if (chosen.outcome === "booked") {
        const tag = document.createElement("span");
        tag.className = "form-tag form-tag--yours";
        tag.textContent = "Yours";
        summary.appendChild(tag);
      } else if (chosen.outcome === "refused" && chosen.refusalReason === "table_unavailable") {
        const tag = document.createElement("span");
        tag.className = "form-tag form-tag--taken";
        tag.textContent = "Taken";
        summary.appendChild(tag);
      }
      form.appendChild(summary);

      if (chosen.outcome === "booked") {
        const note = document.createElement("p");
        note.className = "form-note";
        note.textContent = "This table is booked for you.";
        form.appendChild(note);
      }

      const sizeField = document.createElement("div");
      sizeField.className = "field";
      const sizeLabel = document.createElement("label");
      sizeLabel.setAttribute("for", "booking-party-size");
      sizeLabel.textContent = "Party size";
      const sizeInput = document.createElement("input");
      sizeInput.type = "number";
      sizeInput.min = "1";
      sizeInput.id = "booking-party-size";
      sizeInput.className = "control";
      sizeInput.setAttribute("data-testid", "booking-party-size");
      sizeInput.value = String(chosen.partySize);
      sizeField.appendChild(sizeLabel);
      sizeField.appendChild(sizeInput);
      form.appendChild(sizeField);

      const submitBtn = document.createElement("button");
      submitBtn.type = "submit";
      submitBtn.setAttribute("data-testid", "booking-submit");
      submitBtn.className = "btn " + (chosen.outcome === "booked" ? "btn-outline" : "btn-fill");
      submitBtn.textContent = chosen.outcome === "unsure" ? "Try again"
        : chosen.outcome === "booked" ? "Book again" : "Book this table";
      form.appendChild(submitBtn);

      sizeInput.addEventListener("input", () => {
        const value = parseInt(sizeInput.value, 10);
        if (!value || value === chosen.partySize) return;
        chosen.partySize = value;
        chosen.key = newKey();
        chosen.outcome = null;
        chosen.refusalReason = null;
        chosen.data = null;
        renderGrid(lastSlots, parseInt(partySizeInput.value, 10) || chosen.partySize);
        renderPanel();
      });

      form.addEventListener("submit", (event) => {
        event.preventDefault();
        submitBooking(submitBtn);
      });

      bookingPanel.appendChild(form);
    }

    async function submitBooking(submitBtn) {
      if (!chosen) return;
      const originalText = submitBtn.textContent;
      submitBtn.disabled = true;
      submitBtn.textContent = "Booking…";
      const body = {
        restaurant_id: chosen.restaurantId,
        table_ids: chosen.tableIds,
        starts_at_local: chosen.startsAtLocal,
        party_size: chosen.partySize,
      };
      try {
        const resp = await apiRequest("POST", "/reservations", { json: body, idempotencyKey: chosen.key });
        if (resp.ok) {
          chosen.outcome = "booked";
          chosen.data = resp.body;
          chosen.refusalReason = null;
        } else {
          chosen.outcome = "refused";
          chosen.refusalReason = resp.body && resp.body.error ? resp.body.error.code : "error";
          chosen.errorText = errorMessage(resp.body, "That booking could not be completed.");
          if (chosen.refusalReason === "table_unavailable") {
            runSearch();
          }
        }
      } catch (err) {
        chosen.outcome = "unsure";
      } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = originalText;
        renderGrid(lastSlots, parseInt(partySizeInput.value, 10) || chosen.partySize);
        renderPanel();
      }
    }

    async function runSearch() {
      const restaurantId = restaurantSelect.value;
      const date = dateInput.value;
      const partySize = parseInt(partySizeInput.value, 10);
      if (!restaurantId || !date || !partySize || partySize < 1) return;

      searchGeneration += 1;
      const myGeneration = searchGeneration;
      searchButton.disabled = true;
      searchButton.textContent = "Searching…";
      gridStatusLine.textContent = "";
      if (!chosen) renderPanelIdle(true);

      try {
        const detail = await loadRestaurantDetail(restaurantId);
        if (myGeneration !== searchGeneration) return;
        restaurantDetail = detail;
        showSkeleton(lastRowCount);
        const qs = "?restaurant_id=" + encodeURIComponent(restaurantId) +
          "&date=" + encodeURIComponent(date) + "&party_size=" + encodeURIComponent(String(partySize));
        const availResp = await apiRequest("GET", "/availability" + qs);
        if (myGeneration !== searchGeneration) return; // a later search already started
        if (!availResp.ok) {
          gridTable.hidden = true;
          gridStatusLine.textContent = errorMessage(availResp.body, "Could not load availability.");
          if (!chosen) renderPanelIdle(false);
          positionPanel();
          return;
        }
        renderGrid(availResp.body.slots, partySize);
        if (!chosen) renderPanelIdle(false);
      } catch (err) {
        if (myGeneration !== searchGeneration) return;
        gridTable.hidden = true;
        gridStatusLine.textContent = "Could not reach the server. Please try again.";
        if (!chosen) renderPanelIdle(false);
        positionPanel();
      } finally {
        if (myGeneration === searchGeneration) {
          searchButton.disabled = false;
          searchButton.textContent = "Search";
        }
      }
    }

    searchButton.addEventListener("click", runSearch);
    restaurantSelect.addEventListener("change", () => {
      chosen = null;
      renderPanelIdle(false);
      positionPanel();
      runSearch();
    });

    window.onSessionCleared = () => {
      chosen = null;
      renderPanelIdle(false);
      positionPanel();
    };

    let resizeTimer = null;
    window.addEventListener("resize", () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(positionPanel, 120);
    });

    loadRestaurants().then(() => runSearch());
  }

  document.addEventListener("DOMContentLoaded", () => {
    wireHeader();
    const page = document.body.dataset.page;
    if (page === "search") initSearchPage();
    else if (page === "signup") initAuthForm("signup");
    else if (page === "login") initAuthForm("login");
    else if (page === "lookup") initLookupPage();
  });
})();
