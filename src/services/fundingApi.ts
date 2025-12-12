export type FundingMarketSide = {
  exchange: string;
  symbol: string;
  funding_rate: number;
  funding_percent: number;
  apr_percent: number;
  mark_price: number;
  open_interest: number | null;
  volume_24h: number | null;
  last_updated: string;
};

export type FundingPair = {
  pair_id: string;
  canonical_symbol: string;
  timestamp: string;
  spread_apr_percent: number;
  long_market: FundingMarketSide;
  short_market: FundingMarketSide;
};

export async function fetchFundingLive(opts?: {
  minSpreadAprPercent?: number;
  limit?: number;
}): Promise<FundingPair[]> {
  const minSpread = opts?.minSpreadAprPercent ?? 0;
  const limit = opts?.limit ?? 100;

  const params = new URLSearchParams({
    min_spread_apr_percent: String(minSpread),
    limit: String(limit),
  });

  const res = await fetch(`/api/funding/live?${params.toString()}`);
  if (!res.ok) {
    throw new Error(`Failed to fetch funding live: ${res.status}`);
  }
  const data = await res.json();
  return data as FundingPair[];
}
