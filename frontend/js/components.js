// --- 1. Sidebar Renderer (Sin Spot-Perp) ---
function renderSidebar() {
    const container = document.getElementById("sidebar-container");
    if (!container) return;

    container.innerHTML = `
    <div class="p-6">
        <div class="flex items-center gap-2 mb-1 cursor-pointer" onclick="window.location.href='../index.html'">
            <div class="w-7 h-7 bg-gradient-to-br from-brandTeal to-brandPurple rounded-lg flex items-center justify-center">
                <span class="font-bold text-white text-sm">F</span>
            </div>
            <span class="text-lg font-bold text-white">TheFundingHouse</span>
        </div>
        <span class="text-xs text-slate-500 pl-9">Funding Rate Explorer</span>
    </div>

    <nav class="flex-1 px-4 py-4 space-y-2">
        <a href="#" class="flex items-center gap-3 px-4 py-3 bg-[#1e1b4b] text-brandTeal rounded-xl font-medium border border-teal-500/20 transition-colors">
            <i data-lucide="bar-chart-2" class="w-5 h-5"></i>
            Funding Explorer
        </a>
        <!-- Se ha eliminado Spot-Perp Arbs como pediste -->
    </nav>

    <div class="p-4 border-t border-white/5 mt-auto">
        <button class="w-full bg-[#1e1b4b] hover:bg-[#2d2a6e] text-indigo-100 py-3 rounded-xl font-medium flex items-center justify-center gap-2 border border-indigo-500/30 transition-colors">
            <i data-lucide="wallet" class="w-5 h-5"></i>
            Connect Wallet
        </button>
    </div>
    `;
}

// --- 2. Lógica de Interfaz (Dropdown, Temporalidades, Filtros) ---
let currentTimeframe = "live";
let allRows = [];
let filteredRows = [];
let logoMap = {};
let filtersState = {
    aprMin: null,
    aprMax: null,
    priceMin: null,
    priceMax: null,
    oiMin: null,
    oiMax: null,
    volMin: null,
    volMax: null,
};
let searchTerm = "";

function initUIInteractions() {
    // A. Dropdown de Plataformas
    const btn = document.getElementById("platforms-btn");
    const dropdown = document.getElementById("platforms-dropdown");

    if (btn && dropdown) {
        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            dropdown.classList.toggle("hidden");
        });

        // Cerrar si clic fuera
        document.addEventListener("click", (e) => {
            if (!btn.contains(e.target) && !dropdown.contains(e.target)) {
                dropdown.classList.add("hidden");
            }
        });
    }

    // B. Popover de filtros
    const filtersBtn = document.getElementById("filters-btn");
    const filtersPopover = document.getElementById("filters-popover");
    const applyBtn = document.getElementById("apply-filters-btn");
    const resetBtn = document.getElementById("reset-filters-btn");

    const positionFiltersPopover = () => {
        if (!filtersPopover || !filtersBtn) return;
        filtersPopover.style.left = "0";
        filtersPopover.style.right = "auto";
        const rect = filtersPopover.getBoundingClientRect();
        const margin = 8;
        if (rect.right > window.innerWidth - margin) {
            filtersPopover.style.left = "auto";
            filtersPopover.style.right = "0";
        }
    };

    const toggleFiltersPopover = (forceState) => {
        if (!filtersPopover) return;
        const shouldShow =
            typeof forceState === "boolean"
                ? forceState
                : filtersPopover.classList.contains("hidden");
        if (shouldShow) {
            filtersPopover.classList.remove("hidden");
            positionFiltersPopover();
        } else {
            filtersPopover.classList.add("hidden");
        }
    };

    if (filtersBtn && filtersPopover) {
        filtersBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            toggleFiltersPopover();
        });

        document.addEventListener("click", (e) => {
            if (
                filtersPopover.classList.contains("hidden") ||
                filtersBtn.contains(e.target) ||
                filtersPopover.contains(e.target)
            ) {
                return;
            }
            toggleFiltersPopover(false);
        });
    }

    if (applyBtn) {
        applyBtn.addEventListener("click", (e) => {
            e.preventDefault();
            applyFilters();
            filtersPopover && filtersPopover.classList.add("hidden");
        });
    }
    if (resetBtn) {
        resetBtn.addEventListener("click", (e) => {
            e.preventDefault();
            resetFilters();
        });
    }

    // C. Filtros de Tiempo
    const timeButtons = document.querySelectorAll(".time-btn");
    timeButtons.forEach((button) => {
        button.addEventListener("click", () => {
            // Remover clase active de todos
            timeButtons.forEach((b) => b.classList.remove("active"));
            // Añadir al clickeado
            button.classList.add("active");

            const tf = button.dataset.val || "live";
            currentTimeframe = tf;
            const tableBody =
                document.querySelector("[data-funding-table-body]") ||
                document.getElementById("funding-table-body");
            if (tableBody) {
                tableBody.innerHTML = "";
            }

            loadFunding(tf);
        });
    });

    // D. Search filter
    const searchInput = document.querySelector("[data-funding-search]");
    if (searchInput) {
        searchInput.addEventListener("input", (e) => {
            searchTerm = (e.target.value || "").toLowerCase().trim();
            recomputeAndRender();
        });
    }
}

// --- 3. Datos y Tablas ---
function initDashboard() {
    loadFunding("live");
}

async function loadFunding(timeframe) {
    const tableBody =
        document.querySelector("[data-funding-table-body]") ||
        document.getElementById("funding-table-body");
    if (!tableBody) return;

    tableBody.innerHTML =
        '<tr><td colspan="8" class="text-center py-10 text-slate-400">Loading...</td></tr>';

    try {
        const params = new URLSearchParams({
            min_spread_apr_percent: "0",
            limit: "100",
        });
        const endpoint = (() => {
            switch (timeframe) {
                case "1h":
                    return "/api/funding/1h";
                case "8h":
                    return "/api/funding/8h";
                case "24h":
                    return "/api/funding/24h";
                case "3d":
                    return "/api/funding/3d";
                case "7d":
                    return "/api/funding/7d";
                case "15d":
                    return "/api/funding/15d";
                case "31d":
                    return "/api/funding/31d";
                case "live":
                default:
                    return "/api/funding/live";
            }
        })();

        const res = await fetch(`${endpoint}?${params.toString()}`);
        if (!res.ok) {
            console.error("Failed to fetch funding data", res.status);
            tableBody.innerHTML =
                '<tr><td colspan="8" class="text-center py-4 text-red-400">Not implemented</td></tr>';
            allRows = [];
            filteredRows = [];
            return;
        }
        const data = await res.json();

        data.sort(
            (a, b) =>
                (Number(b.spread_apr_percent) || 0) -
                (Number(a.spread_apr_percent) || 0),
        );

        allRows = data;
        recomputeAndRender();
    } catch (err) {
        console.error("Error loading funding live:", err);
        tableBody.innerHTML =
            '<tr><td colspan="8" class="text-center py-4 text-red-400">Error loading data</td></tr>';
        allRows = [];
        filteredRows = [];
    }
}

function renderFundingRows(data) {
    const tableBody =
        document.querySelector("[data-funding-table-body]") ||
        document.getElementById("funding-table-body");
    if (!tableBody) return;

    // Mensaje global si todo viene de live fallback
    const allFallbackLive =
        Array.isArray(data) &&
        data.length > 0 &&
        data.every((p) => p.mode === "fallback_live" || p.data_source === "live_fallback");
    const globalNotice = document.getElementById("funding-global-notice");
    if (globalNotice) {
        if (allFallbackLive) {
            globalNotice.classList.remove("hidden");
        } else {
            globalNotice.classList.add("hidden");
        }
    }

    if (!data || data.length === 0) {
        tableBody.innerHTML =
            '<tr><td colspan="8" class="text-center py-10 text-slate-400">No data available</td></tr>';
        return;
    }

    const fmtPercent = (v) => (v == null ? "-" : `${Number(v).toFixed(1)}%`);
    const fmtNum = (v) => {
        const num = Number(v);
        if (!isFinite(num)) return "-";
        if (num >= 1_000_000_000) return (num / 1_000_000_000).toFixed(1) + "B";
        if (num >= 1_000_000) return (num / 1_000_000).toFixed(1) + "M";
        if (num >= 1_000) return (num / 1_000).toFixed(1) + "K";
        return num.toString();
    };

    tableBody.innerHTML = "";

    const rows = data.map((pair) => {
        const volL = Number(pair.long_market?.volume_24h);
        const volS = Number(pair.short_market?.volume_24h);
        const volTotal =
            (Number.isFinite(volL) ? volL : 0) +
            (Number.isFinite(volS) ? volS : 0);
        return {
            ...pair,
            __volL: volL,
            __volS: volS,
            __volTotal: volTotal,
        };
    });

    rows.forEach((pair, i) => {
        const long = pair.long_market || {};
        const short = pair.short_market || {};

        const longExchange = (long.exchange || "L").toString();
        const shortExchange = (short.exchange || "S").toString();

        const longOiVal = computeOiUsd(long);
        const shortOiVal = computeOiUsd(short);
        const longOi = longOiVal != null ? fmtNum(longOiVal) : "-";
        const shortOi = shortOiVal != null ? fmtNum(shortOiVal) : "-";
        const totalOiVal = (longOiVal || 0) + (shortOiVal || 0);
        const totalOi =
            longOiVal == null && shortOiVal == null ? "-" : fmtNum(totalOiVal);

        const priceSpreadPctVal = computePriceSpreadPct(pair);
        const priceSpreadPctText =
            priceSpreadPctVal == null
                ? "-"
                : `${priceSpreadPctVal.toFixed(2)}%`;

        const volLDisplay = Number.isFinite(pair.__volL) ? pair.__volL : null;
        const volSDisplay = Number.isFinite(pair.__volS) ? pair.__volS : null;
        const volLText =
            volLDisplay != null && volLDisplay > 0 ? fmtNum(volLDisplay) : "-";
        const volSText =
            volSDisplay != null && volSDisplay > 0 ? fmtNum(volSDisplay) : "-";
        const volTotal = (volLDisplay ?? 0) + (volSDisplay ?? 0);
        const totalVolText =
            volLDisplay == null && volSDisplay == null ? "-" : fmtNum(volTotal);

        const symbolKey = (
            pair.canonical_symbol ||
            pair.pair_id ||
            ""
        ).toString().toUpperCase();
        const logoUrl = symbolKey ? logoMap[symbolKey] : undefined;
        const tokenVisual = logoUrl
            ? `<img src="${logoUrl}" alt="${pair.canonical_symbol || ""}" class="w-[18px] h-[18px] rounded-full flex-none" style="margin-right:4px;" onerror="this.style.display='none';" />`
            : `<div class="w-[22px] h-[22px] rounded-full bg-slate-700 flex items-center justify-center text-[10px] flex-none">${(pair.canonical_symbol || "?")[0]}</div>`;

        const tr = document.createElement("tr");
        tr.className =
            "hover:bg-white/5 transition-colors group border-b border-white/5";
        const fallbackBadge =
            pair.mode === "fallback_live"
                ? `<span class="ml-2 text-[11px] text-amber-300 whitespace-nowrap">Calculando ventana ${currentTimeframe} (mostrando live temporalmente)</span>`
                : "";

        tr.innerHTML = `
            <td class="px-4 py-3 text-slate-400 first:rounded-l-lg">${i + 1}</td>
            <td class="px-4 py-3 text-white whitespace-nowrap">
                <div class="inline-flex items-center gap-2 whitespace-nowrap" style="line-height:1;">
                    ${tokenVisual}
                    <span class="font-semibold inline-block">${pair.canonical_symbol || pair.pair_id || "-"}${fallbackBadge}</span>
                </div>
            </td>
            <td class="px-4 py-3 text-brandTeal font-mono">${fmtPercent(pair.spread_apr_percent)}</td>
            <td class="px-4 py-3 text-slate-300 text-xs">
                ${longExchange.substring(0, 2).toUpperCase()} / ${shortExchange.substring(0, 2).toUpperCase()}
            </td>
            <td class="px-4 py-3 text-slate-400 font-mono hidden md:table-cell">${priceSpreadPctText}</td>
            <td class="px-4 py-3 text-slate-400 font-mono text-xs hidden md:table-cell">
                <div class="text-green-400/70">L: ${longOi}</div>
                <div class="text-red-400/70">S: ${shortOi}</div>
                <div class="text-slate-400/70 text-[12px]">T: ${totalOi}</div>
            </td>
            <td class="px-4 py-3 text-slate-500 font-mono text-xs hidden md:table-cell">
                <div class="flex items-center justify-between gap-2">
                    <div>
                        <div class="text-green-400/70">L: ${volLText}</div>
                        <div class="text-red-400/70">S: ${volSText}</div>
                        <div class="text-slate-400/70 text-[12px]">T: ${totalVolText}</div>
                    </div>
                </div>
            </td>
            <td class="px-4 py-3 text-right last:rounded-r-lg">
                <button class="text-brandTeal hover:bg-brandTeal/10 p-2 rounded transition-colors"><i data-lucide="external-link" class="w-4 h-4"></i></button>
            </td>
        `;
        tableBody.appendChild(tr);
    });

    if (window.lucide) window.lucide.createIcons();
}

async function fetchTokenLogos(symbols) {
    const uniq = Array.from(new Set(symbols.filter(Boolean)));
    if (!uniq.length) return {};
    const qs = encodeURIComponent(uniq.join(","));
    try {
        const res = await fetch(`/api/token/logos?symbols=${qs}`);
        if (!res.ok) return {};
        return await res.json();
    } catch (e) {
        console.error("Error fetching token logos", e);
        return {};
    }
}

async function loadLogosForRows(rows) {
    const slice = rows.slice(0, 100);
    const symbols = slice
        .map((r) =>
            (r.canonical_symbol || r.pair_id || "").toString().toUpperCase(),
        )
        .filter(Boolean);
    // avoid fetching if already cached
    const missing = symbols.filter((s) => !logoMap[s]);
    if (!missing.length) return;
    const map = await fetchTokenLogos(missing);
    const hasNew = Object.keys(map || {}).length > 0;
    logoMap = { ...logoMap, ...map };
    if (hasNew) {
        renderFundingRows(filteredRows);
    }
}

// --- 4. Helpers de filtrado ---
function parseFiltersFromInputs() {
    const inputs = document.querySelectorAll("[data-filter-input]");
    const next = { ...filtersState };
    inputs.forEach((inp) => {
        const key = inp.dataset.key;
        if (!key) return;
        const raw = inp.value;
        if (raw === "" || raw == null) {
            next[key] = null;
            return;
        }
        const num = Number(raw);
        next[key] = Number.isFinite(num) ? num : null;
    });
    return next;
}

function matchesBounds(val, min, max) {
    if (min == null && max == null) return true;
    if (!Number.isFinite(val)) return false;
    if (min != null && val < min) return false;
    if (max != null && val > max) return false;
    return true;
}

function computeOiUsd(mkt) {
    const oi = Number(mkt?.open_interest);
    const price = Number(mkt?.mark_price);
    const ex = (mkt?.exchange || "").toString().toLowerCase();
    if (!Number.isFinite(oi) || oi <= 0) return null;
    if (ex === "hyperliquid") {
        if (!Number.isFinite(price) || price <= 0) return null;
        return oi * price;
    }
    return oi;
}

function computePriceSpreadPct(pair) {
    const a = Number(pair?.long_market?.mark_price);
    const b = Number(pair?.short_market?.mark_price);
    if (!Number.isFinite(a) || !Number.isFinite(b) || a <= 0 || b <= 0) return null;
    const mid = (a + b) / 2;
    if (mid <= 0) return null;
    return ((b - a) / mid) * 100;
}

function computeTotalOi(pair) {
    const longVal = computeOiUsd(pair.long_market || {});
    const shortVal = computeOiUsd(pair.short_market || {});
    if (longVal == null && shortVal == null) return null;
    return (longVal || 0) + (shortVal || 0);
}

function computeTotalVol(pair) {
    const volL = Number(pair?.long_market?.volume_24h);
    const volS = Number(pair?.short_market?.volume_24h);
    const hasL = Number.isFinite(volL);
    const hasS = Number.isFinite(volS);
    if (!hasL && !hasS) return null;
    return (hasL ? volL : 0) + (hasS ? volS : 0);
}

function filterRows(data, filters) {
    return (data || []).filter((pair) => {
        const spreadVal = Number(pair.spread_apr_percent);
        if (!matchesBounds(spreadVal, filters.aprMin, filters.aprMax)) return false;

        const priceSpreadVal = computePriceSpreadPct(pair);
        if (!matchesBounds(priceSpreadVal, filters.priceMin, filters.priceMax))
            return false;

        const oiTotal = computeTotalOi(pair);
        if (!matchesBounds(oiTotal, filters.oiMin, filters.oiMax)) return false;

        const volTotal = computeTotalVol(pair);
        if (!matchesBounds(volTotal, filters.volMin, filters.volMax)) return false;

        return true;
    });
}

function applySearch(rows, term) {
    const t = (term || "").toLowerCase().trim();
    if (!t) return rows;
    return rows.filter((pair) => {
        const sym = (pair.canonical_symbol || "").toLowerCase();
        const longSym = ((pair.long_market || {}).symbol || "").toLowerCase();
        const shortSym = ((pair.short_market || {}).symbol || "").toLowerCase();
        return sym.includes(t) || longSym.includes(t) || shortSym.includes(t);
    });
}

function recomputeAndRender() {
    const base = filterRows(allRows, filtersState);
    const searched = applySearch(base, searchTerm);
    filteredRows = searched;
    renderFundingRows(filteredRows);
    loadLogosForRows(filteredRows);
}

function applyFilters() {
    filtersState = parseFiltersFromInputs();
    recomputeAndRender();
}

function resetFilters() {
    filtersState = {
        aprMin: null,
        aprMax: null,
        priceMin: null,
        priceMax: null,
        oiMin: null,
        oiMax: null,
        volMin: null,
        volMax: null,
    };
    const inputs = document.querySelectorAll("[data-filter-input]");
    inputs.forEach((inp) => {
        inp.value = "";
    });
    recomputeAndRender();
}
