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
    const tableBody = document.getElementById('funding-table-body');
    // Datos de ejemplo para que veas que funciona
    const mockData = [
        { token: "BTC", apr: 25.5, long_platform: "Hyperliquid", short_platform: "Paradex", spread: 0.04, oi_long: "4M", oi_short: "3.2M", volume: "120M" },
        { token: "ETH", apr: 19.2, long_platform: "Backpack", short_platform: "Aster", spread: 0.01, oi_long: "12M", oi_short: "10M", volume: "350M" },
        { token: "SOL", apr: 15.8, long_platform: "Paradex", short_platform: "Hyperliquid", spread: 0.08, oi_long: "800K", oi_short: "900K", volume: "45M" },
        { token: "SUI", apr: 12.1, long_platform: "Aster", short_platform: "Backpack", spread: 0.05, oi_long: "1.2M", oi_short: "1.1M", volume: "22M" }
    ];
    
    // Si tienes backend real, descomenta el fetch y comenta renderTableRows(mockData...)
    /*
    fetch('/api/v1/opportunities')
        .then(res => res.json())
        .then(data => renderTableRows(data, tableBody))
        .catch(err => {
            console.error(err);
            tableBody.innerHTML = '<tr><td colspan="8" class="text-center py-4 text-red-400">Error loading data</td></tr>';
        });
    */
   
    renderTableRows(mockData, tableBody);
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