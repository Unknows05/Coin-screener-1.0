# 🔬 REFACTOR ANALISA — Coin Screener Bot

## DATA SAAT INI (8 hari, 5716 signals, 30 coins)

### WIN RATE BREAKDOWN
| Regime    | Signal | WR     | Trades | Verdict           |
|-----------|--------|--------|--------|-------------------|
| SIDEWAYS  | LONG   | 35.2%  | 693    | ❌ JANGAN TRADE   |
| SIDEWAYS  | SHORT  | 62.6%  | 3049   | ✅ STRONGEST      |
| BULL      | LONG   | 58.7%  | 976    | ✅ GOOD           |
| BULL      | SHORT  | 66.3%  | 368    | ✅ EXCELLENT      |
| BEAR      | LONG   | 50.0%  | 2      | ⚠️ data terlalu sedikit |
| BEAR      | SHORT  | 44.8%  | 516    | ❌ JANGAN TRADE   |
| HIGH_VOL  | LONG   | 70.8%  | 65     | ✅ tapi sample kecil |
| HIGH_VOL  | SHORT  | 36.8%  | 57     | ❌ JANGAN TRADE   |

### PnL ANALYSIS
| Signal | Avg Win | Avg Loss | Net Expectancy |
|--------|---------|----------|----------------|
| LONG   | +5.60%  | -1.58%   | +0.55% per trade (barely positive) |
| SHORT  | +2.46%  | -1.60%   | +0.54% per trade (good)       |

### SL/TP METRICS
- Average SL distance: ~1.0% (TERLALU KETAT)
- Average TP distance: ~2.0%
- R:R ratio: 2:1 (bagus, tapi SL terlalu rapat = banyak false stop-out)

---

## 🏗️ REFACTOR PRIORITAS

### P0 — CRITICAL (Blocker sekarang)

#### 1. Matikan Overfitting Protector (sesaat)
Overfitting protector memblokir 26/30 coins padahal BUKAN overfitting,
tapi regime shift. Train WR 27% itu bukan overfitting — itu model yang jelek.

**Fix:** Lower threshold atau disable sementara sampah data cukup.

#### 2. LONG di SIDEWAYS harus di-block
35.2% WR itu lebih jelek dari random coin flip. Setiap long side 
adalah -205 expected trades loss.

**Fix:** Tambah rule: `if regime == SIDEWAYS and signal == LONG: -> WAIT`

#### 3. SHORT di BEAR harus di-block  
44.8% WR di BEAR — ini kontra-intuitif. Pasar turun tapi SHORT loss? 
Kemungkinan karena volatilitas berlebihan dan SL ketat.

**Fix:** Tambah rule: `if regime == BEAR: -> reduce position/avoid SHORT`

---

### P1 — HIGH (Target 70%+ WR)

#### 4. SL Terlalu Ketat (1.0% average)
SL 1% di crypto = hampir pasti kena stop-out. Volatilitas 15m 
bisa 0.5-2% dalam hitungan menit. SL harus berbasis ATR, bukan persen tetap.

**Fix:** SL = entry - (2.5 * ATR_14), TP = entry + (3-5 * ATR_14)

#### 5. Tambah Coin Selection (500+ perpetual)
Sekarang cuma 30 coin. Binance Futures punya 500+.
Lebih banyak coin = lebih banyak peluang = WR naik karena pilih yang bagus saja.

**Fix:** Auto-discover top 200 coins by volume, filter yang
ATR > threshold dan spread < threshold.

#### 6. Score Threshold Terlalu Tinggi
Current scan: avg score 29.8, threshold SIDEWAYS = 60.
Artinya hampir tidak ada yang lolos di SIDEWAYS.

**Fix:** Score threshold harus adaptive:
- SIDEWAYS: 40 (khusus SHORT saja)
- BULL: 45 (LONG prefer)
- BEAR: 55 (hanya yang paling kuat)

---

### P2 — MEDIUM (Target 75%+ WR)

#### 7. Time-of-Day Filter
Crypto punya pola waktu yang kuat:
- Asia session (00-08 UTC): Volatilitas rendah, WR rendah
- London session (08-16 UTC): WR terbaik
- NY session (16-00 UTC): Volatilitas tinggi

**Fix:** Tambah session filter, prefer London overlap.

#### 8. Confidence Score yang Lebih Akurat
Sekarang confidence tidak predictive (70+ conf = 56.3% WR,
60-69 conf = 62% WR — confidence lebih rendah WIN LEBIH TINGGI!)

**Fix:** Rebuild confidence score berbasis:
- Regime-specific WR (bukan global)
- ATR-normalized momentum
- Volume profile vs average

#### 9. Adaptive Learning Rate
RL weights update terlalu lambat (8 hari data).
Perlu exponential decay agar bot belajar lebih cepat dari regime shift.

**Fix:** Implement EMA-based weight update, decay factor = 0.95

---

### P3 — NICE TO HAVE (Target 80%+ WR)

#### 10. Multi-timeframe Confluence
Sekarang bot pakai 15m/1h/4h tapi hanya as weighted score.
Harus: signal hanya IF semua timeframe searah.

#### 11. Order Flow / Volume Delta
Perpetual futures punya data open interest, funding rate, 
liquidation levels. Tambah ini untuk konfirmasi entry.

#### 12. Dynamic Take Profit
Sekarang TP fixed. Perlu trailing stop atau partial TP:
- TP1: 1.5x SL (take 50% position)
- TP2: 3x SL (take 30% position)  
- Trailing stop: 1.5x ATR untuk sisa 20%

---

## 📐 MATH FRAMEWORK UNTUK 80% WR

### Kelly Criterion untuk Position Sizing
```
f* = (bp - q) / b
dimana:
  f* = fraction of capital to risk
  b = R:R ratio = avg_win / avg_loss
  p = win rate
  q = 1 - p

SHORT di SIDEWAYS:
  b = 2.46 / 1.60 = 1.54
  p = 0.626
  f* = (1.54 * 0.626 - 0.374) / 1.54 = 0.385 = 38.5% Kelly
  
LONG di BULL:
  b = 5.60 / 1.58 = 3.54
  p = 0.587
  f* = (3.54 * 0.587 - 0.413) / 3.54 = 0.470 = 47.0% Kelly

Half-Kelly untuk safety = ~19-24% yang optimal.
Tanpa capital management, fokus ke ENTRY QUALITY.
```

### Expected WR Target Calculation
```
WR_target = 80% memerlukan:

1. Filter regime+signal yang WR > 65%:
   - SIDEWAYS + SHORT = 62.6% (perlu +2.4%)
   - BULL + SHORT = 66.3% ✅
   - BULL + LONG = 58.7% (perlu +21.3%)
   - HIGH_VOL + LONG = 70.8% ✅

2. Dengan ATR-based SL (estimated +5-8% WR karena 
   mengurangi false stop-out):
   - SIDEWAYS + SHORT ≈ 70-72%
   - BULL + SHORT ≈ 73-75%
   - BULL + LONG ≈ 64-66%

3. Dengan time filter (estimated +3-5% WR):
   - SIDEWAYS + SHORT ≈ 73-77%
   - BULL + SHORT ≈ 76-80% ✅ TARGET!

4. Dengan coin selection top performers (est. +3-5%):
   - SIDEWAYS + SHORT ≈ 76-82% ✅ TARGET!
```

**Kesimpulan:** 80% WR achievable dengan:
1. Block LONG di SIDEWAYS (naik WR langsung)
2. ATR-based SL (kurangi false stop-out)
3. Time filter (London session)
4. Coin selection (top 5-10 per regime)
5. Multi-confluence entry requirement