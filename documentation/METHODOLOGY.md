# 🔬 Methodology & Technical Reference

**Deep dive into how everything works**

---

## 📚 Table of Contents

1. [Demand Forecasting](#demand-forecasting)
2. [Inventory Optimization](#inventory-optimization)
3. [ABC Classification](#abc-classification)
4. [Safety Stock Calculation](#safety-stock-calculation)
5. [Performance Metrics](#performance-metrics)

---

## 🔮 Demand Forecasting

### **Overview**

Demand forecasting predicts future sales based on historical patterns. We use 4 methods and select the best.

### **Method 1: Moving Average**

**Formula:**
```
Forecast = Average of Last N periods
F(t) = (D(t-1) + D(t-2) + ... + D(t-n)) / n
```

**Example:**
```
Last 3 months: 100, 105, 110 units
Forecast = (100 + 105 + 110) / 3 = 105 units
```

**When to use:**
- ✅ Stable demand
- ✅ No strong seasonality
- ✅ Limited historical data

**Pros:** Simple, responsive
**Cons:** Ignores trends and seasonality

---

### **Method 2: Exponential Smoothing (ETS)**

**Formula:**
```
F(t+1) = α × D(t) + (1-α) × F(t)

Where:
- α (alpha) = smoothing factor (0.1 to 0.3)
- D(t) = actual demand at time t
- F(t) = forecast at time t
```

**Example:**
```
Last forecast: 100 units
Actual demand: 110 units
α = 0.2
New forecast = 0.2 × 110 + 0.8 × 100 = 102 units
```

**When to use:**
- ✅ Moderate trend
- ✅ Light seasonality
- ✅ Need responsive forecast

**Pros:** Adapts to changes, simple
**Cons:** Requires parameter tuning

---

### **Method 3: ARIMA (AutoRegressive Integrated Moving Average)**

**Formula:**
```
ARIMA(p,d,q)
- p = autoregressive terms
- d = differencing (for stationarity)
- q = moving average terms

Y(t) = c + φ₁Y(t-1) + ... + φₚY(t-p) + θ₁ε(t-1) + ... + θqε(t-q)
```

**When to use:**
- ✅ Complex patterns
- ✅ Multiple trends
- ✅ Seasonal patterns
- ✅ 2+ years of data

**Pros:** Captures complex patterns, statistically robust
**Cons:** Requires more data, slower calculation

---

### **Method 4: Prophet (Facebook)**

**Formula:**
```
y(t) = g(t) + s(t) + h(t) + ε(t)

Where:
- g(t) = trend (piecewise linear/logistic)
- s(t) = seasonality (Fourier series)
- h(t) = holidays effect
- ε(t) = error term
```

**When to use:**
- ✅ Strong seasonality
- ✅ Holiday effects matter
- ✅ Multiple seasonalities
- ✅ Business applications

**Pros:** Robust, handles holidays, very practical
**Cons:** Less statistically pure, requires tuning

---

### **Method Selection Logic**

```
IF historical_data < 3 months:
    Use: Moving Average
ELIF has_strong_seasonality:
    Use: Prophet
ELIF has_trend AND complex_patterns:
    Use: ARIMA
ELSE:
    Use: Exponential Smoothing

FINAL: Select method with lowest MAE (Mean Absolute Error)
```

---

### **Accuracy Metrics**

#### **MAE (Mean Absolute Error)**
```
MAE = Σ|Actual - Forecast| / n

Example:
Actual: [100, 110, 105]
Forecast: [98, 112, 104]
Error: [2, 2, 1]
MAE = (2 + 2 + 1) / 3 = 1.67 units
```

#### **RMSE (Root Mean Squared Error)**
```
RMSE = √(Σ(Actual - Forecast)² / n)

Example:
Errors: [2, 2, 1]
RMSE = √((4 + 4 + 1) / 3) = 2.16 units
```

#### **MAPE (Mean Absolute Percentage Error)**
```
MAPE = (Σ|Actual - Forecast| / Σ|Actual|) × 100%

Example:
Actual total: 315 units
Errors total: 5 units
MAPE = (5 / 315) × 100% = 1.59%
```

---

## 📦 Inventory Optimization

### **EOQ (Economic Order Quantity)**

**Formula:**
```
EOQ = √(2 × D × S / H)

Where:
- D = Annual demand (units)
- S = Order cost per unit
- H = Annual holding cost per unit
```

**Derivation:**
```
Total Cost = (D/Q) × S + (Q/2) × H

Minimize by taking derivative and setting to 0:
d(TC)/dQ = -D×S/Q² + H/2 = 0
Q² = 2×D×S/H
Q = √(2×D×S/H)
```

**Example:**
```
Annual demand: 12,000 units
Order cost: $50 per order
Holding cost: $2 per unit per year

EOQ = √(2 × 12,000 × 50 / 2)
    = √(600,000)
    = 774.6 ≈ 775 units

Recommendation: Order 775 units per cycle
```

**Cost Savings:**
```
Without optimization (order 1,000 units):
- Ordering cost: (12,000/1,000) × $50 = $600
- Holding cost: (1,000/2) × $2 = $1,000
- Total: $1,600

With EOQ (order 775 units):
- Ordering cost: (12,000/775) × $50 = $775
- Holding cost: (775/2) × $2 = $775
- Total: $1,550

Savings: $50/year (3.1%)
```

---

### **ROP (Reorder Point)**

**Formula:**
```
ROP = (D_avg × LT) + SS

Where:
- D_avg = Average daily demand
- LT = Lead time (days)
- SS = Safety stock
```

**Example:**
```
Average daily demand: 100 units
Lead time: 7 days
Safety stock: 150 units

ROP = (100 × 7) + 150 = 850 units

Action: When inventory reaches 850 units, place new order
```

**Why it works:**
```
Timeline:
Day 0: Inventory = 1,000 units (place order)
Day 0-7: Inventory decreases by 100/day
Day 7: Inventory reaches 250 units (safety stock)
Day 7: New order arrives
Day 7+: Inventory replenished
```

---

### **Safety Stock**

**Formula (Service Level method):**
```
SS = Z × σ_d × √LT

Where:
- Z = Z-score for desired service level
- σ_d = Standard deviation of daily demand
- LT = Lead time (days)
```

**Service Level Z-Scores:**
```
Service Level | Z-Score | Stockout Risk
50%           | 0.0     | 50%
75%           | 0.67    | 25%
90%           | 1.28    | 10%
95%           | 1.65    | 5%
99%           | 2.33    | 1%
```

**Example:**
```
Demand std dev: 20 units/day
Lead time: 7 days
Target service level: 95% (Z = 1.65)

SS = 1.65 × 20 × √7
   = 1.65 × 20 × 2.646
   = 87.4 ≈ 88 units

Interpretation:
- Keep 88 units safety stock
- This covers 95% of demand scenarios
- Stockout risk = 5%
```

---

## 📊 ABC Classification

**Pareto Principle: 80% of effects come from 20% of causes**

### **Formula**

```
1. Calculate: Sales Value = Unit Sales × Unit Price
2. Sort: Highest to lowest sales value
3. Cumulative %: Calculate cumulative sales %
4. Classify:
   - A: Top 20% (cumulative 80% value)
   - B: Next 30% (cumulative 15% value)
   - C: Bottom 50% (cumulative 5% value)
```

### **Example**

```
SKU    | Units | Price | Sales | Cum%  | Class
-------|-------|-------|-------|-------|------
SKU-A  | 1000  | $50   | $50K  | 41%   | A
SKU-B  | 500   | $40   | $20K  | 57%   | A
SKU-C  | 2000  | $8    | $16K  | 70%   | A
SKU-D  | 1500  | $5    | $7.5K | 76%   | B
SKU-E  | 1200  | $4    | $4.8K | 80%   | B
SKU-F  | 3000  | $2    | $6K   | 85%   | B
SKU-G  | 5000  | $1    | $5K   | 89%   | C
... (50 more)
```

### **Management Strategy by Category**

```
Category A (High Value):
- Tight inventory control
- Frequent forecasting updates
- Higher safety stock
- Monitor daily
- Example: Keep 95% service level

Category B (Medium Value):
- Standard control
- Weekly updates
- Standard safety stock
- Monitor weekly
- Example: Keep 90% service level

Category C (Low Value):
- Loose control
- Monthly updates
- Low safety stock
- Monitor monthly
- Example: Keep 80% service level
```

---

## 🎯 Performance Metrics

### **Inventory Turnover**

**Formula:**
```
Turnover = COGS / Average Inventory Value
```

**Interpretation:**
```
Turnover = 4 means inventory is replaced 4 times/year
Higher = Better (less capital tied up)
Lower = Worse (excess inventory)

Industry Benchmarks:
- Retail: 5-8
- Pharmacy: 8-12
- Manufacturing: 4-6
- E-commerce: 6-10
```

---

### **Fill Rate**

**Formula:**
```
Fill Rate = Units Fulfilled / Units Demanded × 100%
```

**Example:**
```
Customer demand: 1,000 units
Units in stock: 950 units
Fill rate = 950/1,000 = 95%

Missing: 50 units (5% stockout)
```

---

### **Carrying Cost**

**Formula:**
```
Carrying Cost = Average Inventory × Holding Cost %

Holding Cost includes:
- Storage space: 5%
- Utilities: 2%
- Insurance: 1.5%
- Obsolescence: 2%
- Total: ~10.5% of inventory value
```

---

## 📈 Key Assumptions

1. **Demand is independent and identically distributed**
2. **Lead times are relatively consistent**
3. **No supply constraints**
4. **Holding costs are linear**
5. **No quantity discounts**

---

## ⚠️ Limitations

- ✅ Works best with consistent demand
- ⚠️ May not work for highly volatile products
- ⚠️ Seasonal spikes need manual adjustment
- ⚠️ Does not account for supply constraints
- ⚠️ Assume lead times are stable

---

