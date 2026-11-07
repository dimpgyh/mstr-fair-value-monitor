"use strict";
const $ = (id) => document.getElementById(id);
const money = (v, digits = 2) =>
  Number.isFinite(v)
    ? new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
        maximumFractionDigits: digits,
        minimumFractionDigits: digits,
      }).format(v)
    : "—";
const number = (v, digits = 0) =>
  Number.isFinite(v)
    ? new Intl.NumberFormat("en-US", { maximumFractionDigits: digits }).format(
        v,
      )
    : "—";
const billion = (v) => `${v < 0 ? "−" : ""}$${number(Math.abs(v) / 1e9, 2)}B`;
const pct = (v) =>
  Number.isFinite(v) ? `${v >= 0 ? "+" : ""}${v.toFixed(1)}%` : "—";
const esc = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const dateTime = (s) =>
  s
    ? new Date(s).toLocaleString("id-ID", {
        timeZone: "Asia/Jakarta",
        day: "2-digit",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      }) + " WIB"
    : "—";
const safeUrl = (s) => {
  try {
    const u = new URL(s);
    return ["https:", "http:"].includes(u.protocol) ? u.href : "#";
  } catch {
    return "#";
  }
};
const labels = {
  btc_holdings: "Kepemilikan BTC",
  basic_shares: "Saham basic (A + B)",
  total_debt: "Utang / klaim pokok",
  preferred_stock: "Preferred / klaim likuidasi",
  cash_and_equivalents: "Aset USD (kas + reserve)",
  annual_carry_cost: "Bunga + dividen / tahun",
};
let current = null,
  working = null,
  busy = false,
  dialogReturn = null;
function message(text, error = false) {
  $("notice").textContent = text;
  $("notice").classList.toggle("error", error);
}
async function request(url, options) {
  const res = await fetch(url, options);
  const body = await res.json();
  if (!res.ok) {
    let detail = body.detail;
    if (Array.isArray(detail))
      detail = detail
        .map((x) => `${x.loc.slice(1).join(".")}: ${x.msg}`)
        .join("\n");
    throw new Error(detail || "Permintaan gagal");
  }
  return body;
}
function quoteTime(q) {
  return `${q.source} · ${dateTime(q.observed_at)}${q.stale ? " · KEDALUWARSA" : ""}`;
}
function render(data) {
  current = data;
  disconnected = false;
  const v = data.valuation;
  $("empty").hidden = !!v;
  $("dashboard").hidden = !v;
  if (!v) {
    message(
      data.scheduler.last_error || "Mengambil observasi pasar pertama…",
      !!data.scheduler.last_error,
    );
    renderSourcesFromConfig(data.config);
    tick();
    return;
  }
  const c = v.config,
    b = v.backing,
    m = v.market;
  const edited = JSON.stringify(data.config) !== JSON.stringify(c);
  const stale = m.mstr.stale || m.btc.stale;
  const unverified = v.evidence.filter(
    (x) => x.status === "unverified" || x.stale,
  ).length;
  message(
    edited
      ? "Parameter tersimpan, tetapi hasil masih memakai konfigurasi observasi sebelumnya. Refresh untuk menghitung ulang."
      : data.scheduler.last_error
        ? "Refresh terakhir gagal. Menampilkan observasi tersimpan: " +
          data.scheduler.last_error
        : stale
          ? "Ada harga kedaluwarsa. Periksa waktu observasi sebelum membaca skenario."
          : unverified
            ? `${unverified} input perlu diperiksa sumber atau tanggalnya. Hasil berikut bersifat indikatif.`
            : "Acuan indikatif · jumlah saham masih berupa estimasi; nilai software dan premi adalah asumsi.",
    !!data.scheduler.last_error || stale || edited,
  );
  $("nav").textContent = money(b.net_asset_per_share);
  $("mstr-price").textContent = money(m.mstr.price);
  $("nav-gap").textContent =
    b.net_asset_per_share > 0
      ? `${pct(Math.abs(b.discount_pct))} ${b.discount_pct >= 0 ? "diskon" : "premi"} terhadap acuan`
      : "Aset bersih model negatif";
  $("value-marker").style.left =
    `${Math.max(2, Math.min(98, (m.mstr.price / Math.max(m.mstr.price, b.common_equity_reference, 1)) * 90))}%`;
  $("net-total").textContent = billion(b.net_assets_usd);
  const bridge = [
    ["Treasury Bitcoin", b.treasury_usd],
    ["Kas + reserve USD", c.cash_and_equivalents],
    ["Bisnis software", c.software_ev],
    ["Utang", -c.total_debt],
    ["Preferred stock", -c.preferred_stock],
    ["Kewajiban bersih lain", -c.other_net_liabilities],
  ];
  $("bridge").innerHTML = bridge
    .map(
      ([name, value]) =>
        `<div class="bridge-row"><span>${name}</span><div class="bridge-track"><i class="${value < 0 ? "negative" : ""}" style="width:${Math.max(2, Math.min(100, (Math.abs(value) / b.treasury_usd) * 100))}%"></i></div><span>${billion(value)}</span></div>`,
    )
    .join("");
  $("btc-price").textContent = money(m.btc.price, 0);
  $("btc-time").textContent = quoteTime(m.btc);
  $("holdings").textContent = number(c.btc_holdings);
  $("holdings-note").textContent =
    `Sumber: ${c.evidence.btc_holdings?.as_of || "belum dicatat"}`;
  $("multiple").textContent = number(v.relative.custom_asset_multiple, 2) + "×";
  $("converted-nav").textContent = money(b.converted_net_asset_per_share);
  const names = { bear: "Pesimistis", base: "Dasar", bull: "Optimistis" };
  $("scenario-cards").innerHTML = v.scenarios
    .map(
      (s, i) =>
        `<article class="card scenario ${s.name}"><div class="scenario-top"><span class="scenario-name">${names[s.name]}</span><span class="scenario-index">0${i + 1} / ${s.name.toUpperCase()}</span></div><div class="scenario-value">${money(s.present_value)}</div><p class="scenario-diff">${pct(s.upside_pct)} vs harga pasar</p><dl><div><dt>BTC akhir periode</dt><dd>${money(s.btc_price, 0)}</dd></div><div><dt>Multiple aset BTC</dt><dd>${number(s.asset_multiple, 2)}×</dd></div><div><dt>Harga terminal</dt><dd>${money(s.terminal_price)}</dd></div><div><dt>PV aset bersih</dt><dd>${money(s.asset_backing_pv)}</dd></div><div><dt>PV premi / diskon</dt><dd>${money(s.premium_component_pv)}</dd></div><div><dt>Tingkat diskonto</dt><dd>${number(c.discount_rate_pct, 1)}%</dd></div></dl></article>`,
    )
    .join("");
  $("funding").innerHTML = v.scenarios
    .map(
      (s) =>
        `<div class="funding-row"><span>${names[s.name]}</span><strong class="${s.funding_delta_nav_per_share >= 0 ? "positive" : "negative"}">${s.funding_delta_nav_per_share >= 0 ? "+" : ""}${money(s.funding_delta_nav_per_share)} / saham</strong></div>`,
    )
    .join("");
  const base = v.scenarios.find((s) => s.name === "base");
  $("funding-note").textContent =
    `Ekuitas ${billion(c.new_equity_usd)} · utang ${billion(c.new_debt_usd)} · preferred ${billion(c.new_preferred_usd)}. Biaya tahunan model ${billion(base.annual_carry_usd)}.${base.funding_gap_usd ? " Kekurangan kas " + billion(base.funding_gap_usd) + "." : ""} Tanpa pendanaan baru, dampak tambahan = nol.`;
  $("sensitivity-table").innerHTML =
    `<thead><tr><th>BTC / multiple →</th>${v.sensitivity.multiples.map((x) => `<th>${number(x, 2)}×</th>`).join("")}</tr></thead><tbody>${v.sensitivity.rows.map((r) => `<tr><th>${money(r.btc_price, 0)}<br>${pct(r.change_pct)}</th>${r.cells.map((x) => `<td class="${x < m.mstr.price ? "down" : ""}">${money(x, 0)}</td>`).join("")}</tr>`).join("")}</tbody>`;
  renderSources(v.evidence, m);
  $("warnings").innerHTML = v.warnings
    .map((w) => `<li>${esc(w)}</li>`)
    .join("");
  renderHistory(data.hourly_logs, data.legacy_snapshot_count);
  tick();
}
function renderSourcesFromConfig(c) {
  const rows = Object.keys(labels).map((field) => ({
    field,
    value: c[field],
    ...(c.evidence[field] || { status: "unverified" }),
  }));
  renderSources(rows);
}
function renderSources(rows, market) {
  const status = {
    reported: "Dilaporkan",
    estimate: "Estimasi",
    unverified: "Belum diverifikasi",
  };
  $("source-rows").innerHTML = rows
    .map(
      (r) =>
        `<tr><td>${labels[r.field] || esc(r.field)}</td><td>${r.field === "btc_holdings" || r.field === "basic_shares" ? number(r.value) : billion(r.value)}</td><td><span class="evidence-tag ${r.status !== "reported" || r.stale ? "warning" : ""}">${status[r.status] || "Belum diverifikasi"}${r.stale ? " · cek tanggal" : ""}</span></td><td>${esc(r.as_of || "—")}</td><td>${r.source_url ? `<a class="text-link" href="${esc(safeUrl(r.source_url))}" target="_blank" rel="noopener">Buka sumber ↗</a>` : "Belum ada sumber"}<span class="source-note">${esc(r.note)}</span></td></tr>`,
    )
    .join("");
  if (market)
    for (const [key, name] of [
      ["mstr", "Harga MSTR"],
      ["btc", "Harga BTC"],
    ]) {
      const q = market[key];
      $("source-rows").insertAdjacentHTML(
        "beforeend",
        `<tr><td>${name}</td><td>${money(q.price)}</td><td><span class="evidence-tag ${q.stale ? "warning" : ""}">${q.stale ? "Kedaluwarsa" : "Observasi pasar"}</span></td><td>${dateTime(q.observed_at)}</td><td><a class="text-link" href="${esc(safeUrl(q.source_url))}" target="_blank" rel="noopener">${esc(q.source)} ↗</a><span class="source-note">${esc(q.kind)} · Diambil ${dateTime(q.retrieved_at)}</span></td></tr>`,
      );
    }
}
function renderHistory(rows, legacy) {
  // Never mix different configurations or V1 reconstructions into a comparison line.
  const logs = rows.filter((x) => x.model_version === "2.0");
  const unique = [...new Map(logs.map((x) => [x.timestamp_utc, x])).values()];
  const fingerprint = (x) => JSON.stringify(x.config);
  const configSet = new Set(unique.map(fingerprint));
  const shown =
    configSet.size > 1
      ? unique.filter((x) => fingerprint(x) === fingerprint(unique.at(-1)))
      : unique;
  if (shown.length < 2) {
    $("history-chart").innerHTML =
      "<p>Grafik muncul setelah dua observasi dengan parameter yang sama tersimpan.</p>";
  } else {
    const values = shown.flatMap((x) => [
      x.market.mstr.price,
      x.backing.net_asset_per_share,
    ]);
    const lo = Math.min(...values),
      hi = Math.max(...values),
      pad = Math.max((hi - lo) * 0.2, 1);
    const min = lo - pad,
      max = hi + pad,
      times = shown.map((x) => new Date(x.timestamp_utc).getTime()),
      t0 = times[0],
      dt = Math.max(1, times.at(-1) - t0);
    const x = (i) => 40 + ((times[i] - t0) / dt) * 460,
      y = (v) => 135 - ((v - min) / (max - min)) * 115;
    const path = (fn) =>
      shown
        .map(
          (p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(fn(p)).toFixed(1)}`,
        )
        .join(" ");
    $("history-chart").innerHTML =
      `<svg viewBox="0 0 520 160" role="img" aria-label="Harga MSTR dan nilai aset bersih dari observasi tersimpan"><path d="M40 20H500M40 77H500M40 135H500" stroke="#e5eade" fill="none"/><text x="0" y="25" font-size="9" fill="#70807b">${money(max, 0)}</text><text x="0" y="138" font-size="9" fill="#70807b">${money(min, 0)}</text><path d="${path((p) => p.market.mstr.price)}" stroke="#8c9d92" stroke-width="2" fill="none"/><path d="${path((p) => p.backing.net_asset_per_share)}" stroke="#456e52" stroke-width="2.5" fill="none"/></svg>`;
  }
  $("history-caption").textContent =
    `${shown.length} observasi sebanding · ${shown.length ? dateTime(shown[0].timestamp_utc) + " — " + dateTime(shown.at(-1).timestamp_utc) : "menunggu data"}. ${configSet.size > 1 ? "Grafik difilter ke parameter terbaru. " : ""}${legacy ? `${legacy} snapshot model lama tetap diarsipkan di database. ` : ""}Tidak ada rekonstruksi masa lalu.`;
}
let disconnected = false;
function tick() {
  if (!current) return;
  const s = current.scheduler;
  const next = s.next_refresh_utc ? new Date(s.next_refresh_utc) : null;
  const left = next ? Math.max(0, Math.ceil((next - Date.now()) / 1000)) : 0;
  const latest = current.valuation;
  const age = latest
    ? (Date.now() - new Date(latest.timestamp_utc)) / 3600000
    : Infinity;
  const overdue = age > Math.max(2, (s.refresh_interval_seconds / 3600) * 2);
  $("schedule").textContent = s.is_refreshing
    ? "Sedang mengambil data…"
    : s.last_error || overdue || disconnected
      ? "Perlu perhatian"
      : next
        ? `Refresh berikutnya ${Math.floor(left / 60)} mnt ${left % 60} dtk`
        : "Menunggu jadwal";
  $("status-dot").style.background =
    s.last_error || overdue || disconnected ? "#bc7c40" : "#528363";
  $("last-refresh").textContent =
    `Observasi terakhir ${dateTime(s.last_refresh_utc)} · setiap ${number(s.refresh_interval_seconds / 3600, 2)} jam`;
  if (overdue && latest && !s.last_error)
    message(
      "Observasi tersimpan sudah melewati dua interval. Memeriksa pembaruan server…",
      true,
    );
}
const groups = [
  [
    "Aset & struktur modal",
    [
      ["btc_holdings", "Kepemilikan BTC", 1, "BTC"],
      ["basic_shares", "Saham basic (A + B)", 1, "lembar"],
      ["total_debt", "Utang / klaim pokok", 1e9, "US$ miliar"],
      ["preferred_stock", "Preferred / klaim likuidasi", 1e9, "US$ miliar"],
      ["cash_and_equivalents", "Kas + USD reserve", 1e9, "US$ miliar"],
      [
        "annual_carry_cost",
        "Bunga + dividen yang ada / tahun",
        1e9,
        "US$ miliar",
      ],
      ["software_ev", "Nilai bisnis software", 1e9, "US$ miliar · asumsi"],
      [
        "other_net_liabilities",
        "Kewajiban bersih lain",
        1e9,
        "US$ miliar · boleh negatif untuk aset bersih lain",
      ],
    ],
  ],
  [
    "Konversi yang diasumsikan terjadi",
    [
      ["converted_debt", "Utang yang dikonversi", 1e9, "US$ miliar"],
      ["converted_preferred", "Preferred yang dikonversi", 1e9, "US$ miliar"],
      [
        "conversion_new_shares",
        "Tambahan saham dari konversi",
        1e6,
        "juta lembar",
      ],
      [
        "conversion_carry_relief",
        "Pengurangan biaya tahunan dari konversi",
        1e9,
        "US$ miliar",
      ],
    ],
  ],
  [
    "Pendanaan baru · hasil bersih membeli BTC",
    [
      ["new_equity_usd", "Penerbitan saham baru", 1e9, "US$ miliar"],
      ["equity_issue_price", "Harga penerbitan saham", 1, "US$ / saham"],
      ["new_debt_usd", "Utang baru", 1e9, "US$ miliar"],
      ["debt_rate_pct", "Bunga utang baru", 1, "% / tahun"],
      ["new_preferred_usd", "Preferred baru", 1e9, "US$ miliar"],
      ["preferred_rate_pct", "Dividen preferred baru", 1, "% / tahun"],
      ["funding_fee_pct", "Biaya penerbitan", 1, "% dari seluruh pendanaan"],
      ["discount_rate_pct", "Tingkat diskonto", 1, "% / tahun"],
    ],
  ],
  [
    "Skenario 12 bulan & jadwal",
    [
      ["scenarios.bear.btc_change_pct", "Pesimistis · perubahan BTC", 1, "%"],
      ["scenarios.bear.asset_multiple", "Pesimistis · multiple aset", 1, "×"],
      ["scenarios.base.btc_change_pct", "Dasar · perubahan BTC", 1, "%"],
      ["scenarios.base.asset_multiple", "Dasar · multiple aset", 1, "×"],
      ["scenarios.bull.btc_change_pct", "Optimistis · perubahan BTC", 1, "%"],
      ["scenarios.bull.asset_multiple", "Optimistis · multiple aset", 1, "×"],
      [
        "refresh_interval_seconds",
        "Interval refresh",
        3600,
        "jam · minimum 1 menit",
      ],
    ],
  ],
];
const readPath = (obj, path) => path.split(".").reduce((o, k) => o[k], obj);
function setPath(obj, path, value) {
  const keys = path.split("."),
    last = keys.pop();
  keys.reduce((o, k) => o[k], obj)[last] = value;
}
function buildForm(c) {
  working = structuredClone(c);
  $("form-error").textContent = "";
  $("fields").innerHTML =
    groups
      .map(
        ([name, fields]) =>
          `<fieldset class="field-group"><legend>${name}</legend><div class="field-grid">${fields.map(([key, label, scale, unit]) => `<label>${label}<input type="number" step="any" required name="${key}" data-scale="${scale}" value="${readPath(c, key) / scale}"><span class="field-help">${unit}</span></label>`).join("")}</div></fieldset>`,
      )
      .join("") +
    `<fieldset class="field-group"><legend>Sumber input faktual</legend><p class="muted">Status “Dilaporkan” berarti angka yang kamu catat dari sumber, bukan verifikasi otomatis oleh aplikasi. Tanggal di atas 14 hari akan ditandai untuk diperiksa.</p>${Object.entries(
      labels,
    )
      .map(([key, label]) => {
        const e = c.evidence[key] || {};
        return `<details class="evidence-editor"><summary>${label}</summary><div class="field-grid"><label>Status<select name="evidence.${key}.status"><option value="unverified" ${e.status === "unverified" ? "selected" : ""}>Belum diverifikasi</option><option value="reported" ${e.status === "reported" ? "selected" : ""}>Dilaporkan</option><option value="estimate" ${e.status === "estimate" ? "selected" : ""}>Estimasi</option></select></label><label>Tanggal sumber<input type="date" name="evidence.${key}.as_of" value="${esc(e.as_of || "")}"></label><label class="wide">URL sumber<input type="url" name="evidence.${key}.source_url" value="${esc(e.source_url || "")}"></label><label class="wide">Catatan<input type="text" maxlength="1500" name="evidence.${key}.note" value="${esc(e.note || "")}"></label></div></details>`;
      })
      .join("")}</fieldset>`;
  for (const key of Object.keys(labels))
    $("settings-form").elements[key].addEventListener("input", () => {
      for (const field of ["status", "as_of", "source_url", "note"]) {
        $("settings-form").elements[`evidence.${key}.${field}`].value =
          field === "status" ? "unverified" : "";
      }
    });
}
$("settings-button").addEventListener("click", () => {
  if (!current) return;
  dialogReturn = document.activeElement;
  buildForm(current.config);
  $("settings-dialog").showModal();
});
$("close-settings").addEventListener("click", () =>
  $("settings-dialog").close(),
);
$("settings-dialog").addEventListener("close", () => dialogReturn?.focus());
$("reviewed-button").addEventListener("click", async () => {
  try {
    buildForm(await request("/api/config/reviewed"));
  } catch (e) {
    $("form-error").textContent = e.message;
  }
});
$("settings-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (busy) return;
  const c = structuredClone(working);
  c.evidence = {};
  for (const el of $("settings-form").elements) {
    if (!el.name) continue;
    const parts = el.name.split(".");
    if (parts[0] === "evidence") {
      const [, key, field] = parts;
      c.evidence[key] ??= {};
      c.evidence[key][field] = field === "as_of" ? el.value || null : el.value;
    } else setPath(c, el.name, Number(el.value) * Number(el.dataset.scale));
  }
  busy = true;
  $("save-button").disabled = true;
  $("form-error").textContent = "";
  try {
    render(
      await request("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(c),
      }),
    );
    $("settings-dialog").close();
  } catch (err) {
    $("form-error").textContent = err.message;
  } finally {
    busy = false;
    $("save-button").disabled = false;
  }
});
$("refresh-button").addEventListener("click", async () => {
  if (busy) return;
  busy = true;
  $("refresh-button").disabled = true;
  $("refresh-button").textContent = "Memperbarui…";
  try {
    render(await request("/api/refresh", { method: "POST" }));
  } catch (e) {
    message(e.message, true);
  } finally {
    busy = false;
    $("refresh-button").disabled = false;
    $("refresh-button").textContent = "↻  Refresh data";
  }
});
async function poll() {
  if (busy || $("settings-dialog").open) return;
  try {
    render(await request("/api/valuation"));
  } catch (e) {
    disconnected = true;
    message(
      "Koneksi server terputus. Data di layar adalah observasi terakhir. " +
        e.message,
      true,
    );
  }
}
poll();
setInterval(poll, 30000);
setInterval(tick, 1000);
