(() => {
  const API_BASE = window.__TFH_API_BASE || "http://localhost:3000";
  const banner = document.getElementById("banner");
  const addrEl = document.getElementById("wallet-address");
  const chainEl = document.getElementById("wallet-chain");
  const sessEl = document.getElementById("session-status");
  const portCard = document.getElementById("portfolio-card");
  const totalEl = document.getElementById("portfolio-total");
  const nativeEl = document.getElementById("portfolio-native");
  const countEl = document.getElementById("portfolio-count");

  const btnBack = document.getElementById("btn-back");
  const btnLogout = document.getElementById("btn-logout");
  if (btnBack) btnBack.onclick = () => (window.location.href = "/");
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

  // Render wallet info
  if (wallet && wallet.address) {
    if (addrEl) addrEl.textContent = wallet.address;
    if (chainEl) chainEl.textContent = wallet.chainId != null ? wallet.chainId : "--";
  } else {
    showBanner("Not logged in (wallet missing).");
    if (portCard) portCard.classList.add("disabled");
    if (sessEl) sessEl.textContent = "Not logged in";
    return;
  }

  if (!token) {
    showBanner("Not logged in (token missing).");
    if (portCard) portCard.classList.add("disabled");
    if (sessEl) sessEl.textContent = "Not logged in";
    return;
  }

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

  // Portfolio placeholders remain "--" for now
  if (totalEl) totalEl.textContent = "--";
  if (nativeEl) nativeEl.textContent = "--";
  if (countEl) countEl.textContent = "--";
})();
