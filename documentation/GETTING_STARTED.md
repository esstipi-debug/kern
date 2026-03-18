# 🚀 Getting Started Guide

**Get up and running in 10 minutes!**

---

## ✅ Prerequisites

### **For Excel Users**
- ✅ Microsoft Excel 2016 or newer
- ✅ Windows or Mac
- ✅ ~10 minutes
- ✅ Your data (CSV or manual entry)

### **For Power BI Users**
- ✅ Power BI Desktop (free)
- ✅ Windows 10/11
- ✅ ~15 minutes
- ✅ Your data (Excel, CSV, or SQL)

---

## 📥 Step 1: Download Files

### **Option A: Clone Repository**
```bash
git clone https://github.com/yourusername/supply-chain-optimization.git
cd supply-chain-optimization
```

### **Option B: Download ZIP**
- Go to [Releases](https://github.com/yourusername/supply-chain-optimization/releases)
- Download latest version
- Extract to your computer

---

## 📊 Step 2: Prepare Your Data

### **Required Format**

Your data should have these columns:

```
Date          Product_ID  Quantity  Unit_Cost  Lead_Time_Days
2024-01-01   SKU-A       100       50         7
2024-01-02   SKU-A       105       50         7
2024-01-03   SKU-B       250       25         14
2024-01-04   SKU-B       245       25         14
2024-01-05   SKU-C       50        100        3
```

### **Data Tips**
- ✅ Use consistent date format (YYYY-MM-DD)
- ✅ Include at least 12 months of history
- ✅ Remove blank rows
- ✅ Keep SKU names consistent

### **Sample Data**
Need test data? Use [sample-data.csv](../sample-data.csv) included in repo.

---

## 🎯 Step 3: Excel Setup (5 minutes)

### **Option A: supply-chain-master.xlsm**

**This is your main dashboard. Follow these steps:**

1. **Open File**
   - Double-click `excel-templates/supply-chain-master.xlsm`
   - Click "Enable Macros" when prompted

2. **Load Data**
   - Go to "Data Input" sheet
   - Paste your data starting at cell A1
   - Headers should be in row 1

3. **Run Calculations**
   - Click "Refresh" button (yellow button on right)
   - Or: Press Ctrl+Shift+R
   - Wait 5-10 seconds for calculations

4. **View Results**
   - Click "Dashboard" tab
   - See all recommendations instantly

### **What You'll See**

| Metric | Example | Meaning |
|--------|---------|---------|
| **EOQ** | 548 units | Optimal order quantity |
| **ROP** | 205 units | Reorder when stock hits this |
| **Safety Stock** | 103 units | Buffer for uncertainty |
| **Forecast** | 5,200 units | Expected demand next month |
| **Cost Savings** | $127K/year | Estimated savings |

### **Next Steps**
- Review "Recommendations" sheet
- Update your supplier orders
- Monitor "Alerts" sheet weekly
- Update data monthly

---

## 📈 Step 4: Power BI Setup (10 minutes)

### **First Time Setup**

1. **Open Power BI Desktop**
   - Download free from [powerbi.microsoft.com](https://powerbi.microsoft.com)
   - Install and open

2. **Open Dashboard File**
   - File → Open
   - Navigate to `power-bi/supply-chain-dashboard.pbix`
   - Click Open

3. **Connect Data Source**
   - Click "Transform data" (or Edit Queries)
   - Select your data source:
     - **Excel file:** Browse and select
     - **CSV:** Browse and select
     - **SQL Server:** Enter connection string
     - **Other:** Use "Get Data"

4. **Load & Refresh**
   - Click "Close & Apply"
   - Wait for dashboard to refresh
   - All visuals update automatically

5. **Publish (Optional)**
   - File → Publish
   - Sign in to Power BI Service
   - Select workspace
   - Share link with team

### **Dashboard Navigation**

```
📊 Overview
├─ KPI Cards (EOQ, ROP, Forecast, etc.)
├─ Time series chart (Demand trends)
└─ ABC Classification matrix

📈 Inventory Health
├─ Stock level gauge
├─ Reorder alerts
└─ Turnover metrics

🎯 Forecast Accuracy
├─ Forecast vs Actual
├─ Accuracy %, MAE, RMSE
└─ Trend analysis
```

---

## 🎓 Step 5: Understanding the Results

### **EOQ (Economic Order Quantity)**

```
Formula: √(2 × Annual Demand × Order Cost / Holding Cost)

Example:
- Annual demand: 12,000 units
- Order cost: $50 per order
- Holding cost: $2 per unit per year
- EOQ = √(2 × 12,000 × 50 / 2) = 548 units

Meaning: Order 548 units each time to minimize total cost
```

### **ROP (Reorder Point)**

```
Formula: (Average Demand × Lead Time) + Safety Stock

Example:
- Average demand: 100 units/day
- Lead time: 7 days
- Safety stock: 103 units
- ROP = (100 × 7) + 103 = 803 units

Meaning: When inventory reaches 803 units, place new order
```

### **Safety Stock**

```
Formula: Z-score × Std Dev of Demand × √Lead Time

Example:
- Service level: 95% (Z = 1.65)
- Demand std dev: 15 units
- Lead time: 7 days
- Safety Stock = 1.65 × 15 × √7 = 65 units

Meaning: Keep extra 65 units to prevent stockouts 95% of the time
```

---

## 🔄 Step 6: Monthly Updates

### **Update Your Data**

```
Every month:
1. Add new sales data to Data Input sheet
2. Click "Refresh" button
3. Review updated recommendations
4. Update supplier orders if needed
5. Track actual vs forecast
```

### **Monitor Key Metrics**

| Metric | Check | Action |
|--------|-------|--------|
| **Forecast Accuracy** | < 80% | Review assumptions |
| **Stockouts** | > 5% | Increase safety stock |
| **Excess Stock** | > 20% | Decrease EOQ |
| **Service Level** | < 95% | Adjust ROP |

---

## 📊 Step 7: Interpret Your Data

### **Green Light ✅**
- Forecast accuracy > 85%
- Stockouts < 2%
- Service level > 95%
- No excess inventory > 20%

### **Yellow Light ⚠️**
- Forecast accuracy 75-85%
- Stockouts 2-5%
- Service level 90-95%
- Excess inventory 15-20%
- **Action:** Review data quality, adjust parameters

### **Red Light 🔴**
- Forecast accuracy < 75%
- Stockouts > 5%
- Service level < 90%
- Excess inventory > 20%
- **Action:** Review methodology, check for anomalies

---

## 🆘 Troubleshooting

### **Problem: Macros don't work in Excel**
**Solution:**
1. File → Options → Trust Center
2. Click "Trust Center Settings"
3. Click "Macro Settings"
4. Select "Enable All Macros"
5. Click OK

### **Problem: Power BI shows "Authentication failed"**
**Solution:**
1. Click "Edit Queries"
2. Select your data source
3. Click "Credentials"
4. Re-enter authentication info
5. Click "Connect"

### **Problem: Formulas show #REF! error**
**Solution:**
1. Check data format (dates, numbers)
2. Ensure no blank rows in data
3. Delete calculations and re-paste data
4. Click "Refresh" button again

### **Problem: Dashboard is blank/empty**
**Solution:**
1. Verify data is connected
2. Click "Refresh" in Power BI
3. Check data source path is correct
4. Restart Power BI if needed

---

## 📚 Next Steps

### **Learn More**
- Read [User Guide](USER_GUIDE.md) for detailed explanation
- Study [Methodology](METHODOLOGY.md) to understand formulas
- Review [Case Studies](../case-studies/CASE_STUDIES.md) for examples

### **Get Help**
- Check [FAQ](FAQ.md) for common questions
- Browse [GitHub Issues](https://github.com/yourusername/supply-chain-optimization/issues)
- Email: support@example.com

### **Level Up**
- Customize templates for your business
- Integrate with your ERP system
- Share dashboards with your team
- Track ROI over time

---

## ✅ You're Ready!

Congratulations! You now have:
- ✅ Professional forecasting templates
- ✅ Inventory optimization models
- ✅ Executive dashboards
- ✅ Automated recommendations

**Next:** Load your first data and see the magic! 🎯

Questions? Check [FAQ.md](FAQ.md) or open an issue on GitHub.

