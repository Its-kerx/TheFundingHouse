(async () => {
  const API_BASE = window.__TFH_API_BASE || "http://localhost:3000";
  const banner = document.getElementById("banner");
  const addrEl = document.getElementById("wallet-address");
  const chainEl = document.getElementById("wallet-chain");
  const sessEl = document.getElementById("session-status");
  const portCard = document.getElementById("portfolio-card");
  const totalEl = document.getElementById("portfolio-total");
  const chainsEl = document.getElementById("portfolio-chains");

  const btnBack = document.getElementById("btn-back");
  const btnLogout = document.getElementById("btn-logout");
  if (btnBack) btnBack.onclick = () => (window.location.href = "/pages/dashboard.html");
  if (btnLogout) btnLogout.onclick = () => {
    try {
      localStorage.removeItem("tfh_jwt");
      localStorage.removeItem("tfh_wallet");
    } catch (e) {}
    window.location.href = "/";
  };

  let wallet = null;
  let token = null;
  try {
    wallet = JSON.parse(localStorage.getItem("tfh_wallet") || "null");
    token = localStorage.getItem("tfh_jwt");
  } catch (e) {}

  const showBanner = (msg) => {
    if (banner) {
      banner.textContent = msg;
      banner.style.display = "block";
    }
  };

  const DEFAULT_CHAIN_META = {
    1: { label: "Ethereum", symbol: "ETH", decimals: 18 },
    137: { label: "Polygon", symbol: "MATIC", decimals: 18 },
    11155111: { label: "Sepolia", symbol: "ETH", decimals: 18 },
    999: { label: "HyperliquidEVM", symbol: "HYPE", decimals: 18 },
  };

  const chainMetaOverride = window.__TFH_CHAIN_META || {};

  const getChainMeta = (chainId) => {
    const key = String(chainId);
    return chainMetaOverride[key] || DEFAULT_CHAIN_META[chainId] || null;
  };

  const formatUnits = (weiBig, decimals) => {
    if (weiBig == null) return "--";
    const wei = BigInt(weiBig);
    const dec = BigInt(decimals || 18);
    const base = 10n ** dec;
    const whole = wei / base;
    const fraction = wei % base;
    const fracStr = fraction
      .toString()
      .padStart(Number(dec), "0")
      .slice(0, 4);
    return `${whole.toString()}.${fracStr}`;
  };

  const toHexChainId = (chainId) => {
    const num = Number(chainId);
    if (!Number.isFinite(num)) return null;
    return `0x${num.toString(16)}`;
  };

  const fetchNativeBalance = async (address) => {
    const eth = typeof window !== "undefined" ? window.ethereum : null;
    if (!eth || !address) return null;
    try {
      const balHex = await eth.request({
        method: "eth_getBalance",
        params: [address, "latest"],
      });
      return balHex ? BigInt(balHex) : null;
    } catch (e) {
      return null;
    }
  };

  const renderWalletInfo = (addr, chainIdVal) => {
    if (addrEl) addrEl.textContent = addr || "--";
    if (chainEl) chainEl.textContent = chainIdVal != null ? chainIdVal : "--";
  };

  // Render wallet info
  if (wallet && wallet.address) {
    renderWalletInfo(wallet.address, wallet.chainId);
  } else {
    showBanner("Wallet not connected.");
    if (portCard) portCard.classList.add("disabled");
    if (sessEl) sessEl.textContent = "Not logged in";
    return;
  }

  if (!token) {
    showBanner("Session token missing. Wallet connected in read-only mode.");
    if (sessEl) sessEl.textContent = "Wallet connected";
  }

  if (token) {
    // Test session
    fetch(`${API_BASE}/status`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((res) => {
        if (res.status === 401) throw new Error("Session expired. Please reconnect.");
        if (!res.ok) throw new Error(`Status check failed: ${res.status}`);
        return res.json();
      })
      .then(() => {
        if (sessEl) sessEl.textContent = "Session OK";
      })
      .catch((err) => {
        showBanner(err.message || "Session error");
        if (portCard) portCard.classList.add("disabled");
        if (sessEl) sessEl.textContent = "Session error";
      });
  }

  const chainIdsRaw = Array.isArray(window.__TFH_CHAIN_IDS)
    ? window.__TFH_CHAIN_IDS
    : [wallet.chainId];
  const chainIds = chainIdsRaw
    .map((id) => Number(id))
    .filter((id) => Number.isFinite(id));
  const allowSwitch = window.__TFH_ALLOW_CHAIN_SWITCH === true;

  const eth = typeof window !== "undefined" ? window.ethereum : null;
  const currentChainHex = eth
    ? await eth.request({ method: "eth_chainId" })
    : null;
  const currentChainId = currentChainHex ? parseInt(currentChainHex, 16) : wallet.chainId;

  const balances = [];
  for (const chainId of chainIds) {
    let switched = false;
    if (chainId !== currentChainId) {
      if (!allowSwitch) {
        balances.push({ chainId, balance: null });
        continue;
      }
      const hexId = toHexChainId(chainId);
      if (!hexId) {
        balances.push({ chainId, balance: null });
        continue;
      }
      try {
        await eth.request({
          method: "wallet_switchEthereumChain",
          params: [{ chainId: hexId }],
        });
        switched = true;
      } catch (e) {
        balances.push({ chainId, balance: null });
        continue;
      }
    }

    const wei = await fetchNativeBalance(wallet.address);
    balances.push({ chainId, balance: wei });

    if (switched && currentChainId != null) {
      const backHex = toHexChainId(currentChainId);
      if (backHex) {
        try {
          await eth.request({
            method: "wallet_switchEthereumChain",
            params: [{ chainId: backHex }],
          });
        } catch (e) {
          // ignore
        }
      }
    }
  }

  if (chainsEl) {
    chainsEl.innerHTML = "";
    for (const entry of balances) {
      const meta = getChainMeta(entry.chainId);
      const label = meta?.label || `Chain ${entry.chainId}`;
      const symbol = meta?.symbol || "NATIVE";
      const decimals = meta?.decimals || 18;
      const formatted = formatUnits(entry.balance, decimals);
      const valueText = entry.balance != null ? `${formatted} ${symbol}` : "--";
      const row = document.createElement("div");
      row.className = "row";
      row.innerHTML = `<span>${label}</span><span class="muted">${valueText}</span>`;
      chainsEl.appendChild(row);
    }
  }

  const total = balances.reduce((acc, entry) => {
    if (entry.balance == null) return acc;
    return acc + entry.balance;
  }, 0n);
  const symbols = balances
    .map((entry) => getChainMeta(entry.chainId)?.symbol || "NATIVE")
    .filter((sym, idx, arr) => sym && arr.indexOf(sym) === idx);
  if (totalEl) {
    if (symbols.length === 1 && balances.some((b) => b.balance != null)) {
      const meta = getChainMeta(balances[0].chainId);
      const decimals = meta?.decimals || 18;
      totalEl.textContent = `${formatUnits(total, decimals)} ${symbols[0]}`;
    } else if (balances.some((b) => b.balance != null)) {
      totalEl.textContent = formatUnits(total, 18);
    } else {
      totalEl.textContent = "--";
    }
  }
})();
