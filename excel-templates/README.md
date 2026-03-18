# 📊 Excel Templates Guide

**5 professional templates for supply chain optimization**

---

## 📁 Files Included

### **1. supply-chain-master.xlsm** (Main Dashboard)
**Purpose:** Central control panel for all calculations

**Sheets:**
- Data Input: Load your sales data
- Dashboard: KPI summary + recommendations
- Alerts: Real-time warnings
- Calculations: All formulas (reference)

**Key Features:**
- ✅ Automatic calculations
- ✅ Visual KPI cards
- ✅ Color-coded alerts
- ✅ Macro-enabled (VBA)
- ✅ One-button refresh

**How to Use:**
1. Open file (enable macros)
2. Go to "Data Input" sheet
3. Paste your CSV data (starting A1)
4. Click "Refresh" button
5. View "Dashboard" sheet for results

**Data Format:**
```
Date          | Product | Quantity | Unit_Cost | Lead_Time_Days
2024-01-01   | SKU-A   | 100      | 50        | 7
2024-01-02   | SKU-A   | 105      | 50        | 7
```

---

### **2. forecasting-dashboard.xlsx** (Demand Forecasting)
**Purpose:** Compare 4 forecasting methods

**Methods Included:**
1. Moving Average
2. Exponential Smoothing (ETS)
3. ARIMA
4. Ensemble (weighted average)

**Sheets:**
- Input: Paste historical demand
- Moving Average: Simple 3-month forecast
- Exponential Smoothing: Adaptive forecast
- ARIMA: Advanced pattern detection
- Ensemble: Combined forecast
- Comparison: Choose best method
- Accuracy: MAE, RMSE, MAPE metrics

**Results:**
```
MAE = 2.5 units        ← Lower is better
RMSE = 3.1 units       ← Lower is better
MAPE = 1.8%            ← Lower is better
WINNER: Prophet        ← Recommended method
```

**When to Use Each Method:**
- Moving Average: Stable demand, <6 months data
- ETS: Light trends, 6-12 months data
- ARIMA: Complex patterns, 12+ months data
- Ensemble: Mix of patterns, when unsure

---

### **3. inventory-optimization.xlsx** (EOQ + ROP)
**Purpose:** Calculate optimal order quantities

**Sections:**
1. **EOQ Calculator**
   - Input: Annual demand, order cost, holding cost
   - Output: Optimal order quantity
   - Savings calculation

2. **ROP Calculator**
   - Input: Average demand, lead time, desired service level
   - Output: Reorder point
   - Safety stock

3. **Cost Analysis**
   - Total cost comparison
   - Sensitivity analysis (what-if)
   - Best vs current strategy

**Example Calculation:**
```
Annual Demand: 12,000 units
Order Cost: $50/order
Holding Cost: $2/unit/year

EOQ = √(2 × 12,000 × 50 / 2) = 775 units

Current Strategy (ordering 1,000 units):
Total Cost = $1,600/year

Recommended (ordering 775 units):
Total Cost = $1,550/year

Savings: $50/year (3.1%)
```

---

### **4. abc-classification.xlsx** (Pareto Analysis)
**Purpose:** Classify products using 80/20 rule

**Workflow:**
1. Paste product data (name, annual sales)
2. System auto-calculates percentage
3. Automatic classification to A/B/C
4. Management strategy by category

**Output:**
```
Category A (Top 20%): 80% of sales value
├─ Tight control
├─ High safety stock
├─ Weekly monitoring

Category B (Middle 30%): 15% of sales value
├─ Standard control
├─ Normal safety stock
├─ Monthly monitoring

Category C (Bottom 50%): 5% of sales value
├─ Loose control
├─ Low safety stock
├─ Quarterly monitoring
```

**Sheets:**
- Input: Product list + sales data
- Sorted: Auto-sorted by value
- Classification: A/B/C assignment
- Strategy: Recommended actions by category

---

### **5. case-studies.xlsx** (Examples)
**Purpose:** Real-world examples you can learn from

**Included Cases:**
1. **Retail Chain (500 stores, 2,500 SKUs)**
   - Results: 32% inventory reduction, 60% fewer stockouts
   - Before/after metrics
   - Implementation timeline

2. **Pharmacy Network (45 locations, 3,500 products)**
   - Results: 78% reduction in expired inventory
   - Seasonal forecasting approach
   - Results tracking

3. **Manufacturing Supplier (450 components)**
   - Results: 30% inventory reduction, $660K freed
   - Lead time optimization
   - Supplier management

**How to Use These:**
- Study similar company to yours
- Use as template for your analysis
- Reference for management presentations
- ROI calculation template

---

## 🔧 Creating Your Own Template

### **Step 1: Prepare Data**
```
Minimum requirements:
- 12 months of sales history
- Product information (name, cost, lead time)
- Consistent date format (YYYY-MM-DD)
- No blank rows
```

### **Step 2: Load Data**
```
1. Open supply-chain-master.xlsm
2. Go to "Data Input" sheet
3. Paste data starting at cell A1
4. Headers must match template
```

### **Step 3: Run Calculations**
```
1. Click "Refresh" button (yellow, top-right)
2. Wait 5-10 seconds for macro
3. Check for error messages
4. Review results
```

### **Step 4: Interpret Results**
```
Go to "Dashboard" sheet and review:
- EOQ (Optimal order quantity)
- ROP (Reorder point)
- Safety Stock (Buffer amount)
- Forecast (Next period demand)
- Cost Savings (Est. annual savings)
```

---

## 📊 Understanding Formulas

### **EOQ Formula**
```excel
=SQRT(2*D*S/H)

Where:
D = Annual Demand
S = Order Cost
H = Holding Cost
```

### **ROP Formula**
```excel
=(D_avg*LT)+SS

Where:
D_avg = Average Daily Demand
LT = Lead Time (days)
SS = Safety Stock
```

### **Safety Stock Formula**
```excel
=Z*STDEV(demand)*SQRT(LT)

Where:
Z = Service Level Z-score (1.65 for 95%)
STDEV = Standard Deviation of Demand
LT = Lead Time
```

---

## 🐛 Troubleshooting

### **Problem: #REF! Error**
**Cause:** Formula refers to deleted cells
**Fix:**
1. Check data is pasted correctly
2. Delete error cell
3. Re-enter formula
4. Click Refresh

### **Problem: Macros Don't Work**
**Cause:** Macros disabled in Excel
**Fix:**
1. File → Options → Trust Center
2. Trust Center Settings → Macro Settings
3. Select "Enable All Macros"
4. Restart Excel

### **Problem: Formulas Very Slow**
**Cause:** Large dataset or calculation intensive
**Fix:**
1. Close other programs
2. Reduce data range
3. Manual refresh (Ctrl+Shift+R) instead of auto
4. Split into multiple files

### **Problem: Wrong Results**
**Cause:** Data format or missing values
**Fix:**
1. Check all numbers are numeric (not text)
2. Verify dates in YYYY-MM-DD format
3. Remove blank rows
4. Check for duplicate entries

---

## 💡 Pro Tips

### **Tip 1: Scenario Analysis**
Create multiple sheets for different scenarios:
```
Sheet "Conservative": 97% service level, higher safety stock
Sheet "Aggressive": 85% service level, lower safety stock
Sheet "Current": What you're doing now
Compare results to find optimal strategy
```

### **Tip 2: Automation**
```
Excel → Options → Formulas
Set to "Automatic" recalculation
Formulas update as you type
```

### **Tip 3: Charts**
```
Select data → Insert → Chart
Choose from:
- Line chart (trends over time)
- Column chart (comparing values)
- Pie chart (ABC classification)
- Gauge chart (KPIs)
```

### **Tip 4: Dashboard**
```
Create summary with:
- Big KPI numbers (EOQ, ROP)
- Trend chart (demand over time)
- Alert box (red/yellow/green)
- ABC matrix (scatter plot)
```

---

## 📚 Learning Path

**Week 1:**
- [ ] Download templates
- [ ] Study this README
- [ ] Review examples in case-studies.xlsx
- [ ] Watch demo video (optional)

**Week 2:**
- [ ] Load your data
- [ ] Run calculations
- [ ] Review results
- [ ] Compare to current strategy

**Week 3:**
- [ ] Present to team
- [ ] Get approval
- [ ] Start implementation

**Week 4+:**
- [ ] Track results
- [ ] Update monthly
- [ ] Adjust parameters if needed

---

## 🎯 Next Steps

1. **Read:** [Getting Started Guide](../documentation/GETTING_STARTED.md)
2. **Learn:** [Methodology & Formulas](../documentation/METHODOLOGY.md)
3. **Questions?** Check [FAQ](../documentation/FAQ.md)
4. **Help:** [GitHub Issues](https://github.com/yourusername/supply-chain-optimization/issues)

---

**Ready to optimize?** Download all 5 templates and get started today! 🚀

