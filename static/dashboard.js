"use strict";

// Mirrors appinfo.APP_COLORS: per-app hues sampled from the Instagram spectrum
const APP_COLORS = ["#D62976", "#FA7E1E", "#4F5BD5", "#962FBF", "#F2B01E", "#E8505B"];
const DAY_MS = 86400000;
const EVENTS_PAGE = 15;
const VIDEOS_PAGE = 5;

const $ = (sel, root = document) => root.querySelector(sel);
const reportEl = $("#report");
const sideEl = $("#side");
const tooltip = $("#tooltip");

const state = {
    view: "day",
    anchor: startOfDay(new Date()),
    sessions: [],
    apps: {},
    ranking: [],
    events: [],
    eventsKey: null,
    loading: true,
    error: null,
    sessionId: null,
    sessionEvents: new Map(),
    showAllApps: false,
    showAllEvents: false,
    showAllVideos: false,
    youtube: [],
    seen: loadSeen(),
    chart: null,
};

// Text and time
function esc(value) {
    return String(value ?? "").replace(
        /[&<>"']/g,
        (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
    );
}

function startOfDay(d) {
    const x = new Date(d);
    x.setHours(0, 0, 0, 0);
    return x;
}

function addDays(d, n) {
    const x = new Date(d);
    x.setDate(x.getDate() + n);
    return x;
}

function firstDayOfWeek() {
    try {
        const locale = new Intl.Locale(navigator.language);
        const info = locale.getWeekInfo ? locale.getWeekInfo() : locale.weekInfo;
        if (info && info.firstDay) return info.firstDay % 7;
    } catch (_) {
        /* fall through */
    }
    return /^en-US/i.test(navigator.language) ? 0 : 1;
}

const FIRST_DAY = firstDayOfWeek();

function startOfWeek(d) {
    const x = startOfDay(d);
    return addDays(x, -((x.getDay() - FIRST_DAY + 7) % 7));
}

function parseTime(iso) {
    if (!iso) return NaN;
    // Legacy rows without an offset were written in UTC
    return Date.parse(/(z|[+-]\d\d:\d\d)$/i.test(iso) ? iso : `${iso}Z`);
}

// Mirrors app.fmt_duration
function fmtDuration(seconds) {
    const total = Math.round(seconds || 0);
    if (total < 60) return `${total}s`;
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    if (h) return m ? `${h}h ${m}m` : `${h}h`;
    if (m < 10 && s) return `${m}m ${s}s`;
    return `${m}m`;
}

function fmtAxis(seconds) {
    if (seconds === 0) return "0";
    return seconds < 3600 ? `${Math.round(seconds / 60)}m` : `${+(seconds / 3600).toFixed(1)}h`;
}

const fmt = {
    time: new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }),
    hour: new Intl.DateTimeFormat(undefined, { hour: "numeric" }),
    long: new Intl.DateTimeFormat(undefined, { weekday: "long", month: "long", day: "numeric" }),
    short: new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric" }),
    monthDay: new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }),
    weekday: new Intl.DateTimeFormat(undefined, { weekday: "short" }),
    weekdayNarrow: new Intl.DateTimeFormat(undefined, { weekday: "narrow" }),
    weekdayLong: new Intl.DateTimeFormat(undefined, { weekday: "long" }),
};

function timeRange(start, end) {
    try {
        return fmt.time.formatRange(start, end);
    } catch (_) {
        return `${fmt.time.format(start)} – ${fmt.time.format(end)}`;
    }
}

function plural(n, word, many) {
    return `${n} ${n === 1 ? word : many || `${word}s`}`;
}

// Apps: names, icons, colors
function fallbackName(process) {
    if (!process || process === "Unknown") return "Unidentified";
    const stem = process.replace(/\.exe$/i, "");
    return stem.charAt(0).toUpperCase() + stem.slice(1);
}

function appName(process) {
    return (state.apps[process] && state.apps[process].name) || fallbackName(process);
}

function tile(process, cls = "tile") {
    const app = state.apps[process];
    const letter = esc(appName(process).charAt(0));
    if (app && app.icon) {
        return `<span class="${cls}" data-letter="${letter}"><img src="${esc(app.icon)}" alt="" decoding="async"></span>`;
    }
    return `<span class="${cls}" aria-hidden="true">${letter}</span>`;
}

// A missing icon file falls back to the app's initial
document.addEventListener(
    "error",
    (e) => {
        const img = e.target;
        if (img.tagName === "IMG" && img.parentElement && img.parentElement.dataset.letter) {
            img.parentElement.textContent = img.parentElement.dataset.letter;
        }
    },
    true,
);

// Mirrors appinfo.color_map: all-time top apps own a hue; others borrow free ones
function colorMap(viewRanked) {
    const owned = new Map();
    state.ranking.slice(0, APP_COLORS.length).forEach((p, i) => owned.set(p, APP_COLORS[i]));
    const colors = new Map();
    for (const p of viewRanked) if (owned.has(p)) colors.set(p, owned.get(p));
    const used = new Set(colors.values());
    const free = APP_COLORS.filter((c) => !used.has(c));
    for (const p of viewRanked) if (!colors.has(p) && free.length) colors.set(p, free.shift());
    return colors;
}

const colorOf = (colors, process) => colors.get(process) || "var(--other)";

// Seen sessions (Instagram semantics: a gradient ring until you open it)
function loadSeen() {
    try {
        return new Set(JSON.parse(localStorage.getItem("odinskin:seen") || "[]"));
    } catch (_) {
        return new Set();
    }
}

function markSeen(id) {
    if (state.seen.has(id)) return;
    state.seen.add(id);
    try {
        localStorage.setItem("odinskin:seen", JSON.stringify([...state.seen]));
    } catch (_) {
        /* private mode: the ring just stays */
    }
}

// Aggregation
function sumMap(map) {
    let total = 0;
    for (const v of map.values()) total += v;
    return total;
}

function mergeMaps(maps) {
    const out = new Map();
    for (const m of maps) for (const [k, v] of m) out.set(k, (out.get(k) || 0) + v);
    return out;
}

function ranked(map) {
    return [...map.entries()].sort((a, b) => b[1] - a[1]);
}

/** Split events across bucket boundaries [b0, b1, ..., bn] into per-app seconds. */
function aggregate(events, bounds) {
    const buckets = bounds.slice(0, -1).map(() => new Map());
    const lo = bounds[0];
    const hi = bounds[bounds.length - 1];
    for (const ev of events) {
        let s = Math.max(ev.s, lo);
        const e = Math.min(ev.e, hi);
        if (!(e > s)) continue;
        let i = 0;
        while (i < buckets.length - 1 && bounds[i + 1] <= s) i++;
        while (s < e && i < buckets.length) {
            const end = Math.min(e, bounds[i + 1]);
            buckets[i].set(ev.process_name, (buckets[i].get(ev.process_name) || 0) + (end - s) / 1000);
            s = end;
            i++;
        }
    }
    return buckets;
}

function hourBounds(day) {
    const out = [];
    for (let h = 0; h <= 24; h++) out.push(new Date(day.getFullYear(), day.getMonth(), day.getDate(), h).getTime());
    return out;
}

function sessionStart(s) {
    return parseTime(s.start_time);
}

function sessionsBetween(from, to) {
    return state.sessions.filter((s) => {
        const t = sessionStart(s);
        return t >= from && t < to;
    });
}

// Data
async function getJSON(url) {
    const res = await fetch(url, { headers: { Accept: "application/json" } });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
}

function computeRanking() {
    const totals = new Map();
    for (const s of state.sessions) {
        for (const [p, secs] of Object.entries(s.summary || {})) totals.set(p, (totals.get(p) || 0) + secs);
    }
    state.ranking = ranked(totals).map(([p]) => p);
}

function eventsWindow() {
    const weekStart = startOfWeek(state.anchor);
    return [addDays(weekStart, -7), addDays(weekStart, 7)];
}

async function ensureEvents(force = false) {
    const [from, to] = eventsWindow();
    const key = `${from.getTime()}:${to.getTime()}`;
    if (!force && state.eventsKey === key) return;
    const rows = await getJSON(
        `/api/events?from=${encodeURIComponent(from.toISOString())}&to=${encodeURIComponent(to.toISOString())}`,
    );
    state.events = rows.map((r) => ({ ...r, s: parseTime(r.start_time), e: parseTime(r.end_time) }));
    const range = `from=${encodeURIComponent(from.toISOString())}&to=${encodeURIComponent(to.toISOString())}`;
    state.youtube = (await getJSON(`/api/youtube?${range}`)).map((v) => ({
        ...v,
        s: parseTime(v.first_watched),
        e: parseTime(v.last_watched),
    }));
    state.eventsKey = key;
}

async function load(force = false) {
    try {
        const [sessions, apps] = await Promise.all([getJSON("/api/sessions"), getJSON("/api/apps")]);
        state.sessions = sessions;
        state.apps = apps;
        computeRanking();
        await ensureEvents(force);
        state.error = null;
    } catch (err) {
        state.error = err;
    }
    state.loading = false;
    render();
}

// Report
function periodInfo() {
    const today = startOfDay(new Date());
    if (state.view === "day") {
        const day = state.anchor;
        const title =
            +day === +today ? "Today" : +day === +addDays(today, -1) ? "Yesterday" : fmt.short.format(day);
        return { title, from: day, to: addDays(day, 1), canNext: +addDays(day, 1) <= +today, unit: "day" };
    }
    const start = startOfWeek(state.anchor);
    const thisWeek = startOfWeek(today);
    const title =
        +start === +thisWeek
            ? "This week"
            : +start === +addDays(thisWeek, -7)
              ? "Last week"
              : `${fmt.monthDay.format(start)} – ${fmt.monthDay.format(addDays(start, 6))}`;
    return { title, from: start, to: addDays(start, 7), canNext: +addDays(start, 7) <= +today, unit: "week" };
}

function renderTitle() {
    const info = periodInfo();
    $("#range-title").textContent = info.title;
    document.title = `${info.title} · Odin's Kin`;
    $("#prev").setAttribute("aria-label", `Previous ${info.unit}`);
    $("#next").setAttribute("aria-label", `Next ${info.unit}`);
    $("#next").disabled = !info.canNext;
    const seg = $(".segmented");
    seg.dataset.view = state.view;
    seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.view === state.view)));
}

const arrowUp = `<svg viewBox="0 0 14 14" aria-hidden="true"><path d="M7 11.5v-9M3 6.5l4-4 4 4"/></svg>`;
const arrowDown = `<svg viewBox="0 0 14 14" aria-hidden="true"><path d="M7 2.5v9M3 7.5l4 4 4-4"/></svg>`;
const chevron = `<svg class="chevron" viewBox="0 0 14 14" aria-hidden="true"><path d="M5 2.5 9.5 7 5 11.5"/></svg>`;

function comparison(value, baseline, noun) {
    if (!baseline) return "";
    const pct = Math.round(((value - baseline) / baseline) * 100);
    if (Math.abs(pct) < 3) return `About the same as ${noun}`;
    return `${pct > 0 ? arrowUp : arrowDown}<span>${Math.abs(pct)}% ${pct > 0 ? "more" : "less"} than ${noun}</span>`;
}

function renderReport() {
    const today = startOfDay(new Date());
    const weekStart = startOfWeek(state.anchor);
    const weekBounds = Array.from({ length: 8 }, (_, i) => addDays(weekStart, i).getTime());
    const days = aggregate(state.events, weekBounds);
    const dayTotals = days.map(sumMap);
    const weekTotal = dayTotals.reduce((a, b) => a + b, 0);

    let headline, context, chart, rangeMap, stats;
    const info = periodInfo();
    const rangeSessions = sessionsBetween(+info.from, +info.to);
    const switches = rangeSessions.reduce((n, s) => n + Math.max(0, (s.total_events || 0) - 1), 0);

    if (state.view === "day") {
        const hours = aggregate(state.events, hourBounds(state.anchor));
        rangeMap = mergeMaps(hours);
        const total = sumMap(rangeMap);
        const prior = aggregate(state.events, [+addDays(state.anchor, -7), +state.anchor])[0];
        const priorAvg = sumMap(prior) / 7;
        headline = total ? fmtDuration(total) : "0m";
        if (!total) {
            context = +state.anchor === +today ? "Nothing tracked yet today" : "Nothing tracked on this day";
        } else {
            context = comparison(total, priorAvg, "your 7-day average") || "Your first tracked day this week";
        }
        const longest = state.events.reduce((m, ev) => {
            const s = Math.max(ev.s, +info.from);
            const e = Math.min(ev.e, +info.to);
            return e > s ? Math.max(m, (e - s) / 1000) : m;
        }, 0);
        stats = [
            ["Sessions", rangeSessions.length],
            ["Switches", switches],
            ["Longest focus", longest ? fmtDuration(longest) : "—"],
        ];
        chart = {
            kind: "day",
            buckets: hours,
            labels: hours.map((_, h) => (h % 6 === 0 ? fmt.hour.format(new Date(2000, 0, 1, h)) : "")),
            names: hours.map((_, h) => timeRange(new Date(2000, 0, 1, h), new Date(2000, 0, 1, h + 1))),
        };
    } else {
        rangeMap = mergeMaps(days);
        const elapsed =
            +addDays(weekStart, 7) <= +today ? 7 : Math.max(1, Math.round((today - weekStart) / DAY_MS) + 1);
        const avg = weekTotal / elapsed;
        const prev = sumMap(aggregate(state.events, [+addDays(weekStart, -7), +weekStart])[0]) / 7;
        headline = avg ? fmtDuration(avg) : "0m";
        const delta = comparison(avg, prev, "last week");
        context = delta ? `<span>Daily average</span><span aria-hidden="true">·</span>${delta}` : "Daily average";
        stats = [
            ["Total", weekTotal ? fmtDuration(weekTotal) : "0m"],
            ["Sessions", rangeSessions.length],
            ["Switches", switches],
        ];
        chart = {
            kind: "week",
            buckets: days,
            avg,
            dates: weekBounds.slice(0, 7).map((t) => new Date(t)),
            todayIdx: weekBounds.findIndex((t) => t === +today),
            names: weekBounds.slice(0, 7).map((t) => fmt.long.format(new Date(t))),
        };
    }

    const viewRanked = ranked(rangeMap);
    const colors = colorMap(viewRanked.map(([p]) => p));
    chart.colors = colors;
    chart.order = viewRanked.map(([p]) => p);
    state.chart = chart;

    const legendApps = viewRanked.filter(([p]) => colors.has(p)).slice(0, 5);
    const hasOther = viewRanked.some(([p]) => !legendApps.find(([q]) => q === p));
    const legend = legendApps.length
        ? `<div class="legend">${legendApps
              .map(([p]) => `<span><i style="background:${colorOf(colors, p)}"></i>${esc(appName(p))}</span>`)
              .join("")}${hasOther ? `<span><i style="background:var(--other)"></i>Other</span>` : ""}</div>`
        : `<div class="legend"></div>`;

    const top = viewRanked.length ? viewRanked[0][1] : 0;
    const visible = state.showAllApps ? viewRanked : viewRanked.slice(0, 5);

    const tray = state.view === "day" ? weekTray(weekBounds, days, today) : "";
    const videos = state.youtube
        .filter((v) => v.s < +info.to && v.e > +info.from)
        .sort((a, b) => b.s - a.s);

    reportEl.innerHTML = `
        ${tray}
        <div class="group">
            <div class="summary">
                <p class="headline">${esc(headline)}</p>
                <p class="context">${context}</p>
                <div class="chart" id="chart"></div>
                ${legend}
            </div>
            ${facts(stats)}
        </div>
        <h2 class="section-title">Most used
            ${viewRanked.length > 5 ? `<button type="button" class="link-button" data-action="toggle-apps">${state.showAllApps ? "Show less" : "Show all"}</button>` : ""}
        </h2>
        <div class="group">
            ${
                visible.length
                    ? visible.map(([p, secs]) => appRow(p, secs, top, colors)).join("")
                    : `<div class="empty"><p>Apps you use during a session will rank here.</p></div>`
            }
        </div>
        ${youtubeSection(videos, state.view === "day")}`;
    drawChart();
}

function facts(rows) {
    return `<dl class="facts">${rows
        .map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`)
        .join("")}</dl>`;
}

/** Seven story rings: each day's app split, filled in proportion to the week's busiest day. */
function weekTray(weekBounds, days, today) {
    const totals = days.map(sumMap);
    const max = Math.max(...totals, 1);
    const weekRanked = ranked(mergeMaps(days)).map(([p]) => p);
    const colors = colorMap(weekRanked);
    const size = 44;
    const sw = 4;
    const r = (size - sw) / 2;
    const C = 2 * Math.PI * r;
    const items = days.map((bucket, i) => {
        const date = new Date(weekBounds[i]);
        const future = date > today;
        const selected = +date === +state.anchor;
        let arcs = "";
        let offset = 0;
        const reach = (totals[i] / max) * C;
        for (const [p, v] of ranked(bucket)) {
            const len = (v / totals[i]) * reach;
            if (len < 0.5) continue;
            arcs += `<circle cx="${size / 2}" cy="${size / 2}" r="${r}" stroke-width="${sw}" style="stroke:${colorOf(colors, p)}"
                stroke-dasharray="${len.toFixed(2)} ${C}" stroke-dashoffset="${(-offset).toFixed(2)}"/>`;
            offset += len;
        }
        const label = `${fmt.long.format(date)}: ${totals[i] ? fmtDuration(totals[i]) : "nothing tracked"}`;
        return `
            <button type="button" class="tray-day${selected ? " selected" : ""}${+date === +today ? " today" : ""}"
                data-day="${weekBounds[i]}" ${future ? "disabled" : ""} aria-pressed="${selected}" aria-label="${esc(label)}">
                <span class="tray-ring">
                    ${
                        totals[i]
                            ? `<svg viewBox="0 0 ${size} ${size}" aria-hidden="true">
                        <circle class="tray-track" cx="${size / 2}" cy="${size / 2}" r="${r}" stroke-width="${sw}"/>${arcs}
                    </svg>`
                            : ""
                    }
                    <span class="tray-date num">${date.getDate()}</span>
                </span>
                <span class="tray-weekday">${esc(fmt.weekdayNarrow.format(date))}</span>
            </button>`;
    });
    return `<div class="week-tray" role="group" aria-label="Days this week">${items.join("")}</div>`;
}

const playGlyph = `<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M7.2 5.6v8.8c0 .6.6.9 1.1.6l6.9-4.4c.5-.3.5-.9 0-1.2L8.3 5c-.5-.3-1.1 0-1.1.6Z"/></svg>`;
const outGlyph = `<svg class="chevron" viewBox="0 0 14 14" aria-hidden="true"><path d="M5 3h6v6M11 3 3.5 10.5"/></svg>`;

const videoName = (title) => (title || "").replace(/\s+-\s+YouTube(?: Music)?$/, "");

function videoHref(v) {
    return v.url || `https://www.youtube.com/results?search_query=${encodeURIComponent(v.title)}`;
}

function videoRow(v, dayView) {
    const when = dayView ? fmt.time.format(v.s) : `${fmt.weekday.format(v.s)} ${fmt.time.format(v.s)}`;
    const where = v.url ? (v.music ? "YouTube Music" : "YouTube") : "Search on YouTube";
    return `
        <a class="row video" href="${esc(videoHref(v))}" target="_blank" rel="noopener noreferrer"
            aria-label="${esc(`${v.title}, watched ${fmtDuration(v.seconds)}, opens ${where}`)}">
            <span class="tile play">${playGlyph}</span>
            <span class="row-main">
                <span class="row-title">${esc(v.title)}</span>
                <span class="row-sub num">${esc(`${when} · ${where}`)}</span>
            </span>
            <span class="row-end">${esc(fmtDuration(v.seconds))}${outGlyph}</span>
        </a>`;
}

function youtubeSection(videos, dayView) {
    const shown = state.showAllVideos ? videos : videos.slice(0, VIDEOS_PAGE);
    const toggle =
        videos.length > VIDEOS_PAGE
            ? `<button type="button" class="link-button" data-action="toggle-videos">${state.showAllVideos ? "Show less" : `Show all ${videos.length}`}</button>`
            : "";
    return `
        <h2 class="section-title">YouTube${toggle}</h2>
        <div class="group">${
            shown.length
                ? shown.map((v) => videoRow(v, dayView)).join("")
                : `<div class="empty"><p>Videos you watch in Chrome, Edge, Brave or Firefox during a session show up here, with their links.</p></div>`
        }</div>`;
}

function appRow(process, seconds, top, colors) {
    const width = top ? Math.max(1.5, (seconds / top) * 100) : 0;
    return `
        <div class="row">
            ${tile(process)}
            <div class="row-main">
                <div class="row-title">${esc(appName(process))}</div>
                <span class="usage-bar" style="width:${width.toFixed(2)}%;background:${colorOf(colors, process)}"></span>
            </div>
            <div class="row-end num">${esc(fmtDuration(seconds))}</div>
        </div>`;
}

// Chart (SVG, sized to its container)
function niceStep(max) {
    const steps = [60, 300, 600, 900, 1800, 3600, 7200, 10800, 14400, 21600, 28800, 43200];
    return steps.find((s) => Math.ceil(max / s) <= 3) || 43200;
}

function drawChart() {
    const el = $("#chart");
    const spec = state.chart;
    if (!el || !spec) return;
    const W = el.clientWidth;
    const H = el.clientHeight;
    if (!W) return;

    const padR = 36;
    const padB = 24;
    const plotW = W - padR;
    const plotH = H - padB - 6;
    const top = 6;
    const n = spec.buckets.length;
    const colW = plotW / n;
    const barW = spec.kind === "week" ? Math.min(34, colW * 0.56) : Math.max(3, Math.min(13, colW * 0.62));
    const totals = spec.buckets.map(sumMap);
    const max = Math.max(...totals, spec.avg || 0, spec.kind === "day" ? 900 : 3600);
    const step = niceStep(max);
    const yMax = step * Math.max(1, Math.ceil(max / step));
    const y = (v) => top + plotH - (v / yMax) * plotH;
    const narrow = colW < 44;

    let svg = `<svg width="${W}" height="${H}" role="img" aria-label="${esc(
        spec.kind === "week" ? "Focus time per day this week" : "Focus time per hour",
    )}"><defs>`;
    const cols = [];

    spec.buckets.forEach((bucket, i) => {
        const x = i * colW + (colW - barW) / 2;
        const total = totals[i];
        const r = Math.min(4, barW / 2);
        const barTop = y(total);
        const h = top + plotH - barTop;
        svg += `<clipPath id="c${i}"><path d="M${x},${top + plotH} V${barTop + r} a${r},${r} 0 0 1 ${r},${-r} H${x + barW - r} a${r},${r} 0 0 1 ${r},${r} V${top + plotH} Z"/></clipPath>`;

        let segs = "";
        if (total > 0) {
            let acc = 0;
            const parts = [];
            let other = 0;
            for (const p of spec.order) {
                const v = bucket.get(p);
                if (!v) continue;
                if (spec.colors.has(p)) parts.push([spec.colors.get(p), v]);
                else other += v;
            }
            if (other) parts.push(["var(--other)", other]);
            const minH = Math.max(2, h);
            for (const [color, v] of parts) {
                const segH = (v / total) * minH;
                const sy = top + plotH - acc - segH;
                segs += `<rect x="${x}" y="${sy}" width="${barW}" height="${segH + 0.5}" style="fill:${color}"/>`;
                acc += segH;
            }
        }
        cols.push({ i, x, segs, total });
    });
    svg += `</defs>`;

    // Grid; a gridline hugging the avg line would read as a glitch, so it yields
    const avgY = spec.avg ? y(spec.avg) : null;
    for (let v = step; v <= yMax; v += step) {
        if (avgY !== null && Math.abs(y(v) - avgY) < 12) continue;
        svg += `<line class="gridline" x1="0" x2="${plotW}" y1="${y(v)}" y2="${y(v)}"/>`;
    }
    svg += `<line class="baseline" x1="0" x2="${plotW}" y1="${top + plotH}" y2="${top + plotH}"/>`;
    for (let v = 0; v <= yMax; v += step) {
        if (avgY !== null && Math.abs(y(v) - avgY) < 12) continue;
        svg += `<text class="tick" x="${plotW + 8}" y="${y(v) + 4}">${fmtAxis(v)}</text>`;
    }

    // Columns
    for (const c of cols) {
        const interactive = spec.kind === "week";
        const label = `${spec.names[c.i]}: ${fmtDuration(c.total)}`;
        svg += `<g class="col" data-i="${c.i}">
            <rect class="hit-bg" x="${c.i * colW + 2}" y="${top - 4}" width="${colW - 4}" height="${plotH + 8}" rx="8"/>
            <g clip-path="url(#c${c.i})">${c.segs}</g>
            <rect class="hit" x="${c.i * colW}" y="0" width="${colW}" height="${top + plotH + padB}"
                ${interactive ? `tabindex="0" role="button" aria-label="${esc(label)}, show this day"` : ""}/>
        </g>`;
        let xl = "";
        if (spec.kind === "week") {
            const d = spec.dates[c.i];
            xl = (narrow ? fmt.weekdayNarrow : fmt.weekday).format(d);
            svg += `<text class="tick${c.i === spec.todayIdx ? " today" : ""}" x="${c.i * colW + colW / 2}" y="${H - 6}" text-anchor="middle">${esc(xl)}</text>`;
        } else if (spec.labels[c.i]) {
            svg += `<text class="tick" x="${c.i * colW + 1}" y="${H - 6}">${esc(spec.labels[c.i])}</text>`;
        }
    }

    if (avgY !== null) {
        svg += `<line class="avg-line" x1="0" x2="${plotW}" y1="${avgY}" y2="${avgY}"/>`;
        svg += `<text class="avg-label" x="${plotW + 8}" y="${avgY + 4}">avg</text>`;
    }
    svg += `</svg>`;
    el.innerHTML = svg;
}

function chartTip(i) {
    const spec = state.chart;
    const bucket = spec.buckets[i];
    const total = sumMap(bucket);
    const rows = ranked(bucket)
        .slice(0, 4)
        .map(
            ([p, v]) =>
                `<span class="tip-row"><i style="background:${colorOf(spec.colors, p)}"></i>${esc(appName(p))}<b>${esc(fmtDuration(v))}</b></span>`,
        )
        .join("");
    return `<strong>${esc(spec.names[i])}</strong>${total ? rows : "Nothing tracked"}`;
}

// Tooltip
function showTip(html, x, y) {
    tooltip.innerHTML = html;
    tooltip.hidden = false;
    const { width, height } = tooltip.getBoundingClientRect();
    const left = Math.min(Math.max(8, x - width / 2), innerWidth - width - 8);
    const topY = y - height - 12 < 8 ? y + 18 : y - height - 12;
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${topY}px`;
}

function hideTip() {
    tooltip.hidden = true;
}

reportEl.addEventListener("pointermove", (e) => {
    const col = e.target.closest && e.target.closest(".col");
    if (!col) return hideTip();
    showTip(chartTip(+col.dataset.i), e.clientX, col.getBoundingClientRect().top + 4);
});
reportEl.addEventListener("pointerleave", hideTip);
reportEl.addEventListener("focusin", (e) => {
    const col = e.target.closest && e.target.closest(".col");
    if (!col) return;
    const r = col.getBoundingClientRect();
    showTip(chartTip(+col.dataset.i), r.left + r.width / 2, r.top + 4);
});
reportEl.addEventListener("focusout", hideTip);

function openDay(i) {
    if (!state.chart || state.chart.kind !== "week") return;
    hideTip();
    state.view = "day";
    state.anchor = state.chart.dates[i];
    render();
}

reportEl.addEventListener("click", (e) => {
    const col = e.target.closest(".col");
    if (col) return openDay(+col.dataset.i);
    if (e.target.closest('[data-action="toggle-apps"]')) {
        state.showAllApps = !state.showAllApps;
        renderReport();
    }
    if (e.target.closest('[data-action="toggle-videos"]')) {
        state.showAllVideos = !state.showAllVideos;
        renderReport();
    }
    const day = e.target.closest("[data-day]");
    if (day && !day.disabled) {
        state.anchor = new Date(+day.dataset.day);
        navigate();
    }
});
reportEl.addEventListener("keydown", (e) => {
    const col = e.target.closest && e.target.closest(".col");
    if (col && (e.key === "Enter" || e.key === " ")) {
        e.preventDefault();
        openDay(+col.dataset.i);
    }
});

new ResizeObserver(() => drawChart()).observe(reportEl);

// Sessions list
function sessionRow(s) {
    const start = new Date(sessionStart(s));
    const end = new Date(parseTime(s.end_time) || sessionStart(s));
    const apps = Object.entries(s.summary || {}).sort((a, b) => b[1] - a[1]);
    const lead = apps.length ? apps[0][0] : "Unknown";
    const stack = apps
        .slice(1, 4)
        .map(([p]) => tile(p, "tile mini"))
        .join("");
    const isNew = !state.seen.has(s.id);
    return `
        <button type="button" class="row" data-session="${esc(s.id)}"
            aria-label="${esc(`${isNew ? "New session" : "Session"}, ${timeRange(start, end)}, ${fmtDuration((end - start) / 1000)}, mostly ${appName(lead)}, ${plural(apps.length, "app")}`)}">
            <span class="ring${isNew ? " new" : ""}">${tile(lead)}</span>
            <span class="row-main">
                <span class="row-title num">${esc(timeRange(start, end))}</span>
                <span class="row-sub stacked"><span class="stack" aria-hidden="true">${stack}</span>${esc(
                    apps.length ? plural(apps.length, "app") : "No apps recorded",
                )}</span>
            </span>
            <span class="row-end">${esc(fmtDuration((end - start) / 1000))}${chevron}</span>
        </button>`;
}

function renderSessions() {
    const info = periodInfo();
    const list = sessionsBetween(+info.from, +info.to);
    let body;
    if (!state.sessions.length) {
        body = `<div class="group"><div class="empty">
            <span class="ring-art" aria-hidden="true"></span>
            <h3>No sessions yet</h3>
            <p>Open Odin's Kin on your PC, press Start session, and work as usual. When you stop, the session shows up here.</p>
        </div></div>`;
    } else if (!list.length) {
        body = `<div class="group"><div class="empty">
            <h3>No sessions ${state.view === "day" ? "on this day" : "this week"}</h3>
            <p>${
                state.view === "day"
                    ? "Pick another day with the arrows, or switch to Week to see more."
                    : "Step back a week with the arrows to find earlier sessions."
            }</p>
        </div></div>`;
    } else if (state.view === "day") {
        body = `<div class="group">${list.map(sessionRow).join("")}</div>`;
    } else {
        const byDay = new Map();
        for (const s of list) {
            const key = +startOfDay(new Date(sessionStart(s)));
            if (!byDay.has(key)) byDay.set(key, []);
            byDay.get(key).push(s);
        }
        body = [...byDay.entries()]
            .sort((a, b) => b[0] - a[0])
            .map(
                ([day, items]) =>
                    `<h3 class="day-label">${esc(fmt.long.format(new Date(day)))}</h3><div class="group">${items.map(sessionRow).join("")}</div>`,
            )
            .join("");
    }
    sideEl.innerHTML = `
        <h2 class="section-title">Sessions<span class="aside num">${list.length ? esc(plural(list.length, "session")) : ""}</span></h2>
        ${body}`;
}

sideEl.addEventListener("click", (e) => {
    const row = e.target.closest("[data-session]");
    if (row) {
        location.hash = `#session/${row.dataset.session}`;
        return;
    }
    const action = e.target.closest("[data-action]");
    if (!action) return;
    if (action.dataset.action === "back") {
        if (history.length > 1 && state.cameFromList) history.back();
        else location.hash = "";
    } else if (action.dataset.action === "all-events") {
        state.showAllEvents = true;
        renderDetail();
    } else if (action.dataset.action === "retry-detail") {
        state.sessionEvents.delete(state.sessionId);
        renderDetail();
    }
});

// Session detail
function donut(apps, colors, total) {
    const size = 188;
    const sw = 14;
    const r = (size - sw) / 2;
    const C = 2 * Math.PI * r;
    const gap = 5;
    let circles = `<circle class="donut-track" cx="${size / 2}" cy="${size / 2}" r="${r}" stroke-width="${sw}"/>`;
    const segs = [];
    let other = 0;
    for (const [p, v] of apps) {
        if (colors.has(p) && v / total >= 0.01) segs.push([colors.get(p), v / total]);
        else other += v / total;
    }
    if (other > 0.0001) segs.push(["var(--other)", other]);
    if (segs.length === 1) {
        circles += `<circle cx="${size / 2}" cy="${size / 2}" r="${r}" stroke-width="${sw}" style="stroke:${segs[0][0]}"/>`;
    } else {
        let offset = 0;
        for (const [color, frac] of segs) {
            const len = Math.max(0.01, frac * C - gap - sw);
            circles += `<circle cx="${size / 2}" cy="${size / 2}" r="${r}" stroke-width="${sw}"
                style="stroke:${color}" stroke-dasharray="${len} ${C}" stroke-dashoffset="${-(offset + (gap + sw) / 2)}"/>`;
            offset += frac * C;
        }
    }
    return `<div class="donut"><svg viewBox="0 0 ${size} ${size}" aria-hidden="true">${circles}</svg>
        <div class="donut-value"><strong>${esc(fmtDuration(total))}</strong><span>focus time</span></div></div>`;
}

function sessionVideos(events) {
    const byKey = new Map();
    for (const ev of events) {
        if (!ev.window_title) continue;
        const title = videoName(ev.window_title);
        const key = ev.url || `title:${title.toLowerCase()}`;
        const v = byKey.get(key) || {
            title,
            url: ev.url,
            music: / - YouTube Music$/.test(ev.window_title),
            seconds: 0,
            s: ev.s,
            e: ev.e,
        };
        v.seconds += ev.duration_seconds || 0;
        v.e = Math.max(v.e, ev.e);
        byKey.set(key, v);
    }
    if (!byKey.size) return "";
    const inOrder = [...byKey.values()].sort((a, b) => a.s - b.s);
    return `<h2 class="section-title">YouTube</h2>
        <div class="group">${inOrder.map((v) => videoRow(v, true)).join("")}</div>`;
}

const backButton = `<button type="button" class="back" data-action="back">
    <svg viewBox="0 0 22 22" aria-hidden="true"><path d="M13.5 4.5 7 11l6.5 6.5"/></svg>Sessions</button>`;

async function renderDetail() {
    const id = state.sessionId;
    const s = state.sessions.find((x) => x.id === id);
    if (!s) {
        sideEl.innerHTML = `<div class="detail">${backButton}<div class="group"><div class="empty">
            <h3>Session not found</h3><p>It may have been removed from the database.</p></div></div></div>`;
        return;
    }
    markSeen(id);

    let events = state.sessionEvents.get(id);
    if (!events) {
        sideEl.innerHTML = `<div class="detail">${backButton}<div class="group"><div class="detail-head">
            <div class="skeleton" style="width:188px;height:188px;border-radius:50%"></div>
            <div class="skeleton" style="width:60%;height:20px"></div></div></div></div>`;
        try {
            const rows = await getJSON(`/api/sessions/${id}/events`);
            events = rows.map((r) => ({ ...r, s: parseTime(r.start_time), e: parseTime(r.end_time) }));
            state.sessionEvents.set(id, events);
        } catch (err) {
            if (state.sessionId !== id) return;
            sideEl.innerHTML = `<div class="detail">${backButton}<div class="group"><div class="empty">
                <h3>Couldn't load this session</h3><p>${esc(err.message)}</p>
                <button type="button" class="link-button" data-action="retry-detail">Try again</button></div></div></div>`;
            return;
        }
        if (state.sessionId !== id) return;
    }

    const start = sessionStart(s);
    const end = parseTime(s.end_time) || (events.length ? events[events.length - 1].e : start);
    const span = Math.max(1, end - start);
    const totals = new Map();
    for (const ev of events) totals.set(ev.process_name, (totals.get(ev.process_name) || 0) + (ev.duration_seconds || 0));
    const apps = ranked(totals);
    const total = sumMap(totals);
    const colors = colorMap(apps.map(([p]) => p));
    const longest = events.reduce((m, ev) => Math.max(m, ev.duration_seconds || 0), 0);

    const blocks = events
        .map((ev, i) => {
            const left = ((ev.s - start) / span) * 100;
            const width = (((ev.e || ev.s) - ev.s) / span) * 100;
            const label = `${appName(ev.process_name)}, ${fmt.time.format(ev.s)}, ${fmtDuration(ev.duration_seconds)}`;
            return `<button type="button" data-ev="${i}" style="left:${left.toFixed(3)}%;width:${width.toFixed(3)}%;background:${colorOf(colors, ev.process_name)}" aria-label="${esc(label)}"></button>`;
        })
        .join("");

    const shownEvents = state.showAllEvents ? events : events.slice(0, EVENTS_PAGE);
    const eventRows = shownEvents
        .map(
            (ev) => `
        <div class="row event">
            <time class="num" datetime="${esc(ev.start_time)}">${esc(fmt.time.format(ev.s))}</time>
            ${tile(ev.process_name)}
            <div class="row-main">
                <div class="row-title">${esc(appName(ev.process_name))}</div>
                ${ev.window_title ? `<div class="row-sub">${esc(videoName(ev.window_title))}</div>` : ""}
            </div>
            <div class="row-end num">${esc(fmtDuration(ev.duration_seconds))}</div>
        </div>`,
        )
        .join("");

    const top = apps.length ? apps[0][1] : 0;
    sideEl.innerHTML = `
        <div class="detail">
            ${backButton}
            <div class="group">
                <div class="detail-head">
                    ${donut(apps, colors, total)}
                    <div>
                        <h2>${esc(fmt.long.format(new Date(start)))}</h2>
                        <p class="num">${esc(timeRange(new Date(start), new Date(end)))}</p>
                    </div>
                </div>
                <div class="timeline-wrap">
                    <div class="timeline" id="timeline">${blocks}</div>
                    <div class="timeline-ticks"><span>${esc(fmt.time.format(start))}</span><span>${esc(
                        fmt.time.format(start + span / 2),
                    )}</span><span>${esc(fmt.time.format(end))}</span></div>
                </div>
                ${facts([
                    ["Switches", Math.max(0, events.length - 1)],
                    ["Apps", apps.length],
                    ["Longest focus", longest ? fmtDuration(longest) : "—"],
                ])}
            </div>
            <h2 class="section-title">Apps</h2>
            <div class="group">${apps.map(([p, v]) => appRow(p, v, top, colors)).join("") || `<div class="empty"><p>No apps recorded.</p></div>`}</div>
            ${sessionVideos(events)}
            <h2 class="section-title">Focus events<span class="aside num">${esc(events.length)}</span></h2>
            <div class="group">
                ${eventRows || `<div class="empty"><p>No focus events recorded.</p></div>`}
                ${
                    events.length > shownEvents.length
                        ? `<button type="button" class="more" data-action="all-events">Show all ${esc(events.length)} events</button>`
                        : ""
                }
            </div>
        </div>`;

    const timeline = $("#timeline");
    const tipFor = (btn) => {
        const ev = events[+btn.dataset.ev];
        return `<strong>${esc(appName(ev.process_name))}</strong>${ev.window_title ? `${esc(videoName(ev.window_title))}<br>` : ""}<span class="num">${esc(
            timeRange(new Date(ev.s), new Date(ev.e || ev.s)),
        )} · ${esc(fmtDuration(ev.duration_seconds))}</span>`;
    };
    timeline.addEventListener("pointermove", (e) => {
        const btn = e.target.closest("button");
        if (!btn) return hideTip();
        showTip(tipFor(btn), e.clientX, timeline.getBoundingClientRect().top);
    });
    timeline.addEventListener("pointerleave", hideTip);
    timeline.addEventListener("focusin", (e) => {
        const r = e.target.getBoundingClientRect();
        showTip(tipFor(e.target), r.left + r.width / 2, r.top);
    });
    timeline.addEventListener("focusout", hideTip);
}

// Skeleton, error
function renderSkeleton() {
    reportEl.innerHTML = `<div class="group"><div class="summary" style="padding-bottom:20px">
        <div class="skeleton" style="width:200px;height:56px"></div>
        <div class="skeleton" style="width:240px;height:16px;margin-top:12px"></div>
        <div class="skeleton" style="height:180px;margin-top:24px"></div></div></div>`;
    sideEl.innerHTML = `<h2 class="section-title">Sessions</h2><div class="group">${Array.from(
        { length: 4 },
        () => `<div class="row"><span class="skeleton" style="width:48px;height:48px;border-radius:50%"></span>
            <span><span class="skeleton" style="display:block;width:60%;height:14px"></span>
            <span class="skeleton" style="display:block;width:40%;height:12px;margin-top:8px"></span></span><span></span></div>`,
    ).join("")}</div>`;
}

function renderError() {
    reportEl.innerHTML = `<div class="group"><div class="empty">
        <span class="ring-art" aria-hidden="true"></span>
        <h3>Couldn't load your sessions</h3>
        <p>${esc(state.error.message)}. If you started the dashboard from the app, keep the app open and try again.</p>
        <button type="button" class="link-button" id="retry">Try again</button>
    </div></div>`;
    sideEl.innerHTML = "";
    $("#retry").addEventListener("click", () => {
        state.loading = true;
        render();
        load(true);
    });
}

// Render + navigation
function isoDate(d) {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** ?view=week&date=2026-09-24 keeps the report across reloads and bookmarks. */
function readQuery() {
    const params = new URLSearchParams(location.search);
    if (params.get("view") === "week") state.view = "week";
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(params.get("date") || "");
    if (m) {
        const d = new Date(+m[1], +m[2] - 1, +m[3]);
        if (!Number.isNaN(+d) && d <= new Date()) state.anchor = startOfDay(d);
    }
}

function writeQuery() {
    const params = new URLSearchParams();
    if (state.view === "week") params.set("view", "week");
    if (+state.anchor !== +startOfDay(new Date())) params.set("date", isoDate(state.anchor));
    const query = params.toString();
    const url = `${location.pathname}${query ? `?${query}` : ""}${location.hash}`;
    if (url !== `${location.pathname}${location.search}${location.hash}`) history.replaceState(null, "", url);
}

function render() {
    renderTitle();
    writeQuery();
    document.body.classList.toggle("in-detail", state.sessionId !== null);
    if (state.loading) return renderSkeleton();
    if (state.error) return renderError();
    renderReport();
    if (state.sessionId !== null) renderDetail();
    else renderSessions();
}

async function navigate() {
    hideTip();
    render();
    if (state.loading || state.error) return;
    const key = state.eventsKey;
    try {
        await ensureEvents();
    } catch (err) {
        state.error = err;
    }
    if (state.eventsKey !== key || state.error) render();
}

function step(dir) {
    const info = periodInfo();
    if (dir > 0 && !info.canNext) return;
    state.anchor = addDays(state.anchor, dir * (state.view === "day" ? 1 : 7));
    if (state.anchor > new Date()) state.anchor = startOfDay(new Date());
    state.showAllApps = false;
    navigate();
}

$("#prev").addEventListener("click", () => step(-1));
$("#next").addEventListener("click", () => step(1));

$(".segmented").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-view]");
    if (!btn || btn.dataset.view === state.view) return;
    state.view = btn.dataset.view;
    state.showAllApps = false;
    navigate();
});

document.addEventListener("keydown", (e) => {
    if (e.target.closest("input, textarea, select") || e.altKey || e.ctrlKey || e.metaKey) return;
    if (e.key === "ArrowLeft" && !e.target.closest(".col")) step(-1);
    else if (e.key === "ArrowRight" && !e.target.closest(".col")) step(1);
    else if (e.key === "Escape" && state.sessionId !== null) location.hash = "";
});

function readRoute() {
    const match = /^#session\/(\d+)$/.exec(location.hash);
    const next = match ? Number(match[1]) : null;
    state.cameFromList = next !== null && state.sessionId === null;
    if (next !== state.sessionId) state.showAllEvents = false;
    state.sessionId = next;
}

window.addEventListener("hashchange", () => {
    readRoute();
    render();
    if (state.sessionId !== null && matchMedia("(max-width: 960px)").matches) scrollTo({ top: 0 });
});

// Pick up sessions saved while the dashboard sat in another tab
document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && !state.loading) load(true);
});

readQuery();
readRoute();
render();
load();
