from typing import List, Dict, Any
from hyperliquid.info import Info
from datetime import datetime, timezone


def get_now_ms() -> int:
    """Timestamp actual en milisegundos (UTC) en ms."""
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def fetch_raw_data(info: Info):
    """
    Descarga datos base de Hyperliquid mainnet:
      - universe: lista de mercados perp
      - asset_ctxs: contexto por mercado (OI, funding, vol, etc.)
      - mids: mid price de cada token
    """
    meta, asset_ctxs = info.meta_and_asset_ctxs()
    universe = meta["universe"]
    mids = info.all_mids()
    return universe, asset_ctxs, mids


def build_rows(universe: List[Dict[str, Any]],
               asset_ctxs: List[Dict[str, Any]],
               mids: Dict[str, str]) -> List[Dict[str, Any]]:
    """
    Una fila por token con:
      token, price, open_interest, volume_24h, funding_8h, apr
    """
    rows: List[Dict[str, Any]] = []

    for i, asset in enumerate(universe):
        token = asset["name"]
        ctx = asset_ctxs[i]

        # Precio mid
        raw_price = mids.get(token)
        if raw_price is None:
            continue
        price = float(raw_price)

        # Open interest (en notional USD, nombre puede variar según versión)
        raw_oi = (
            ctx.get("openInterest")
            or ctx.get("openInterestUsd")
            or ctx.get("openInterestNotional")
            or "0"
        )
        open_interest = float(raw_oi)

        # Volumen notional 24h (USD)
        raw_vol = ctx.get("dayNtlVlm") or ctx.get("dayNtlVlmUsd") or "0"
        volume_24h = float(raw_vol)

        # Funding normalizado a 8h (número, tipo 0.00015)
        raw_funding_8h = ctx.get("funding") or ctx.get("currentFunding") or "0"
        funding_8h = float(raw_funding_8h)

        # APR anualizado COMO NÚMERO, NO EN %
        # (funding_8h * 3 intervalos/día * 365 días)
        apr = funding_8h * 3 * 365  # ej: 2.7 -> 270%

        row = {
            "token": token,
            "price": price,
            "open_interest": open_interest,
            "volume_24h": volume_24h,
            "funding_8h": funding_8h,  # NUMÉRICO
            "apr": apr,                # NUMÉRICO (no %)
        }
        rows.append(row)

    return rows


def rank_rows_by_apr(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Ordena por APR descendente y añade 'position'."""
    rows_sorted = sorted(rows, key=lambda r: r["apr"], reverse=True)
    for idx, row in enumerate(rows_sorted, start=1):
        row["position"] = idx
    return rows_sorted


def main():
    info = Info()  # mainnet perps

    _now_ms = get_now_ms()  # solo por si lo necesitas luego

    universe, asset_ctxs, mids = fetch_raw_data(info)
    rows = build_rows(universe, asset_ctxs, mids)
    ranked = rank_rows_by_apr(rows)

    print(f"Total tokens: {len(ranked)}\n")
    print(f"{'Pos':>3}  {'Token':<8} {'Funding(8h)':>12} {'APR(num)':>12} {'Price':>12} {'OI':>15} {'Vol24h':>18}")
    print("-" * 80)
    for row in ranked[:20]:
        print(
            f"{row['position']:>3}  "
            f"{row['token']:<8} "
            f"{row['funding_8h']:>12.6f} "     # funding 8h NUMÉRICO
            f"{row['apr']:>12.6f} "            # apr NUMÉRICO
            f"{row['price']:>12.4f} "
            f"{row['open_interest']:>15.2f} "
            f"{row['volume_24h']:>18.2f}"
        )


if __name__ == "__main__":
    main()