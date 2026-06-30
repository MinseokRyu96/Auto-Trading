const state = {
  data: null,
  refreshedAt: null,
  refreshInFlight: false,
  autoRefreshTimer: null,
};

const AUTO_REFRESH_MS = 5 * 60 * 1000;

const money = new Intl.NumberFormat("ko-KR", {
  maximumFractionDigits: 0,
});

const number = new Intl.NumberFormat("ko-KR", {
  maximumFractionDigits: 2,
});

function formatMoney(value) {
  const num = Number(value || 0);
  return `${money.format(num)}원`;
}

function formatNumber(value) {
  return number.format(Number(value || 0));
}

function signedClass(value) {
  return Number(value || 0) >= 0 ? "pos" : "neg";
}

function timeOnly(value) {
  if (!value) return "";
  if (value.includes("T")) return value.split("T")[1].slice(0, 8);
  if (value.includes(" ")) return value.split(" ")[1].slice(0, 8);
  return value.slice(0, 8);
}

function localTimeOnly(value) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

async function loadDashboard(options = {}) {
  const dateInput = document.querySelector("#dateInput");
  const refresh = Boolean(options.refresh);
  const endpoint = refresh ? "/api/refresh" : "/api/overview";
  const response = await fetch(`${endpoint}?date=${encodeURIComponent(dateInput.value)}`, {
    method: refresh ? "POST" : "GET",
  });
  if (!response.ok) {
    const errorPayload = await response.json().catch(() => ({}));
    throw new Error(errorPayload.error || "대시보드 데이터를 갱신하지 못했습니다.");
  }
  state.data = await response.json();
  state.refreshedAt = new Date();
  render();
}

async function refreshDashboard(options = {}) {
  if (state.refreshInFlight) return;
  state.refreshInFlight = true;
  const silent = Boolean(options.silent);
  const button = document.querySelector("#refreshButton");
  const previousText = button.textContent;
  button.disabled = true;
  button.textContent = silent ? "Auto Refresh" : "Refreshing";
  try {
    await loadDashboard({ refresh: true });
  } catch (error) {
    console.error(error);
    if (!silent) alert(error.message || "대시보드 데이터를 갱신하지 못했습니다.");
  } finally {
    state.refreshInFlight = false;
    button.disabled = false;
    button.textContent = previousText;
    scheduleAutoRefresh();
  }
}

function scheduleAutoRefresh() {
  if (state.autoRefreshTimer) {
    clearTimeout(state.autoRefreshTimer);
  }
  state.autoRefreshTimer = setTimeout(() => {
    refreshDashboard({ silent: true });
  }, AUTO_REFRESH_MS);
}

function render() {
  const { summary, trades, orders, holdings, rankings, series, quotes, account, strategy, learning, meta, date } = state.data;
  setText("#realizedPnl", formatMoney(summary.realized_pnl));
  setText("#winRate", `${summary.realized_estimated ? "추정 " : ""}승률 ${formatNumber(summary.win_rate)}%`);
  setText("#tradeCount", `${summary.realized_estimated ? "추정 체결" : "체결"} ${summary.trade_count}건`);
  setText("#tradeDateLabel", date);
  setText("#orderQueueCount", `${((strategy && strategy.orders) || []).length}건`);
  renderMeta(meta || {});

  colorByValue("#realizedPnl", summary.realized_pnl);
  renderAccount(account);
  renderRiskState((strategy && strategy.risk) || null);
  renderStrategySignals((strategy && strategy.signals) || []);
  renderRebalanceOrders((strategy && strategy.orders) || []);
  renderLearning(learning || {});

  renderTrades(trades);
  renderOrders(orders);
  renderHoldings(holdings);
  renderQuotes(quotes);
  renderRank("#volumeRank", rankings.volume_rank, "trade_value");
  renderRank("#marketCapRank", rankings.market_cap, "market_cap");
  drawLineChart("pnlChart", series.pnl, "trade_date", "realized_pnl", "#04756f", { includeZero: true });
  drawLineChart("equityChart", series.equity, "collected_at", "equity", "#245b9e", { includeZero: false });
}

function formatPercent(value) {
  if (value === null || value === undefined || value === "") return "-";
  return `${formatNumber(Number(value) * 100)}%`;
}

function renderMeta(meta) {
  const liveConfigured = Boolean(meta.live_configured);
  const tradingEnabled = Boolean(meta.trading_enabled);
  const liveEnabled = Boolean(meta.live_trading);
  const liveCard = document.querySelector("#liveTradingCard");
  const liveLabel = document.querySelector("#liveLockLabel");
  const toggleButton = document.querySelector("#tradingToggleButton");
  setText("#envLabel", `KIS ${String(meta.env || "-").toUpperCase()}`);
  setText("#liveLockLabel", liveEnabled ? "매매 ON" : "매매 OFF");
  setText(
    "#liveModeNote",
    liveConfigured
      ? tradingEnabled
        ? "자동 주문 제출 가능"
        : "대시보드에서 주문 중지됨"
      : "환경설정에서 실주문 차단됨"
  );
  if (liveCard) {
    liveCard.classList.toggle("is-enabled", liveEnabled);
    liveCard.classList.toggle("is-disabled", !liveEnabled);
  }
  if (liveLabel) {
    liveLabel.setAttribute("aria-label", `Live Trading ${liveEnabled ? "ON" : "OFF"}`);
  }
  if (toggleButton) {
    toggleButton.disabled = !liveConfigured;
    toggleButton.textContent = tradingEnabled ? "매매 끄기" : "매매 켜기";
    toggleButton.classList.toggle("danger", tradingEnabled);
  }
  setText("#syncTime", state.refreshedAt ? localTimeOnly(state.refreshedAt) : "-");
}

async function setTradingEnabled(enabled) {
  if (enabled && !confirm("자동매매 주문 제출을 다시 켤까요?")) return;
  if (!enabled && !confirm("자동매매 주문 제출을 멈출까요? 시세/전략 갱신은 계속됩니다.")) return;
  const button = document.querySelector("#tradingToggleButton");
  button.disabled = true;
  try {
    const response = await fetch("/api/trading-control", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.error || "매매 상태를 변경하지 못했습니다.");
    }
    await loadDashboard();
  } catch (error) {
    console.error(error);
    alert(error.message || "매매 상태를 변경하지 못했습니다.");
  } finally {
    button.disabled = false;
  }
}

function renderAccount(account) {
  if (!account) {
    setText("#accountTotal", "0원");
    setText("#accountCash", "0원");
    setText("#accountPnl", "평가손익 0원");
    setText("#accountUpdated", "계좌 스냅샷 없음");
    setText("#accountToday", "당일 매수/매도 0 / 0");
    return;
  }
  setText("#accountTotal", formatMoney(account.total_eval_amount || account.net_asset_amount));
  setText("#accountCash", formatMoney(account.cash_amount));
  setText("#accountPnl", `평가손익 ${formatMoney(account.valuation_pnl)}`);
  setText("#accountUpdated", `업데이트 ${localTimeOnly(account.collected_at)}`);
  setText("#accountToday", `당일 매수/매도 ${formatMoney(account.today_buy_amount)} / ${formatMoney(account.today_sell_amount)}`);
  colorByValue("#accountPnl", account.valuation_pnl);
}

function renderRiskState(risk) {
  if (!risk) {
    setText("#riskRegime", "시장 리스크 미계산");
    setText("#opsRiskRegime", "-");
    setText("#opsExposure", "0%");
    setText("#opsRiskReason", "전략을 실행하면 하락장 방어 상태가 표시됩니다.");
    setText("#riskRegimeLabel", "liquidity_momentum_v1");
    setText("#riskMetrics", "전략을 실행하면 하락장 방어 상태가 표시됩니다.");
    return;
  }
  const regimeText = {
    risk_on: "정상",
    high_volatility: "고변동성",
    risk_off: "위험 축소",
    crash: "급락 방어",
    unknown: "데이터 부족",
  }[risk.regime] || risk.regime;
  setText("#riskRegime", `${regimeText} · 목표노출 ${formatNumber(Number(risk.exposure_multiplier || 0) * 100)}%`);
  setText("#opsRiskRegime", regimeText);
  setText("#opsExposure", `${formatNumber(Number(risk.exposure_multiplier || 0) * 100)}%`);
  setText("#opsRiskReason", risk.reason || "");
  setText("#riskRegimeLabel", `기준일 ${risk.as_of_date || ""}`);
  setText(
    "#riskMetrics",
    `20일 ${formatNumber(Number(risk.market_return_20d || 0) * 100)}% · 60일 낙폭 ${formatNumber(Number(risk.market_drawdown_60d || 0) * 100)}% · 변동성 ${formatNumber(Number(risk.market_volatility_20d || 0) * 100)}% · ${risk.reason || ""}`
  );
}

function renderStrategySignals(rows) {
  const body = document.querySelector("#strategySignalsBody");
  if (!rows.length) {
    body.innerHTML = `<tr><td class="empty" colspan="5">아직 전략 실행 결과가 없습니다.</td></tr>`;
    return;
  }
  body.innerHTML = rows.map((row) => `
    <tr>
      <td><strong>${row.name || row.symbol}</strong> <span class="muted-code">${row.symbol}</span></td>
      <td class="num">${formatNumber(Number(row.score || 0) * 100)}</td>
      <td class="num">${formatNumber(Number(row.target_weight || 0) * 100)}%</td>
      <td><span class="state-pill state-${row.action || "watch"}">${actionLabel(row.action)}</span></td>
      <td>${row.reason || ""}</td>
    </tr>
  `).join("");
}

function renderRebalanceOrders(rows) {
  const body = document.querySelector("#rebalanceOrdersBody");
  if (!rows.length) {
    body.innerHTML = `<tr><td class="empty" colspan="5">주문 미리보기가 없습니다.</td></tr>`;
    return;
  }
  body.innerHTML = rows.map((row) => `
    <tr>
      <td><strong>${row.name || row.symbol}</strong> <span class="muted-code">${row.symbol}</span></td>
      <td><span class="badge ${row.side}">${row.side === "buy" ? "매수" : "매도"}</span></td>
      <td class="num">${formatNumber(row.quantity)}</td>
      <td class="num">${formatMoney(row.order_value)}</td>
      <td>${row.reason || ""}</td>
    </tr>
  `).join("");
}

function renderLearning(learning) {
  const review = learning.review;
  const suggestions = learning.suggestions || [];
  if (!review) {
    setText("#learningDate", "리뷰 없음");
    setText("#learningQuality", "-");
    setText("#learningSummary", "장 마감 리뷰를 실행하면 오늘의 성과와 개선 판단이 표시됩니다.");
  } else {
    setText("#learningDate", review.trade_date || "");
    setText("#learningQuality", formatNumber(review.quality_score));
    setText("#learningSummary", review.summary || "");
  }

  const body = document.querySelector("#learningSuggestionsBody");
  if (!suggestions.length) {
    body.innerHTML = `<tr><td class="empty" colspan="4">추천 파라미터가 없습니다.</td></tr>`;
    return;
  }
  body.innerHTML = suggestions.map((row) => `
    <tr>
      <td><strong>${parameterLabel(row.parameter)}</strong></td>
      <td class="num">${parameterValue(row.current_value)}</td>
      <td class="num">${parameterValue(row.suggested_value)}</td>
      <td>${row.reason || ""} <span class="muted-code">신뢰도 ${formatPercent(row.confidence)}</span></td>
    </tr>
  `).join("");
}

function parameterLabel(parameter) {
  const labels = {
    gross_exposure: "총 노출",
    max_positions: "최대 종목수",
    max_annual_volatility: "변동성 한도",
    risk_off_multiplier: "위험축소 배율",
    crash_multiplier: "급락장 배율",
    no_change: "유지",
  };
  return labels[parameter] || parameter;
}

function parameterValue(value) {
  if (value === null || value === undefined || value === "") return "-";
  const num = Number(value);
  if (!Number.isFinite(num)) return String(value);
  if (Math.abs(num) <= 1) return formatPercent(num);
  return formatNumber(num);
}

function actionLabel(action) {
  if (action === "target") return "편입";
  if (action === "exit") return "축소";
  return "관찰";
}

function renderTrades(rows) {
  const body = document.querySelector("#tradesBody");
  if (!rows.length) {
    body.innerHTML = `<tr><td class="empty" colspan="5">선택한 날짜의 체결 내역이 없습니다.</td></tr>`;
    return;
  }
  body.innerHTML = rows.map((row) => `
    <tr>
      <td>${timeOnly(row.execution_time)}</td>
      <td><strong>${row.symbol}</strong> ${row.name || ""}</td>
      <td><span class="badge ${row.side}">${row.side === "buy" ? "매수" : "매도"}</span></td>
      <td class="num">${formatMoney(row.amount)}</td>
      <td class="num ${signedClass(row.realized_pnl)}">${formatMoney(row.realized_pnl)}</td>
    </tr>
  `).join("");
}

function renderOrders(rows) {
  const body = document.querySelector("#ordersBody");
  if (!rows.length) {
    body.innerHTML = `<tr><td class="empty" colspan="5">주문 이벤트가 없습니다.</td></tr>`;
    return;
  }
  body.innerHTML = rows.map((row) => `
    <tr>
      <td>${timeOnly(row.event_time)}</td>
      <td><strong>${row.symbol}</strong> ${row.name || ""}</td>
      <td>${row.status}</td>
      <td class="num">${formatNumber(row.quantity)}</td>
      <td>${row.reason || ""}</td>
    </tr>
  `).join("");
}

function renderHoldings(rows) {
  const body = document.querySelector("#holdingsBody");
  if (!rows.length) {
    body.innerHTML = `<tr><td class="empty" colspan="4">잔고 스냅샷이 없습니다.</td></tr>`;
    return;
  }
  body.innerHTML = rows.map((row) => `
    <tr>
      <td><strong>${row.symbol}</strong> ${row.name || ""}</td>
      <td class="num">${formatNumber(row.quantity)}</td>
      <td class="num">${formatMoney(row.eval_amount)}</td>
      <td class="num ${signedClass(row.pnl)}">${formatMoney(row.pnl)}</td>
    </tr>
  `).join("");
}

function renderQuotes(rows) {
  const body = document.querySelector("#quotesBody");
  if (!rows.length) {
    body.innerHTML = `<tr><td class="empty" colspan="4">시세 스냅샷이 없습니다.</td></tr>`;
    return;
  }
  body.innerHTML = rows.map((row) => `
    <tr>
      <td><strong>${row.name || row.symbol}</strong> <span class="muted-code">${row.symbol}</span></td>
      <td class="num">${formatMoney(row.last_price)}</td>
      <td class="num ${signedClass(row.change_rate)}">${formatNumber(row.change_rate)}%</td>
      <td class="num">${formatNumber(row.accumulated_volume)}</td>
    </tr>
  `).join("");
}

function renderRank(selector, rows, amountKey) {
  const list = document.querySelector(selector);
  if (!rows.length) {
    list.innerHTML = `<li class="empty">랭킹 스냅샷이 없습니다.</li>`;
    return;
  }
  list.innerHTML = rows.map((row, index) => `
    <li>
      <span class="rank-no">${row.rank_no || index + 1}</span>
      <span class="rank-main">
        <strong class="rank-name">${row.name || row.symbol || ""}</strong>
        <span class="rank-symbol">${row.symbol || ""}</span>
      </span>
      <span class="rank-value">${formatMoney(row[amountKey] || row.trade_value || row.price)}</span>
    </li>
  `).join("");
}

function drawLineChart(canvasId, rows, labelKey, valueKey, color, options = {}) {
  const canvas = document.getElementById(canvasId);
  const context = canvas.getContext("2d");
  const rect = canvas.getBoundingClientRect();
  const logicalHeight = Number(canvas.dataset.chartHeight || 220);
  const logicalWidth = Math.max(320, Math.floor(rect.width));
  canvas.style.height = `${logicalHeight}px`;
  canvas.width = logicalWidth;
  canvas.height = logicalHeight;

  const width = logicalWidth;
  const height = logicalHeight;
  const plot = {
    left: 58,
    right: width - 18,
    top: 24,
    bottom: height - 42,
  };
  context.clearRect(0, 0, width, height);
  context.lineWidth = 1;

  if (!rows.length) {
    context.fillStyle = "#667085";
    context.font = "13px system-ui";
    context.fillText("표시할 데이터가 없습니다.", 52, height / 2);
    return;
  }

  const cleanRows = rows
    .map((row) => ({ ...row, __value: Number(row[valueKey]) }))
    .filter((row) => Number.isFinite(row.__value));
  if (!cleanRows.length) {
    context.fillStyle = "#667085";
    context.font = "13px system-ui";
    context.fillText("표시할 데이터가 없습니다.", 52, height / 2);
    return;
  }

  const values = cleanRows.map((row) => row.__value);
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (options.includeZero) {
    min = Math.min(min, 0);
    max = Math.max(max, 0);
  }
  const rawSpan = max - min;
  const padding = rawSpan > 0 ? rawSpan * 0.12 : Math.max(Math.abs(max) * 0.002, 1000);
  min -= padding;
  max += padding;
  const span = max - min || 1;

  drawChartGrid(context, plot, width, height, min, max);

  context.strokeStyle = color;
  context.lineWidth = 2.5;
  context.beginPath();
  cleanRows.forEach((row, index) => {
    const x = plot.left + ((plot.right - plot.left) * index) / Math.max(cleanRows.length - 1, 1);
    const y = plot.bottom - ((row.__value - min) / span) * (plot.bottom - plot.top);
    if (index === 0) context.moveTo(x, y);
    else context.lineTo(x, y);
  });
  context.stroke();

  const gradient = context.createLinearGradient(0, plot.top, 0, plot.bottom);
  gradient.addColorStop(0, `${color}26`);
  gradient.addColorStop(1, `${color}00`);
  context.lineTo(plot.right, plot.bottom);
  context.lineTo(plot.left, plot.bottom);
  context.closePath();
  context.fillStyle = gradient;
  context.fill();

  context.fillStyle = color;
  cleanRows.forEach((row, index) => {
    const x = plot.left + ((plot.right - plot.left) * index) / Math.max(cleanRows.length - 1, 1);
    const y = plot.bottom - ((row.__value - min) / span) * (plot.bottom - plot.top);
    context.beginPath();
    context.arc(x, y, index === cleanRows.length - 1 ? 4 : 2.5, 0, Math.PI * 2);
    context.fill();
  });

  const latest = cleanRows[cleanRows.length - 1];
  context.fillStyle = "#101828";
  context.font = "600 13px system-ui";
  context.fillText(formatMoney(latest.__value), plot.left, 14);
  context.fillStyle = "#667085";
  context.font = "12px system-ui";
  const first = shortChartLabel(cleanRows[0][labelKey]);
  const last = shortChartLabel(latest[labelKey]);
  context.fillText(first, plot.left, height - 8);
  context.textAlign = "right";
  context.fillText(last, plot.right, height - 8);
  context.textAlign = "left";
}

function drawChartGrid(context, plot, width, height, min, max) {
  context.strokeStyle = "#e6ebef";
  context.fillStyle = "#667085";
  context.font = "11px system-ui";
  context.textAlign = "right";
  for (let index = 0; index < 4; index += 1) {
    const ratio = index / 3;
    const y = plot.top + (plot.bottom - plot.top) * ratio;
    const value = max - (max - min) * ratio;
    context.beginPath();
    context.moveTo(plot.left, y);
    context.lineTo(plot.right, y);
    context.stroke();
    context.fillText(compactMoney(value), plot.left - 8, y + 4);
  }
  context.strokeStyle = "#cfd8df";
  context.beginPath();
  context.moveTo(plot.left, plot.top);
  context.lineTo(plot.left, plot.bottom);
  context.lineTo(plot.right, plot.bottom);
  context.stroke();
  context.textAlign = "left";
}

function compactMoney(value) {
  const num = Number(value || 0);
  if (Math.abs(num) >= 100000000) return `${formatNumber(num / 100000000)}억`;
  if (Math.abs(num) >= 10000) return `${formatNumber(num / 10000)}만`;
  return money.format(num);
}

function shortChartLabel(value) {
  const text = String(value || "");
  if (text.includes("T")) return text.split("T")[1].slice(0, 5);
  return text.slice(5, 10) || text;
}

function setText(selector, value) {
  document.querySelector(selector).textContent = value;
}

function colorByValue(selector, value) {
  const element = document.querySelector(selector);
  element.classList.remove("pos", "neg");
  element.classList.add(signedClass(value));
}

function setActiveNav(sectionId) {
  document.querySelectorAll("nav a").forEach((link) => {
    link.classList.toggle("active", link.getAttribute("href") === `#${sectionId}`);
  });
}

function updateActiveNavFromScroll() {
  const links = [...document.querySelectorAll("nav a[href^='#']")];
  const sectionIds = links.map((link) => link.getAttribute("href").slice(1));
  let activeId = sectionIds[0];
  for (const sectionId of sectionIds) {
    const section = document.getElementById(sectionId);
    if (!section) continue;
    const top = section.getBoundingClientRect().top;
    if (top <= 140) activeId = sectionId;
  }
  setActiveNav(activeId);
}

function initNav() {
  document.querySelectorAll("nav a[href^='#']").forEach((link) => {
    link.addEventListener("click", () => {
      const sectionId = link.getAttribute("href").slice(1);
      setActiveNav(sectionId);
    });
  });
  window.addEventListener("hashchange", () => {
    const sectionId = window.location.hash.replace("#", "");
    if (sectionId) setActiveNav(sectionId);
  });
  window.addEventListener("scroll", updateActiveNavFromScroll, { passive: true });
  updateActiveNavFromScroll();
}

function todayIso() {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

document.querySelector("#dateInput").value = todayIso();
document.querySelector("#refreshButton").addEventListener("click", refreshDashboard);
document.querySelector("#tradingToggleButton").addEventListener("click", () => {
  const enabled = !Boolean(state.data && state.data.meta && state.data.meta.trading_enabled);
  setTradingEnabled(enabled);
});
window.addEventListener("resize", () => {
  if (state.data) render();
  updateActiveNavFromScroll();
});
initNav();
loadDashboard().catch((error) => {
  console.error(error);
  alert("대시보드 데이터를 불러오지 못했습니다.");
}).finally(() => {
  scheduleAutoRefresh();
});
