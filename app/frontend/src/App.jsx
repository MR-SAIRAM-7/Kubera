import { useEffect, useMemo, useState } from "react";
import axios from "axios";
import { createChart } from "lightweight-charts";
import { motion } from "framer-motion";

const BACKEND_URL = import.meta.env.VITE_BACKEND_URL ?? "";
const API = `${BACKEND_URL}/api`;
const REFRESH_INTERVAL_MS = 120000;

const agentStyles = {
  "Technical Analyst": "border-l-[#22D3EE] text-[#22D3EE]",
  "Sentiment Analyst": "border-l-[#10B981] text-[#10B981]",
  "Risk Manager": "border-l-[#F59E0B] text-[#F59E0B]",
  "Chief Synthesizer": "border-l-[#0EA5E9] text-[#0EA5E9]",
};

const statusTone = (value, positive = true) => {
  if (value === null || value === undefined) {
    return "text-[#A1A1AA]";
  }
  if (positive) {
    return Number(value) >= 0 ? "text-[#10B981]" : "text-[#EF4444]";
  }
  return Number(value) >= 0 ? "text-[#EF4444]" : "text-[#10B981]";
};

const cardClassName =
  "border border-[#27272A] bg-[#121214] p-4 transition-colors duration-200 hover:border-[#3F3F46]";

const ChartPanel = ({ candles }) => {
  useEffect(() => {
    const container = document.getElementById("price-chart-container");
    if (!container || !candles?.length) return;

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

    const handleResize = () => {
      chart.resize(container.clientWidth, 460);
    };
    window.addEventListener("resize", handleResize);
    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
    };
  }, [candles]);

  return (
    <section className="col-span-12 border border-[#27272A] bg-[#121214] p-3 lg:col-span-8">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-xs uppercase tracking-[0.2em] text-[#A1A1AA]">Price Action</h2>
        <span className="text-xs text-[#71717A]">Lightweight Candles</span>
      </div>
      <div id="price-chart-container" className="h-[460px] w-full" data-testid="price-chart-container" />
    </section>
  );
};

const BrainPanel = ({ logs }) => {
  return (
    <aside className="col-span-12 flex min-h-[500px] flex-col border border-[#27272A] bg-[#050505] lg:col-span-4 lg:row-span-2">
      <div className="border-b border-[#27272A] px-4 py-3">
        <h2 className="text-xs uppercase tracking-[0.2em] text-[#A1A1AA]">Brain Panel</h2>
      </div>
      <div className="flex-1 space-y-2 overflow-y-auto p-3 font-mono text-xs">
        {logs?.map((entry, index) => (
          <motion.article
            key={`${entry.timestamp}-${entry.agent}-${index}`}
            initial={{ opacity: 0.3 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.25 }}
            className={`border-l-2 bg-[#0A0A0B] p-3 ${agentStyles[entry.agent] ?? "border-l-[#3F3F46] text-[#A1A1AA]"}`}
          >
            <p className="mb-1 text-[10px] uppercase tracking-[0.2em] text-[#71717A]">{entry.agent}</p>
            <p className="leading-relaxed text-[#E4E4E7]">{entry.message}</p>
          </motion.article>
        ))}
      </div>
    </aside>
  );
};

const NewsFeed = ({ newsItems }) => {
  return (
    <section className={`${cardClassName} col-span-12 lg:col-span-4`} data-testid="news-feed">
      <header className="mb-3 flex items-center justify-between">
        <h3 className="text-xs uppercase tracking-[0.2em] text-[#A1A1AA]">News & Sentiment</h3>
        <span className="text-xs text-[#71717A]">{newsItems?.length ?? 0} items</span>
      </header>
      <ul className="max-h-[300px] space-y-2 overflow-y-auto">
        {newsItems?.slice(0, 8).map((item, idx) => (
          <li key={`${item.link}-${idx}`} className="border border-[#27272A] bg-[#0A0A0B] p-3 hover:bg-[#18181B]">
            <a
              href={item.link}
              target="_blank"
              rel="noreferrer"
              className="block text-sm text-[#F4F4F5] transition-colors duration-200 hover:text-[#22D3EE]"
            >
              {item.title}
            </a>
            <p className="mt-1 text-xs text-[#71717A]">{item.source}</p>
          </li>
        ))}
      </ul>
    </section>
  );
};

const BacktestPanel = ({ synthesis }) => {
  return (
    <section className={`${cardClassName} col-span-12 lg:col-span-4`} data-testid="backtesting-results">
      <h3 className="mb-3 text-xs uppercase tracking-[0.2em] text-[#A1A1AA]">Backtesting Reflection</h3>
      <div className="space-y-3 text-sm text-[#E4E4E7]">
        <p>
          Matched setups: <strong>{synthesis?.historical_matches ?? "-"}</strong>
        </p>
        <p>
          Winning setups: <strong>{synthesis?.historical_wins ?? "-"}</strong>
        </p>
        <p className="text-[#A1A1AA]">{synthesis?.justification ?? "No synthesis available."}</p>
      </div>
    </section>
  );
};

const MetricCard = ({ testId, label, value, toneClass = "text-[#F4F4F5]" }) => (
  <section className={cardClassName} data-testid={testId}>
    <p className="text-xs uppercase tracking-[0.2em] text-[#A1A1AA]">{label}</p>
    <p className={`mt-3 text-3xl font-semibold leading-none tracking-tight ${toneClass}`}>{value}</p>
  </section>
);

const DashboardPage = () => {
  const [symbol, setSymbol] = useState("ADANIENT");
  const [query, setQuery] = useState("ADANIENT");
  const [snapshot, setSnapshot] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const fetchSnapshot = async (symbolValue) => {
    setLoading(true);
    setError("");
    try {
      const response = await axios.get(`${API}/dashboard/${encodeURIComponent(symbolValue)}`);
      setSnapshot(response.data);
    } catch (err) {
      setError(err?.response?.data?.detail || "Unable to fetch dashboard data");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchSnapshot(symbol);
    const timer = setInterval(() => fetchSnapshot(symbol), REFRESH_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [symbol]);

  const synthesized = snapshot?.agents?.synthesizer;
  const risk = snapshot?.agents?.risk;
  const sentiment = snapshot?.agents?.sentiment;
  const market = snapshot?.market;
  const cards = useMemo(
    () => [
      {
        testId: "buy-signal-card",
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
        testId: "win-probability-card",
        label: "Win Probability",
        value: synthesized?.win_probability ? `${synthesized.win_probability}%` : "-",
        toneClass: statusTone(synthesized?.win_probability),
      },
      {
        testId: "sentiment-score-card",
        label: "Sentiment Score",
        value: sentiment?.sentiment_score ?? "-",
        toneClass: statusTone(sentiment?.sentiment_score),
      },
      {
        testId: "risk-reward-card",
        label: "Risk:Reward",
        value: risk?.risk_reward_ratio ?? "-",
        toneClass: risk?.risk_reward_ratio >= 1.6 ? "text-[#10B981]" : "text-[#F59E0B]",
      },
    ],
    [risk?.risk_reward_ratio, sentiment?.sentiment_score, synthesized?.signal, synthesized?.win_probability],
  );

  return (
    <div className="relative min-h-screen overflow-x-hidden bg-[#0A0A0B] p-2 font-sans text-[#F4F4F5] md:p-4">
      <div className="pointer-events-none fixed inset-0 opacity-[0.02] [background-image:radial-gradient(#fff_1px,transparent_1px)] [background-size:3px_3px]" />

      <header className="relative z-10 border border-[#27272A] bg-[#121214] p-4">
        <div className="grid gap-3 lg:grid-cols-3 lg:items-center">
          <div className="text-xs uppercase tracking-[0.2em] text-[#A1A1AA]">Kubera Autonomous Market AI</div>
          <form
            className="flex items-center gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              const next = query.trim().toUpperCase();
              if (next) setSymbol(next);
            }}
          >
            <input
              data-testid="stock-search-input"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              className="w-full border border-[#27272A] bg-[#0A0A0B] px-3 py-2 text-sm text-[#F4F4F5] outline-none transition-colors duration-200 focus:border-[#0EA5E9]"
              placeholder="Search symbol (e.g. ADANIENT)"
            />
            <button
              data-testid="search-submit-button"
              type="submit"
              className="border border-[#0EA5E9] bg-[#0EA5E9] px-3 py-2 text-xs font-semibold uppercase tracking-[0.12em] text-[#050505] transition-colors duration-200 hover:bg-[#22D3EE]"
            >
              Load
            </button>
          </form>
          <div className="text-right text-xs text-[#A1A1AA]">
            {loading ? "Refreshing..." : `Live every ${REFRESH_INTERVAL_MS / 1000}s`} · {symbol}
          </div>
        </div>
      </header>

      {error ? (
        <div className="relative z-10 mt-4 border border-[#EF4444] bg-[#2A1113] p-3 text-sm text-[#FCA5A5]">{error}</div>
      ) : null}

      <main className="relative z-10 mt-4 grid grid-cols-1 gap-4 lg:grid-cols-12">
        <section className="col-span-12 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:col-span-8 lg:grid-cols-4">
          {cards.map((card) => (
            <MetricCard key={card.testId} {...card} />
          ))}
        </section>
        <BrainPanel logs={snapshot?.brain_log ?? []} />
        <ChartPanel candles={market?.candles ?? []} />
        <NewsFeed newsItems={market?.news ?? []} />
        <BacktestPanel synthesis={synthesized} />
        <section className={`${cardClassName} col-span-12 lg:col-span-8`}>
          <h3 className="mb-3 text-xs uppercase tracking-[0.2em] text-[#A1A1AA]">Risk Controls</h3>
          <div className="grid gap-3 text-sm text-[#E4E4E7] md:grid-cols-4">
            <p>
              Last Price:
              <span className={`ml-2 font-semibold ${statusTone(market?.current_price - market?.previous_close)}`}>
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
          </div>
        </section>
      </main>
    </div>
  );
};

const App = () => {
  return (
    <div>
      <DashboardPage />
    </div>
  );
};

export default App;
