import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import { createChart } from "lightweight-charts";
import { motion } from "framer-motion";

const BACKEND_URL = import.meta.env.VITE_BACKEND_URL ?? "http://localhost:8000";
const API = `${BACKEND_URL}/api`;
const WS_BASE = BACKEND_URL.replace(/^http/i, "ws");
const DEFAULT_REFRESH_INTERVAL_MS = 60000;
// Maps RR=4.0 to full gauge; RR>=4.0 remains capped at 100%.
const RR_SCALE_FACTOR = 25;
const REFRESH_OPTIONS = [
  { label: "30s", value: 30000 },
  { label: "1m", value: 60000 },
  { label: "2m", value: 120000 },
];

const cardClassName =
  "border border-[#27272A] bg-[#121214] p-4 transition-colors duration-200 hover:border-[#3F3F46]";
const MotionArticle = motion.article;

const agentStyles = {
  "Technical Analyst": "border-l-[#22D3EE] text-[#22D3EE]",
  "Sentiment Analyst": "border-l-[#10B981] text-[#10B981]",
  "Risk Manager": "border-l-[#F59E0B] text-[#F59E0B]",
  "Chief Synthesizer": "border-l-[#0EA5E9] text-[#0EA5E9]",
};

const statusTone = (value, positive = true) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "text-[#A1A1AA]";
  if (positive) return Number(value) >= 0 ? "text-[#10B981]" : "text-[#EF4444]";
  return Number(value) >= 0 ? "text-[#EF4444]" : "text-[#10B981]";
};

const Gauge = ({ label, value, max = 100, good = true }) => {
  const ratio = Math.max(0, Math.min(1, Number(value ?? 0) / max));
  const fillClass = good ? "bg-[#10B981]" : "bg-[#F59E0B]";
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs text-[#A1A1AA]">
        <span>{label}</span>
        <span>{value ?? "-"}{max === 100 ? "%" : ""}</span>
      </div>
      <div className="h-2 w-full bg-[#27272A]">
        <div className={`h-2 ${fillClass}`} style={{ width: `${ratio * 100}%` }} />
      </div>
    </div>
  );
};

const ChartPanel = ({ market, technical, risk, synthesis }) => {
  useEffect(() => {
    const container = document.getElementById("price-chart-container");
    const candles = market?.candles ?? [];
    if (!container || !candles.length) return;

    container.innerHTML = "";
    const chart = createChart(container, {
      layout: { background: { color: "#121214" }, textColor: "#A1A1AA" },
      grid: { vertLines: { color: "#27272A" }, horzLines: { color: "#27272A" } },
      width: container.clientWidth,
      height: 460,
      rightPriceScale: { borderColor: "#27272A" },
      timeScale: { borderColor: "#27272A", visible: false },
      crosshair: { mode: 1 },
    });

    const series = chart.addCandlestickSeries({
      upColor: "#10B981",
      downColor: "#EF4444",
      borderVisible: false,
      wickUpColor: "#10B981",
      wickDownColor: "#EF4444",
    });

    series.setData(
      candles.map((candle) => ({
        time: candle.t,
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close,
      })),
    );

    const latestTime = candles[candles.length - 1]?.t;
    const addGuideLine = (price, color) => {
      if (!Number.isFinite(price) || !candles.length) return;
      const line = chart.addLineSeries({ color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
      line.setData([
        { time: candles[0].t, value: price },
        { time: latestTime, value: price },
      ]);
    };

    addGuideLine(technical?.indicators?.support, "#10B981");
    addGuideLine(technical?.indicators?.resistance, "#F59E0B");
    addGuideLine(risk?.stop_loss, "#EF4444");
    addGuideLine(risk?.take_profit, "#22D3EE");

    const markers = (market?.overlays?.signal_markers ?? []).map((marker) => {
      const isBuy = marker.signal === "BUY";
      return {
        time: marker.time,
        position: isBuy ? "belowBar" : "aboveBar",
        color: isBuy ? "#10B981" : "#EF4444",
        shape: isBuy ? "arrowUp" : "arrowDown",
        text: marker.signal,
      };
    });
    if (markers.length && typeof series.setMarkers === "function") {
      series.setMarkers(markers);
    }

    const handleResize = () => chart.resize(container.clientWidth, 460);
    window.addEventListener("resize", handleResize);
    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
    };
  }, [
    market?.candles,
    market?.overlays?.signal_markers,
    risk?.stop_loss,
    risk?.take_profit,
    technical?.indicators?.resistance,
    technical?.indicators?.support,
  ]);

  return (
    <section className="col-span-12 border border-[#27272A] bg-[#121214] p-3 lg:col-span-8">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-xs uppercase tracking-[0.2em] text-[#A1A1AA]">Price Action</h2>
        <span className="text-xs text-[#71717A]">{synthesis?.signal ?? "-"} Signal Overlay</span>
      </div>
      <div id="price-chart-container" className="h-[460px] w-full" />
    </section>
  );
};

const BrainPanel = ({ logs }) => (
  <aside className="col-span-12 flex min-h-[500px] flex-col border border-[#27272A] bg-[#050505] lg:col-span-4 lg:row-span-2">
    <div className="border-b border-[#27272A] px-4 py-3">
      <h2 className="text-xs uppercase tracking-[0.2em] text-[#A1A1AA]">Agent Brain</h2>
    </div>
    <div className="flex-1 space-y-2 overflow-y-auto p-3 font-mono text-xs">
      {(logs ?? []).map((entry, index) => (
        <MotionArticle
          key={`${entry.timestamp}-${entry.agent}-${index}`}
          initial={{ opacity: 0.3 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.2 }}
          className={`border-l-2 bg-[#0A0A0B] p-3 ${agentStyles[entry.agent] ?? "border-l-[#3F3F46] text-[#A1A1AA]"}`}
        >
          <p className="mb-1 text-[10px] uppercase tracking-[0.2em] text-[#71717A]">{entry.agent}</p>
          <p className="leading-relaxed text-[#E4E4E7]">{entry.message}</p>
        </MotionArticle>
      ))}
    </div>
  </aside>
);

const MetricCard = ({ label, value, toneClass = "text-[#F4F4F5]" }) => (
  <section className={cardClassName}>
    <p className="text-xs uppercase tracking-[0.2em] text-[#A1A1AA]">{label}</p>
    <p className={`mt-3 text-3xl font-semibold leading-none tracking-tight ${toneClass}`}>{value}</p>
  </section>
);

const App = () => {
  const [symbol, setSymbol] = useState("NSE:ADANIENT");
  const [query, setQuery] = useState("NSE:ADANIENT");
  const [snapshot, setSnapshot] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [refreshIntervalMs, setRefreshIntervalMs] = useState(DEFAULT_REFRESH_INTERVAL_MS);
  const wsRef = useRef(null);

  const fetchFallback = useCallback(
    async (symbolValue) => {
      setLoading(true);
      setError("");
      try {
        await axios.post(`${API}/subscriptions/${encodeURIComponent(symbolValue)}`);
        const response = await axios.get(`${API}/dashboard/${encodeURIComponent(symbolValue)}`);
        setSnapshot(response.data);
      } catch (err) {
        setError(err?.response?.data?.detail || "Unable to fetch dashboard data");
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  const connectWebSocket = useCallback(
    (symbolValue) => {
      if (wsRef.current) {
        wsRef.current.close();
      }

      const socket = new WebSocket(`${WS_BASE}/ws/dashboard/${encodeURIComponent(symbolValue)}?news_limit=25`);
      wsRef.current = socket;
      socket.onopen = () => setError("");
      socket.onmessage = (event) => {
        try {
          setSnapshot(JSON.parse(event.data));
          setLoading(false);
        } catch {
          setError("Invalid stream payload");
        }
      };
      socket.onerror = (event) => {
        console.warn("WebSocket error", event);
        setError("Realtime stream error; using HTTP refresh fallback.");
      };
      socket.onclose = () => {};
    },
    [],
  );

  useEffect(() => {
    fetchFallback(symbol);
    connectWebSocket(symbol);
    const timer = setInterval(() => fetchFallback(symbol), refreshIntervalMs);
    return () => {
      clearInterval(timer);
      if (wsRef.current) wsRef.current.close();
    };
  }, [connectWebSocket, fetchFallback, refreshIntervalMs, symbol]);

  const synthesized = snapshot?.agents?.synthesizer;
  const risk = snapshot?.agents?.risk;
  const sentiment = snapshot?.agents?.sentiment;
  const technical = snapshot?.agents?.technical;
  const market = snapshot?.market;
  const paperTrading = snapshot?.paper_trading?.summary;
  const probability = Number(synthesized?.win_probability ?? 0);
  const rr = Number(risk?.risk_reward_ratio ?? 0);

  const cards = useMemo(
    () => [
      {
        label: "Signal",
        value: synthesized?.signal ?? "-",
        toneClass:
          synthesized?.signal === "BUY"
            ? "text-[#10B981]"
            : synthesized?.signal === "SELL"
              ? "text-[#EF4444]"
              : "text-[#F59E0B]",
      },
      {
        label: "Win Probability",
        value: synthesized?.win_probability ? `${synthesized.win_probability}%` : "-",
        toneClass: statusTone(synthesized?.win_probability),
      },
      {
        label: "Sentiment Score",
        value: sentiment?.sentiment_score ?? "-",
        toneClass: statusTone(sentiment?.sentiment_score),
      },
      {
        label: "Risk:Reward",
        value: risk?.risk_reward_ratio ?? "-",
        toneClass: risk?.risk_reward_ratio >= 2 ? "text-[#10B981]" : "text-[#F59E0B]",
      },
      {
        label: "Confidence Band",
        value: synthesized?.confidence_band ?? "-",
        toneClass: synthesized?.confidence_band === "high" ? "text-[#10B981]" : "text-[#F59E0B]",
      },
    ],
    [
      risk?.risk_reward_ratio,
      sentiment?.sentiment_score,
      synthesized?.confidence_band,
      synthesized?.signal,
      synthesized?.win_probability,
    ],
  );

  return (
    <div className="relative min-h-screen overflow-x-hidden bg-[#0A0A0B] p-2 font-sans text-[#F4F4F5] md:p-4">
      <header className="relative z-10 border border-[#27272A] bg-[#121214] p-4">
        <div className="grid gap-3 lg:grid-cols-4 lg:items-center">
          <div className="text-xs uppercase tracking-[0.2em] text-[#A1A1AA]">Kubera Institutional MAS</div>
          <form
            className="flex items-center gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              const next = query.trim().toUpperCase();
              if (next) {
                setSymbol(next);
                setLoading(true);
              }
            }}
          >
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              className="w-full border border-[#27272A] bg-[#0A0A0B] px-3 py-2 text-sm text-[#F4F4F5] outline-none focus:border-[#0EA5E9]"
              placeholder="Ticker (e.g. NSE:RELIANCE, NASDAQ:AAPL)"
            />
            <button
              type="submit"
              className="border border-[#0EA5E9] bg-[#0EA5E9] px-3 py-2 text-xs font-semibold uppercase tracking-[0.12em] text-[#050505] hover:bg-[#22D3EE]"
            >
              Load
            </button>
          </form>
          <div className="text-right text-xs text-[#A1A1AA]">
            {loading ? "Refreshing..." : `HTTP fallback every ${refreshIntervalMs / 1000}s`} · {symbol}
          </div>
          <div className="flex items-center justify-end gap-2 text-xs text-[#A1A1AA]">
            <select
              value={refreshIntervalMs}
              onChange={(event) => setRefreshIntervalMs(Number(event.target.value))}
              className="border border-[#27272A] bg-[#0A0A0B] px-2 py-2 text-xs"
            >
              {REFRESH_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="border border-[#22D3EE] px-3 py-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-[#22D3EE]"
              onClick={() => fetchFallback(symbol)}
            >
              Refresh
            </button>
          </div>
        </div>
      </header>

      {error ? <div className="relative z-10 mt-4 border border-[#EF4444] bg-[#2A1113] p-3 text-sm text-[#FCA5A5]">{error}</div> : null}
      {snapshot?.warning ? (
        <div className="relative z-10 mt-2 border border-[#F59E0B] bg-[#2A220F] p-2 text-xs text-[#FCD34D]">{snapshot.warning}</div>
      ) : null}

      <main className="relative z-10 mt-4 grid grid-cols-1 gap-4 lg:grid-cols-12">
        <section className="col-span-12 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:col-span-8 lg:grid-cols-5">
          {cards.map((card) => (
            <MetricCard key={card.label} {...card} />
          ))}
        </section>

        <BrainPanel logs={snapshot?.brain_log ?? []} />
        <ChartPanel market={market} risk={risk} technical={technical} synthesis={synthesized} />

        <section className={`${cardClassName} col-span-12 lg:col-span-4`}>
          <h3 className="mb-3 text-xs uppercase tracking-[0.2em] text-[#A1A1AA]">Confidence vs Risk</h3>
          <div className="space-y-3">
            <Gauge label="Winning Probability" value={probability} />
            <Gauge label="Risk/Reward Strength" value={Math.min(100, rr * RR_SCALE_FACTOR)} good={rr >= 2} />
            <Gauge label="Sentiment Conviction" value={Math.abs(sentiment?.sentiment_score ?? 0)} />
          </div>
        </section>

        <section className={`${cardClassName} col-span-12 lg:col-span-4`}>
          <h3 className="mb-3 text-xs uppercase tracking-[0.2em] text-[#A1A1AA]">Paper Trading Engine</h3>
          <div className="space-y-2 text-sm text-[#E4E4E7]">
            <p>Total trades: {paperTrading?.total_trades ?? 0}</p>
            <p>Win rate: {paperTrading?.win_rate ?? 0}%</p>
            <p className={statusTone(paperTrading?.realized_pnl)}>Realized PnL: {paperTrading?.realized_pnl ?? 0}</p>
            <p className={statusTone(paperTrading?.unrealized_pnl)}>Unrealized PnL: {paperTrading?.unrealized_pnl ?? 0}</p>
          </div>
        </section>

        <section className={`${cardClassName} col-span-12 lg:col-span-8`}>
          <h3 className="mb-3 text-xs uppercase tracking-[0.2em] text-[#A1A1AA]">Risk Controls</h3>
          <div className="grid gap-3 text-sm text-[#E4E4E7] md:grid-cols-4">
            <p>
              Last Price:
              <span className={`ml-2 font-semibold ${statusTone((market?.current_price ?? 0) - (market?.previous_close ?? 0))}`}>
                {market?.current_price ?? "-"}
              </span>
            </p>
            <p>
              Stop Loss: <span className="ml-2 font-semibold text-[#EF4444]">{risk?.stop_loss ?? "-"}</span>
            </p>
            <p>
              Take Profit: <span className="ml-2 font-semibold text-[#10B981]">{risk?.take_profit ?? "-"}</span>
            </p>
            <p>
              VaR 95%: <span className="ml-2 font-semibold text-[#F59E0B]">{risk?.value_at_risk_95 ?? "-"}</span>
            </p>
            <p>
              Position Size: <span className="ml-2 font-semibold text-[#22D3EE]">{risk?.position_size_pct ?? "-"}%</span>
            </p>
          </div>
          <p className="mt-3 text-xs text-[#A1A1AA]">{risk?.risk_warning ?? "Risk guidance unavailable."}</p>
        </section>
      </main>
    </div>
  );
};

export default App;
