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
            
            // Aquí podrías llamar a tu backend con la nueva temporalidad
            console.log("Timeframe changed to:", button.dataset.val);
            // initDashboard(button.dataset.val); // Ejemplo de recarga
        });
    });
}

// --- 3. Datos y Tablas ---
function initDashboard() {
    loadFundingLive();
}

function renderTableRows(data, container) {
    if (!data || data.length === 0) {
        container.innerHTML = '<tr><td colspan="8" class="text-center py-10 text-slate-400">No data available</td></tr>';
        return;
    }

    container.innerHTML = data.map((item, i) => `
        <tr class="hover:bg-white/5 transition-colors group">
            <td class="px-4 py-3 text-slate-400 border-b border-white/5 first:rounded-l-lg">${i + 1}</td>
            <td class="px-4 py-3 font-bold text-white border-b border-white/5 flex items-center gap-2">
                <div class="w-6 h-6 rounded-full bg-slate-700 flex items-center justify-center text-[10px]">${item.token[0]}</div>
                ${item.token}
            </td>
            <td class="px-4 py-3 text-brandTeal font-mono border-b border-white/5">${item.apr}%</td>
            <td class="px-4 py-3 text-slate-300 border-b border-white/5 text-xs">
                ${item.long_platform.substring(0,2).toUpperCase()} / ${item.short_platform.substring(0,2).toUpperCase()}
            </td>
            <td class="px-4 py-3 text-slate-400 border-b border-white/5 font-mono hidden md:table-cell">${item.spread}%</td>
            <td class="px-4 py-3 text-slate-400 border-b border-white/5 font-mono text-xs hidden md:table-cell">
                <div class="text-green-400/70">L: ${item.oi_long}</div>
                <div class="text-red-400/70">S: ${item.oi_short}</div>
            </td>
            <td class="px-4 py-3 text-slate-500 border-b border-white/5 font-mono hidden md:table-cell">${item.volume}</td>
            <td class="px-4 py-3 text-right border-b border-white/5 last:rounded-r-lg">
                <button class="text-brandTeal hover:bg-brandTeal/10 p-2 rounded transition-colors"><i data-lucide="external-link" class="w-4 h-4"></i></button>
            </td>
        </tr>
    `).join('');
    
    if(window.lucide) lucide.createIcons();
}

async function loadFundingLive() {
    const tableBody = document.querySelector("[data-funding-table-body]") || document.getElementById("funding-table-body");
    if (!tableBody) return;

    tableBody.innerHTML = "";

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

        if (!data.length) {
            tableBody.innerHTML = '<tr><td colspan="8" class="text-center py-10 text-slate-400">No data available</td></tr>';
            return;
        }

        const fmtPercent = (v) => (v == null ? "-" : `${v.toFixed(1)}%`);
        const fmtNum = (v) => {
            if (v == null) return "-";
            if (v >= 1_000_000_000) return (v / 1_000_000_000).toFixed(1) + "B";
            if (v >= 1_000_000) return (v / 1_000_000).toFixed(1) + "M";
            if (v >= 1_000) return (v / 1_000).toFixed(1) + "K";
            return v.toString();
        };

        data.forEach((pair, i) => {
            const long = pair.long_market || {};
            const short = pair.short_market || {};

            const longExchange = long.exchange || "L";
            const shortExchange = short.exchange || "S";

            const longOi = fmtNum(long.open_interest);
            const shortOi = fmtNum(short.open_interest);
            const vol24h = fmtNum(
                long.volume_24h != null ? long.volume_24h : short.volume_24h
            );

            const tr = document.createElement("tr");
            tr.className = "hover:bg-white/5 transition-colors group";
            tr.innerHTML = `
                <td class="px-4 py-3 text-slate-400 border-b border-white/5 first:rounded-l-lg">${i + 1}</td>
                <td class="px-4 py-3 font-bold text-white border-b border-white/5 flex items-center gap-2">
                    <div class="w-6 h-6 rounded-full bg-slate-700 flex items-center justify-center text-[10px]">${(pair.canonical_symbol || "?")[0]}</div>
                    ${pair.canonical_symbol || pair.pair_id || "-"}
                </td>
                <td class="px-4 py-3 text-brandTeal font-mono border-b border-white/5">${fmtPercent(pair.spread_apr_percent)}</td>
                <td class="px-4 py-3 text-slate-300 border-b border-white/5 text-xs">
                    ${longExchange.substring(0,2).toUpperCase()} / ${shortExchange.substring(0,2).toUpperCase()}
                </td>
                <td class="px-4 py-3 text-slate-400 border-b border-white/5 font-mono hidden md:table-cell">${fmtPercent(pair.spread_apr_percent)}</td>
                <td class="px-4 py-3 text-slate-400 border-b border-white/5 font-mono text-xs hidden md:table-cell">
                    <div class="text-green-400/70">L: ${longOi}</div>
                    <div class="text-red-400/70">S: ${shortOi}</div>
                </td>
                <td class="px-4 py-3 text-slate-500 border-b border-white/5 font-mono hidden md:table-cell">${vol24h}</td>
                <td class="px-4 py-3 text-right border-b border-white/5 last:rounded-r-lg">
                    <button class="text-brandTeal hover:bg-brandTeal/10 p-2 rounded transition-colors"><i data-lucide="external-link" class="w-4 h-4"></i></button>
                </td>
            `;
            tableBody.appendChild(tr);
        });

        if (window.lucide) window.lucide.createIcons();
    } catch (err) {
        console.error("Error loading funding live:", err);
        tableBody.innerHTML = '<tr><td colspan="8" class="text-center py-4 text-red-400">Error loading data</td></tr>';
    }
}
