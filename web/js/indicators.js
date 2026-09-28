// Paper-Trader — Stage 7 technical indicators (pure calculations).
// Every function returns an array aligned index-for-index with the input
// `closes` array: values that cannot be computed yet (insufficient history)
// are `null`, never faked. No DOM access, no dependencies.

/**
 * Simple Moving Average.
 * @param {number[]} closes
 * @param {number} period
 * @returns {(number|null)[]}
 */
function calcSMA(closes, period) {
  const out = new Array(closes.length).fill(null);
  if (period < 1) return out;
  let sum = 0;
  for (let i = 0; i < closes.length; i++) {
    sum += closes[i];
    if (i >= period) sum -= closes[i - period];
    if (i >= period - 1) out[i] = sum / period;
  }
  return out;
}

/**
 * Exponential Moving Average (seeded with the SMA of the first `period`
 * closes, standard practice).
 */
function calcEMA(closes, period) {
  const out = new Array(closes.length).fill(null);
  if (period < 1 || closes.length < period) return out;
  const k = 2 / (period + 1);
  let prev = 0;
  for (let i = 0; i < period; i++) prev += closes[i];
  prev /= period;
  out[period - 1] = prev;
  for (let i = period; i < closes.length; i++) {
    prev = closes[i] * k + prev * (1 - k);
    out[i] = prev;
  }
  return out;
}

/**
 * Wilder's RSI. Returns nulls until index `period` (the first value needs
 * `period + 1` closes).
 */
function calcRSI(closes, period = 14) {
  const out = new Array(closes.length).fill(null);
  if (closes.length <= period) return out;

  let avgGain = 0, avgLoss = 0;
  for (let i = 1; i <= period; i++) {
    const d = closes[i] - closes[i - 1];
    if (d > 0) avgGain += d; else avgLoss -= d;
  }
  avgGain /= period;
  avgLoss /= period;
  out[period] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);

  for (let i = period + 1; i < closes.length; i++) {
    const d = closes[i] - closes[i - 1];
    const gain = d > 0 ? d : 0;
    const loss = d < 0 ? -d : 0;
    avgGain = (avgGain * (period - 1) + gain) / period;
    avgLoss = (avgLoss * (period - 1) + loss) / period;
    out[i] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  }
  return out;
}

/**
 * MACD (12/26/9). Returns three arrays aligned with `closes`; the signal
 * line starts `slow - 1 + signal - 1` bars in. Histogram = macd - signal.
 */
function calcMACD(closes, fast = 12, slow = 26, signal = 9) {
  const emaFast = calcEMA(closes, fast);
  const emaSlow = calcEMA(closes, slow);

  const macdLine = emaFast.map((f, i) =>
    f === null || emaSlow[i] === null ? null : f - emaSlow[i]);

  // Signal EMA over the non-null MACD values, then re-align to candle indexes.
  const firstIdx = macdLine.findIndex((v) => v !== null);
  const signalLine = new Array(closes.length).fill(null);
  const histogram = new Array(closes.length).fill(null);
  if (firstIdx !== -1) {
    const compact = macdLine.slice(firstIdx);
    const sig = calcEMA(compact, signal);
    for (let i = 0; i < sig.length; i++) {
      if (sig[i] !== null) {
        signalLine[firstIdx + i] = sig[i];
        histogram[firstIdx + i] = macdLine[firstIdx + i] - sig[i];
      }
    }
  }
  return { macdLine, signalLine, histogram };
}

/**
 * Compute every requested indicator for a candle series.
 * @param {Array<{close:number}>} candles
 * @param {Object} active  e.g. { sma20:true, ema12:true, rsi14:true, macd:true }
 * @returns {Object} keyed values arrays (plus `macd` object when active)
 */
function computeIndicators(candles, active) {
  const closes = candles.map((c) => c.close);
  const out = {};
  if (active.sma20) out.sma20 = calcSMA(closes, 20);
  if (active.sma50) out.sma50 = calcSMA(closes, 50);
  if (active.sma200) out.sma200 = calcSMA(closes, 200);
  if (active.ema12) out.ema12 = calcEMA(closes, 12);
  if (active.ema26) out.ema26 = calcEMA(closes, 26);
  if (active.rsi14) out.rsi14 = calcRSI(closes, 14);
  if (active.macd) out.macd = calcMACD(closes);
  return out;
}
