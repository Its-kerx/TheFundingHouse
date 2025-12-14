// --- 1. Sidebar Renderer (Sin Spot-Perp) ---
function renderSidebar() {
    const container = document.getElementById('sidebar-container');
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

// --- 2. Lógica de Interfaz (Dropdown y Temporalidades) ---
let currentTimeframe = "live";
let allRows = [];
let filteredRows = [];
let logoMap = {};

function initUIInteractions() {
    // A. Dropdown de Plataformas
    const btn = document.getElementById('platforms-btn');
    const dropdown = document.getElementById('platforms-dropdown');
    
    if (btn && dropdown) {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            dropdown.classList.toggle('hidden');
        });

        // Cerrar si clic fuera
        document.addEventListener('click', (e) => {
            if (!btn.contains(e.target) && !dropdown.contains(e.target)) {
                dropdown.classList.add('hidden');
            }
        });
    }

    // B. Filtros de Tiempo
    const timeButtons = document.querySelectorAll('.time-btn');
    timeButtons.forEach(button => {
        button.addEventListener('click', () => {
            // Remover clase active de todos
            timeButtons.forEach(b => b.classList.remove('active'));
            // Añadir al clickeado
            button.classList.add('active');

            const tf = button.dataset.val || "live";
            currentTimeframe = tf;
            const tableBody = document.querySelector("[data-funding-table-body]") || document.getElementById("funding-table-body");
            if (tableBody) {
                tableBody.innerHTML = '';
            }

            if (tf === "live") {
                loadFundingLive();
            } else {
                if (tableBody) {
                    tableBody.innerHTML = '<tr><td colspan="8" class="text-center py-10 text-slate-400">Not implemented yet</td></tr>';
                }
            }
        });
    });

    // Search filter
    const searchInput = document.querySelector("[data-funding-search]");
    if (searchInput) {
        searchInput.addEventListener("input", (e) => {
            const term = (e.target.value || "").toLowerCase().trim();
            if (!term) {
                filteredRows = [...allRows];
            } else {
                filteredRows = allRows.filter((pair) => {
                    const sym = (pair.canonical_symbol || "").toLowerCase();
                    const longSym = ((pair.long_market || {}).symbol || "").toLowerCase();
                    const shortSym = ((pair.short_market || {}).symbol || "").toLowerCase();
                    return (
                        sym.includes(term) ||
                        longSym.includes(term) ||
                        shortSym.includes(term)
                    );
                });
            }
            renderFundingRows(filteredRows);
            loadLogosForRows(filteredRows);
        });
    }
}

// --- 3. Datos y Tablas ---
function initDashboard() {
    loadFundingLive();
}

async function loadFundingLive() {
    const tableBody = document.querySelector("[data-funding-table-body]") || document.getElementById("funding-table-body");
    if (!tableBody) return;

    tableBody.innerHTML = "";

    if (currentTimeframe !== "live") {
        tableBody.innerHTML = '<tr><td colspan="8" class="text-center py-10 text-slate-400">Not implemented yet</td></tr>';
        return;
    }

    try {
        const params = new URLSearchParams({
            min_spread_apr_percent: "0",
            limit: "100",
        });
        const res = await fetch(`/api/funding/live?${params.toString()}`);
        if (!res.ok) {
            console.error("Failed to fetch /api/funding/live", res.status);
            tableBody.innerHTML = '<tr><td colspan="8" class="text-center py-4 text-red-400">Error loading data</td></tr>';
            return;
        }
        const data = await res.json();

        data.sort((a, b) => (b.spread_apr_percent || 0) - (a.spread_apr_percent || 0));

        allRows = data;
        filteredRows = [...data];
        renderFundingRows(filteredRows);
        loadLogosForRows(filteredRows);
    } catch (err) {
        console.error("Error loading funding live:", err);
        tableBody.innerHTML = '<tr><td colspan="8" class="text-center py-4 text-red-400">Error loading data</td></tr>';
    }
}

function renderFundingRows(data) {
    const tableBody = document.querySelector("[data-funding-table-body]") || document.getElementById("funding-table-body");
    if (!tableBody) return;

    if (!data || data.length === 0) {
        tableBody.innerHTML = '<tr><td colspan="8" class="text-center py-10 text-slate-400">No data available</td></tr>';
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
        const volTotal = (Number.isFinite(volL) ? volL : 0) + (Number.isFinite(volS) ? volS : 0);
        return {
            ...pair,
            __volL: volL,
            __volS: volS,
            __volTotal: volTotal,
        };
    });

    const maxVolTotal = Math.max(1, ...rows.map((r) => Number.isFinite(r.__volTotal) ? r.__volTotal : 0));

    rows.forEach((pair, i) => {
        const long = pair.long_market || {};
        const short = pair.short_market || {};

        const longExchange = (long.exchange || "L").toString();
        const shortExchange = (short.exchange || "S").toString();

        const computeOiUsd = (mkt) => {
            const oi = Number(mkt.open_interest);
            const price = Number(mkt.mark_price);
            const ex = (mkt.exchange || "").toString().toLowerCase();
            if (!isFinite(oi) || oi <= 0) return null;
            if (ex === "hyperliquid") {
                if (!isFinite(price) || price <= 0) return null;
                return oi * price;
            }
            return oi;
        };

        const longOiVal = computeOiUsd(long);
        const shortOiVal = computeOiUsd(short);
        const longOi = longOiVal != null ? fmtNum(longOiVal) : "—";
        const shortOi = shortOiVal != null ? fmtNum(shortOiVal) : "—";
        const totalOiVal = (longOiVal || 0) + (shortOiVal || 0);
        const totalOi = (longOiVal == null && shortOiVal == null) ? "—" : fmtNum(totalOiVal);

        const priceA = Number(long.mark_price);
        const priceB = Number(short.mark_price);
        let priceSpreadPctText = "—";
        if (Number.isFinite(priceA) && Number.isFinite(priceB) && priceA > 0 && priceB > 0) {
            const mid = (priceA + priceB) / 2;
            if (mid > 0) {
                const priceSpreadPct = ((priceB - priceA) / mid) * 100;
                priceSpreadPctText = `${priceSpreadPct.toFixed(2)}%`;
            }
        }

        const volLDisplay = Number.isFinite(pair.__volL) ? pair.__volL : null;
        const volSDisplay = Number.isFinite(pair.__volS) ? pair.__volS : null;
        const volLText = volLDisplay != null && volLDisplay > 0 ? fmtNum(volLDisplay) : "—";
        const volSText = volSDisplay != null && volSDisplay > 0 ? fmtNum(volSDisplay) : "—";
        const volTotal = (volLDisplay ?? 0) + (volSDisplay ?? 0);
        const totalVolText = (volLDisplay == null && volSDisplay == null) ? "—" : fmtNum(volTotal);

        const symbolKey = (pair.canonical_symbol || pair.pair_id || "").toString().toUpperCase();
        const logoUrl = symbolKey ? logoMap[symbolKey] : undefined;
        const tokenVisual = logoUrl
            ? `<img src="${logoUrl}" alt="${pair.canonical_symbol || ""}" class="w-[18px] h-[18px] rounded-full flex-none" style="margin-right:4px;" onerror="this.style.display='none';" />`
            : `<div class="w-[22px] h-[22px] rounded-full bg-slate-700 flex items-center justify-center text-[10px] flex-none">${(pair.canonical_symbol || "?")[0]}</div>`;

        const tr = document.createElement("tr");
        tr.className = "hover:bg-white/5 transition-colors group border-b border-white/5";
        tr.innerHTML = `
            <td class="px-4 py-3 text-slate-400 first:rounded-l-lg">${i + 1}</td>
            <td class="px-4 py-3 text-white whitespace-nowrap">
                <div class="inline-flex items-center gap-2 whitespace-nowrap" style="line-height:1;">
                    ${tokenVisual}
                    <span class="font-semibold inline-block">${pair.canonical_symbol || pair.pair_id || "-"}</span>
                </div>
            </td>
            <td class="px-4 py-3 text-brandTeal font-mono">${fmtPercent(pair.spread_apr_percent)}</td>
            <td class="px-4 py-3 text-slate-300 text-xs">
                ${longExchange.substring(0,2).toUpperCase()} / ${shortExchange.substring(0,2).toUpperCase()}
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
        .map((r) => (r.canonical_symbol || r.pair_id || "").toString().toUpperCase())
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
