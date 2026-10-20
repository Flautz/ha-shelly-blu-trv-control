/*
 * Shelly BLU TRV schedule card.
 *
 * Shows the weekly schedule stored on a Shelly BLU TRV and lets you edit
 * it. Talks to the shelly_blu_trv_control integration via websocket.
 */
const DOMAIN = "shelly_blu_trv_control";
const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const MAX_RULES = 10;

const I18N = {
  en: {
    days: ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"],
    loading: "Reading schedule from the thermostat (Bluetooth)…",
    saving: "Writing schedule to the thermostat…",
    no_rules: "No schedule rules on this thermostat.",
    add: "Add rule",
    save: "Save",
    discard: "Discard",
    reload: "Reload from thermostat",
    unsaved: "Unsaved changes",
    saved: "Saved",
    copy_to: "Copy schedule to",
    copy: "Copy",
    copied: "Schedule copied",
    temp: "°C",
    pos: "Valve %",
    enabled: "Active",
    delete: "Delete",
    advanced: "Advanced timespec",
    max_rules: `The thermostat supports at most ${MAX_RULES} rules.`,
    need_days: "Select at least one weekday for every rule.",
    need_time: "Every rule needs a time.",
    temp_range: (min, max) => `Temperatures must be between ${min} and ${max} °C.`,
    no_entity: "Please select the schedule sensor of a BLU TRV.",
    not_found: "Entity not found",
    confirm_discard: "Discard unsaved changes?",
    off: "Schedule paused (all rules inactive)",
    disconnected: "Thermostat not connected to the gateway",
  },
  de: {
    days: ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"],
    loading: "Lese Zeitplan vom Thermostat (Bluetooth)…",
    saving: "Schreibe Zeitplan auf das Thermostat…",
    no_rules: "Auf diesem Thermostat sind keine Zeitplanregeln gespeichert.",
    add: "Regel hinzufügen",
    save: "Speichern",
    discard: "Verwerfen",
    reload: "Vom Thermostat neu laden",
    unsaved: "Ungespeicherte Änderungen",
    saved: "Gespeichert",
    copy_to: "Zeitplan kopieren nach",
    copy: "Kopieren",
    copied: "Zeitplan kopiert",
    temp: "°C",
    pos: "Ventil %",
    enabled: "Aktiv",
    delete: "Löschen",
    advanced: "Erweiterter Timespec",
    max_rules: `Das Thermostat unterstützt höchstens ${MAX_RULES} Regeln.`,
    need_days: "Bitte für jede Regel mindestens einen Wochentag wählen.",
    need_time: "Jede Regel braucht eine Uhrzeit.",
    temp_range: (min, max) => `Temperaturen müssen zwischen ${min} und ${max} °C liegen.`,
    no_entity: "Bitte den Zeitplan-Sensor eines BLU TRV auswählen.",
    not_found: "Entität nicht gefunden",
    confirm_discard: "Ungespeicherte Änderungen verwerfen?",
    off: "Zeitplan pausiert (alle Regeln inaktiv)",
    disconnected: "Thermostat nicht mit dem Gateway verbunden",
  },
};

function tempColor(t) {
  // 5 °C blue → 18 °C yellow → 24 °C+ red
  const stops = [
    [5, [66, 133, 244]],
    [15, [77, 182, 172]],
    [18, [253, 216, 53]],
    [21, [255, 152, 0]],
    [24, [229, 57, 53]],
  ];
  if (t <= stops[0][0]) return `rgb(${stops[0][1]})`;
  for (let i = 1; i < stops.length; i++) {
    if (t <= stops[i][0]) {
      const [t0, c0] = stops[i - 1];
      const [t1, c1] = stops[i];
      const f = (t - t0) / (t1 - t0);
      return `rgb(${c0.map((c, k) => Math.round(c + f * (c1[k] - c))).join(",")})`;
    }
  }
  return `rgb(${stops[stops.length - 1][1]})`;
}

function minutes(time) {
  const [h, m] = time.split(":").map(Number);
  return h * 60 + m;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

let keySeq = 0;

class ShellyTrvScheduleCard extends HTMLElement {
  static getConfigForm() {
    return {
      schema: [
        {
          name: "entity",
          required: true,
          selector: { entity: { filter: { integration: DOMAIN, domain: "sensor" } } },
        },
        { name: "title", selector: { text: {} } },
      ],
    };
  }

  static getStubConfig(hass) {
    const ent = Object.values(hass.entities || {}).find(
      (e) => e.platform === DOMAIN && e.entity_id.startsWith("sensor.")
    );
    return { entity: ent ? ent.entity_id : "" };
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._rules = null; // editable copy
    this._meta = null; // limits etc. from the backend
    this._dirty = false;
    this._busy = null; // "loading" | "saving" | null
    this._error = null;
    this._info = null;
    this._loadedRev = undefined;
    this._targets = null;
  }

  setConfig(config) {
    if (!config || !config.entity) throw new Error("entity is required");
    const changed = !this._config || this._config.entity !== config.entity;
    this._config = config;
    if (changed) {
      this._rules = null;
      this._dirty = false;
      this._loadedRev = undefined;
    }
    this._render();
  }

  getCardSize() {
    return 8;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6 };
  }

  get _t() {
    const lang = (this._hass && this._hass.language) || "en";
    return I18N[lang.split("-")[0]] || I18N.en;
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (!this._config) return;
    const st = hass.states[this._config.entity];
    if (!st) {
      if (first) this._render();
      return;
    }
    const rev = st.attributes.schedule_rev;
    if (this._busy) return;
    if (this._rules === null && !this._error) {
      this._load(false);
    } else if (!this._dirty && rev !== undefined && rev !== null && rev !== this._loadedRev) {
      // Schedule changed elsewhere (Shelly app, service call, other card).
      this._load(false);
    } else if (first) {
      this._render();
    }
    if (this._targets === null) this._loadTargets();
  }

  async _loadTargets() {
    this._targets = [];
    try {
      const list = await this._hass.callWS({ type: `${DOMAIN}/list` });
      this._targets = list.filter((t) => t.entity_id !== this._config.entity);
      this._renderFooter();
    } catch (e) {
      /* copy is optional */
    }
  }

  _name(entityId) {
    const hass = this._hass;
    const ent = hass.entities && hass.entities[entityId];
    const dev = ent && ent.device_id && hass.devices && hass.devices[ent.device_id];
    if (dev) return dev.name_by_user || dev.name;
    const st = hass.states[entityId];
    return (st && st.attributes.friendly_name) || entityId;
  }

  async _load(refresh) {
    this._busy = "loading";
    this._error = null;
    this._render();
    try {
      const res = await this._hass.callWS({
        type: `${DOMAIN}/get`,
        entity_id: this._config.entity,
        refresh,
      });
      this._apply(res);
    } catch (e) {
      this._error = e.message || String(e);
      if (this._rules === null) this._rules = [];
    }
    this._busy = null;
    this._render();
  }

  _apply(res) {
    this._meta = res;
    this._loadedRev = res.schedule_rev;
    this._rules = res.rules.map((r) => ({
      _key: ++keySeq,
      rule_id: r.rule_id,
      enable: r.enable !== false,
      advanced: r.time === null,
      timespec: r.timespec,
      time: r.time || "06:00",
      days: new Set(r.days || []),
      mode: r.pos !== undefined && r.pos !== null ? "pos" : "temp",
      target_C: r.target_C ?? 20,
      pos: r.pos ?? 0,
    }));
    this._dirty = false;
  }

  _payload() {
    const t = this._t;
    const min = this._meta ? this._meta.min_target_C : 4;
    const max = this._meta ? this._meta.max_target_C : 30;
    if (this._rules.length > MAX_RULES) throw new Error(t.max_rules);
    return this._rules.map((r) => {
      const out = { enable: r.enable };
      if (r.rule_id !== undefined && r.rule_id !== null) out.rule_id = r.rule_id;
      if (r.advanced) {
        out.timespec = r.timespec;
      } else {
        if (!r.time) throw new Error(t.need_time);
        if (!r.days.size) throw new Error(t.need_days);
        out.time = r.time;
        out.days = DAYS.filter((d) => r.days.has(d));
      }
      if (r.mode === "pos") {
        out.pos = Math.round(Number(r.pos));
      } else {
        const v = Number(r.target_C);
        if (!(v >= min && v <= max)) throw new Error(t.temp_range(min, max));
        out.target_C = v;
      }
      return out;
    });
  }

  async _save() {
    let rules;
    try {
      rules = this._payload();
    } catch (e) {
      this._error = e.message;
      this._renderFooter();
      return;
    }
    this._busy = "saving";
    this._error = null;
    this._info = null;
    this._render();
    try {
      const res = await this._hass.callWS({
        type: `${DOMAIN}/set`,
        entity_id: this._config.entity,
        rules,
      });
      this._apply(res);
      this._info = this._t.saved;
    } catch (e) {
      this._error = e.message || String(e);
    }
    this._busy = null;
    this._render();
  }

  async _copy() {
    const sel = this.shadowRoot.getElementById("copy-target");
    if (!sel || !sel.value) return;
    let rules;
    try {
      rules = this._payload().map(({ rule_id, ...r }) => r);
    } catch (e) {
      this._error = e.message;
      this._renderFooter();
      return;
    }
    this._busy = "saving";
    this._error = null;
    this._info = null;
    this._render();
    try {
      await this._hass.callWS({ type: `${DOMAIN}/set`, entity_id: sel.value, rules });
      this._info = `${this._t.copied}: ${this._name(sel.value)}`;
    } catch (e) {
      this._error = e.message || String(e);
    }
    this._busy = null;
    this._render();
  }

  _changed() {
    this._dirty = true;
    this._info = null;
    this._renderWeek();
    this._renderFooter();
  }

  /* ---------- rendering ---------- */

  _render() {
    const root = this.shadowRoot;
    const t = this._t;
    if (!this._config) return;
    const title =
      this._config.title || (this._hass ? this._name(this._config.entity) : this._config.entity);
    const st = this._hass && this._hass.states[this._config.entity];
    root.innerHTML = `
      <style>${STYLE}</style>
      <ha-card>
        <div class="header">
          <div class="title">${esc(title)}</div>
          <button class="icon" id="reload" title="${esc(t.reload)}" ${this._busy ? "disabled" : ""}>
            <ha-icon icon="mdi:refresh"></ha-icon>
          </button>
        </div>
        <div class="content">
          ${!this._hass ? "" : !st ? `<div class="msg error">${esc(t.not_found)}: ${esc(this._config.entity)}</div>` : ""}
          ${this._meta && this._meta.connected === false ? `<div class="msg warn">${esc(t.disconnected)}</div>` : ""}
          ${this._busy ? `<div class="msg busy"><span class="spinner"></span>${esc(t[this._busy])}</div>` : ""}
          <div id="week"></div>
          <div id="rules"></div>
          <div id="footer"></div>
        </div>
      </ha-card>`;
    root.getElementById("reload").addEventListener("click", () => {
      if (this._dirty && !confirm(t.confirm_discard)) return;
      this._load(true);
    });
    if (this._rules === null) return;
    this._renderWeek();
    this._renderRules();
    this._renderFooter();
  }

  _events() {
    // Weekly list of [minuteOfWeek, rule] for enabled, parseable rules.
    const ev = [];
    for (const r of this._rules || []) {
      if (!r.enable || r.advanced || !r.time) continue;
      const m = minutes(r.time);
      DAYS.forEach((d, i) => {
        if (r.days.has(d)) ev.push([i * 1440 + m, r]);
      });
    }
    return ev.sort((a, b) => a[0] - b[0]);
  }

  _renderWeek() {
    const el = this.shadowRoot.getElementById("week");
    if (!el || this._rules === null) return;
    const t = this._t;
    const ev = this._events();
    if (!ev.length) {
      el.innerHTML = this._rules.length
        ? `<div class="msg">${esc(t.off)}</div>`
        : `<div class="msg">${esc(t.no_rules)}</div>`;
      return;
    }
    const label = (r) => (r.mode === "pos" ? `${Math.round(r.pos)}%` : `${Number(r.target_C)}°`);
    const color = (r) => (r.mode === "pos" ? "var(--disabled-color, #9e9e9e)" : tempColor(Number(r.target_C)));
    const now = new Date();
    const today = (now.getDay() + 6) % 7;
    const nowPct = ((now.getHours() * 60 + now.getMinutes()) / 1440) * 100;
    let rows = "";
    DAYS.forEach((d, i) => {
      const start = i * 1440;
      const end = start + 1440;
      // value at the start of the day = last event before it (week wraps)
      let cur = ev[ev.length - 1][1];
      for (const [m, r] of ev) if (m <= start) cur = r;
      const segs = [];
      let pos = start;
      for (const [m, r] of ev) {
        if (m <= start || m >= end) continue;
        segs.push([pos, m, cur]);
        pos = m;
        cur = r;
      }
      segs.push([pos, end, cur]);
      const bar = segs
        .filter(([a, b]) => b > a)
        .map(([a, b, r]) => {
          const w = ((b - a) / 1440) * 100;
          const from = `${String(Math.floor((a - start) / 60)).padStart(2, "0")}:${String((a - start) % 60).padStart(2, "0")}`;
          return `<div class="seg" style="left:${((a - start) / 1440) * 100}%;width:${w}%;background:${color(r)}" title="${from} → ${esc(label(r))}">${w > 7 ? esc(label(r)) : ""}</div>`;
        })
        .join("");
      rows += `<div class="day ${i === today ? "today" : ""}"><span>${t.days[i]}</span><div class="bar">${bar}${i === today ? `<div class="now" style="left:${nowPct}%"></div>` : ""}</div></div>`;
    });
    const ticks = [0, 3, 6, 9, 12, 15, 18, 21, 24]
      .map((h) => `<span style="left:${(h / 24) * 100}%">${h}</span>`)
      .join("");
    el.innerHTML = `<div class="week">${rows}<div class="day"><span></span><div class="ticks">${ticks}</div></div></div>`;
  }

  _renderRules() {
    const el = this.shadowRoot.getElementById("rules");
    if (!el) return;
    const t = this._t;
    const min = this._meta ? this._meta.min_target_C : 4;
    const max = this._meta ? this._meta.max_target_C : 30;
    const dis = this._busy ? "disabled" : "";
    el.innerHTML = this._rules
      .map((r) => {
        const when = r.advanced
          ? `<input class="timespec" data-f="timespec" type="text" value="${esc(r.timespec)}" title="${esc(t.advanced)}" ${dis}>`
          : `<input class="time" data-f="time" type="time" value="${esc(r.time)}" ${dis}>
             <div class="chips">${DAYS.map(
               (d, i) =>
                 `<button class="chip ${r.days.has(d) ? "on" : ""}" data-day="${d}" ${dis}>${t.days[i]}</button>`
             ).join("")}</div>`;
        const value =
          r.mode === "pos"
            ? `<input class="num" data-f="pos" type="number" min="0" max="100" step="1" value="${esc(r.pos)}" ${dis}>`
            : `<input class="num" data-f="target_C" type="number" min="${min}" max="${max}" step="0.5" value="${esc(r.target_C)}" ${dis}>`;
        return `<div class="rule ${r.enable ? "" : "inactive"}" data-key="${r._key}">
            <input type="checkbox" data-f="enable" title="${esc(t.enabled)}" ${r.enable ? "checked" : ""} ${dis}>
            ${when}
            <div class="value">${value}
              <select data-f="mode" ${dis}>
                <option value="temp" ${r.mode === "temp" ? "selected" : ""}>${esc(t.temp)}</option>
                <option value="pos" ${r.mode === "pos" ? "selected" : ""}>${esc(t.pos)}</option>
              </select>
            </div>
            <button class="icon del" title="${esc(t.delete)}" ${dis}><ha-icon icon="mdi:delete-outline"></ha-icon></button>
          </div>`;
      })
      .join("");

    el.querySelectorAll(".rule").forEach((row) => {
      const rule = this._rules.find((r) => String(r._key) === row.dataset.key);
      row.querySelectorAll("[data-f]").forEach((input) => {
        const f = input.dataset.f;
        const ev = input.type === "checkbox" || input.tagName === "SELECT" ? "change" : "input";
        input.addEventListener(ev, () => {
          if (f === "enable") {
            rule.enable = input.checked;
            row.classList.toggle("inactive", !rule.enable);
          } else if (f === "mode") {
            rule.mode = input.value;
            this._changed();
            this._renderRules();
            return;
          } else {
            rule[f] = input.value;
          }
          this._changed();
        });
      });
      row.querySelectorAll(".chip").forEach((chip) => {
        chip.addEventListener("click", () => {
          const d = chip.dataset.day;
          if (rule.days.has(d)) rule.days.delete(d);
          else rule.days.add(d);
          chip.classList.toggle("on", rule.days.has(d));
          this._changed();
        });
      });
      row.querySelector(".del").addEventListener("click", () => {
        this._rules = this._rules.filter((r) => r !== rule);
        this._changed();
        this._renderRules();
      });
    });
  }

  _renderFooter() {
    const el = this.shadowRoot.getElementById("footer");
    if (!el || this._rules === null) return;
    const t = this._t;
    const dis = this._busy ? "disabled" : "";
    const full = this._rules.length >= MAX_RULES;
    const targets = (this._targets || [])
      .map((x) => `<option value="${esc(x.entity_id)}">${esc(this._name(x.entity_id))}</option>`)
      .join("");
    el.innerHTML = `
      ${this._error ? `<div class="msg error">${esc(this._error)}</div>` : ""}
      ${this._info ? `<div class="msg ok">${esc(this._info)}</div>` : ""}
      ${full ? `<div class="msg">${esc(t.max_rules)}</div>` : ""}
      <div class="actions">
        <div class="presets">
          <button class="text" id="add" ${dis || (full ? "disabled" : "")}><ha-icon icon="mdi:plus"></ha-icon>${esc(t.add)}</button>
        </div>
        ${this._dirty ? `<span class="dirty">${esc(t.unsaved)}</span>` : ""}
        <button class="text" id="discard" ${dis || (this._dirty ? "" : "disabled")}>${esc(t.discard)}</button>
        <button class="primary" id="save" ${dis || (this._dirty ? "" : "disabled")}>${esc(t.save)}</button>
      </div>
      ${
        targets
          ? `<div class="actions copy"><span>${esc(t.copy_to)}</span>
              <select id="copy-target" ${dis}>${targets}</select>
              <button class="text" id="copy" ${dis}><ha-icon icon="mdi:content-copy"></ha-icon>${esc(t.copy)}</button></div>`
          : ""
      }`;
    const on = (id, fn) => {
      const b = this.shadowRoot.getElementById(id);
      if (b) b.addEventListener("click", fn);
    };
    on("add", () => {
      const last = this._rules[this._rules.length - 1];
      this._rules.push({
        _key: ++keySeq,
        enable: true,
        advanced: false,
        time: "06:00",
        days: new Set(last && !last.advanced ? last.days : DAYS),
        mode: "temp",
        target_C: 21,
        pos: 0,
      });
      this._changed();
      this._renderRules();
    });
    on("discard", () => {
      this._load(false);
    });
    on("save", () => this._save());
    on("copy", () => this._copy());
  }
}

const STYLE = `
  ha-card { overflow: hidden; }
  .header { display: flex; align-items: center; padding: 12px 16px 0; }
  .title { flex: 1; font-size: 1.25em; font-weight: 500; }
  .content { padding: 8px 16px 16px; }
  button { font: inherit; cursor: pointer; }
  button:disabled { cursor: default; opacity: .5; }
  button.icon { background: none; border: none; color: var(--secondary-text-color); padding: 4px; border-radius: 50%; }
  button.icon:hover:not(:disabled) { background: var(--secondary-background-color); }
  button.text { background: none; border: none; color: var(--primary-color); padding: 6px 8px; border-radius: 6px; display: inline-flex; align-items: center; gap: 4px; }
  button.text:hover:not(:disabled) { background: rgba(var(--rgb-primary-color, 3,169,244), .1); }
  button.primary { background: var(--primary-color); color: var(--text-primary-color, #fff); border: none; padding: 6px 16px; border-radius: 6px; }
  ha-icon { --mdc-icon-size: 20px; }
  .msg { padding: 8px 0; color: var(--secondary-text-color); }
  .msg.error { color: var(--error-color, #db4437); }
  .msg.warn { color: var(--warning-color, #ffa600); }
  .msg.ok { color: var(--success-color, #43a047); }
  .msg.busy { display: flex; align-items: center; gap: 8px; }
  .spinner { width: 14px; height: 14px; border: 2px solid var(--divider-color); border-top-color: var(--primary-color); border-radius: 50%; animation: spin 1s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .week { margin: 4px 0 12px; }
  .day { display: flex; align-items: center; gap: 8px; margin: 2px 0; }
  .day > span { width: 22px; font-size: .85em; color: var(--secondary-text-color); }
  .day.today > span { color: var(--primary-color); font-weight: 600; }
  .bar { position: relative; flex: 1; height: 22px; border-radius: 4px; overflow: hidden; background: var(--secondary-background-color); }
  .seg { position: absolute; top: 0; bottom: 0; display: flex; align-items: center; justify-content: center; font-size: .75em; color: #000c; white-space: nowrap; overflow: hidden; box-shadow: inset -1px 0 0 #fff8; }
  .now { position: absolute; top: -2px; bottom: -2px; width: 2px; background: var(--primary-text-color); }
  .ticks { position: relative; flex: 1; height: 14px; font-size: .7em; color: var(--secondary-text-color); }
  .ticks span { position: absolute; transform: translateX(-50%); }
  .ticks span:first-child { transform: none; }
  .ticks span:last-child { transform: translateX(-100%); }
  .rule { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; padding: 6px 0; border-top: 1px solid var(--divider-color); }
  .rule.inactive > :not(.del):not([data-f="enable"]) { opacity: .45; }
  input, select { font: inherit; color: var(--primary-text-color); background: var(--card-background-color, #fff); border: 1px solid var(--divider-color); border-radius: 6px; padding: 4px 6px; }
  input[type="checkbox"] { width: 18px; height: 18px; accent-color: var(--primary-color); }
  input.time { width: 6.5em; }
  input.num { width: 4.5em; }
  input.timespec { flex: 1; min-width: 12em; font-family: monospace; }
  .chips { display: flex; gap: 2px; }
  .chip { min-width: 28px; padding: 3px 4px; border-radius: 12px; border: 1px solid var(--divider-color); background: none; color: var(--secondary-text-color); font-size: .8em; }
  .chip.on { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
  .value { display: flex; gap: 4px; align-items: center; }
  .del { margin-left: auto; }
  .actions { display: flex; align-items: center; justify-content: flex-end; gap: 8px; margin-top: 8px; flex-wrap: wrap; }
  .presets { margin-right: auto; }
  .actions.copy { justify-content: flex-start; color: var(--secondary-text-color); font-size: .9em; }
  .dirty { color: var(--warning-color, #ffa600); font-size: .85em; }
`;

if (!customElements.get("shelly-trv-schedule-card")) {
  customElements.define("shelly-trv-schedule-card", ShellyTrvScheduleCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "shelly-trv-schedule-card",
    name: "Shelly BLU TRV Schedule",
    description: "Edit the schedule stored on a Shelly BLU TRV",
    preview: false,
    documentationURL: "https://github.com/Flautz/ha-shelly-blu-trv-control",
  });
}
