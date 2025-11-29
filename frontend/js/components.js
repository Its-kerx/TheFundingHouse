// ============================================
// WEB COMPONENT: Sidebar (MADRE - se define aquí)
// ============================================
class AppSidebar extends HTMLElement {
    connectedCallback() {
        this.innerHTML = `
            <aside class="sidebar">
                <div class="sidebar-header">
                    <h1 class="logo">TheFundingHouse</h1>
                    <p class="tagline">Funding Rate Explorer</p>
                </div>

                <nav class="sidebar-nav">
                    <a href="dashboard.html" class="nav-item active">
                        <span class="icon">📊</span>
                        <span class="text">Funding Explorer</span>
                    </a>
                </nav>

                <div class="sidebar-footer">
                    <div class="user-info">
                        <span class="user-name">Connect Wallet</span>
                    </div>
                </div>
            </aside>
        `;

        // Marcar como activo el link actual
        this.setActiveLink();
    }

    setActiveLink() {
        const currentPage = window.location.pathname.split('/').pop() || 'dashboard.html';
        const links = this.querySelectorAll('.nav-item');
        links.forEach(link => {
            if (link.getAttribute('href') === currentPage) {
                link.classList.add('active');
            } else {
                link.classList.remove('active');
            }
        });
    }
}

// Registrar el componente
customElements.define('app-sidebar', AppSidebar);


// ============================================
// LÓGICA DE DATOS: Funding Rates Table
// ============================================
document.addEventListener('DOMContentLoaded', async () => {
    const tableBody = document.getElementById('funding-table-body');

    if (!tableBody) return; // Si no estamos en la página de funding explorer, salir

    try {
        // Llamar al API Gateway
        const response = await fetch('http://localhost:3000/api/v1/data-ingestion/funding-rates?limit=50');
        const data = await response.json();

        if (data.length === 0) {
            tableBody.innerHTML = '<tr><td colspan="7" class="loading-cell">No data available</td></tr>';
            return;
        }

        // Renderizar datos en la tabla
        tableBody.innerHTML = data.map((item, index) => {
            const apr = item.apr || 0;
            const aprClass = apr > 10 ? 'apr-high' : apr > 5 ? 'apr-medium' : 'apr-low';

            return `
                <tr>
                    <td>${index + 1}</td>
                    <td>
                        <div class="token-cell">
                            <strong>${item.pair}</strong>
                            <span class="exchange-badge">${item.exchange_id}</span>
                        </div>
                    </td>
                    <td class="${aprClass}">
                        <strong>${apr.toFixed(2)}%</strong>
                    </td>
                    <td>
                        <div class="pair-cell">
                            <span class="long">Long</span>
                            <span class="short">Short</span>
                        </div>
                    </td>
                    <td>${(item.spread * 100).toFixed(3)}%</td>
                    <td>$${formatNumber(item.open_interest)}</td>
                    <td>$${formatNumber(item.volume_24h)}</td>
                </tr>
            `;
        }).join('');

    } catch (error) {
        console.error('Error loading funding rates:', error);
        tableBody.innerHTML = `
            <tr>
                <td colspan="7" class="error-cell">
                    Error loading data: ${error.message}
                </td>
            </tr>
        `;
    }
});

// Utility: Formatear números grandes
function formatNumber(num) {
    if (num >= 1_000_000_000) {
        return (num / 1_000_000_000).toFixed(2) + 'B';
    } else if (num >= 1_000_000) {
        return (num / 1_000_000).toFixed(2) + 'M';
    } else if (num >= 1_000) {
        return (num / 1_000).toFixed(2) + 'K';
    }
    return num.toFixed(2);
}
