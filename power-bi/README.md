# 📈 Power BI Dashboards Guide

**3 professional dashboards for supply chain monitoring**

---

## 📊 Dashboards Included

### **1. supply-chain-dashboard.pbix** (Executive Overview)
**Purpose:** High-level KPI monitoring and decision making

**Pages:**
1. **Overview (Home)**
   - 6 KPI cards (EOQ, ROP, Safety Stock, Forecast, Accuracy, Cost Savings)
   - Trend line (Demand over 12 months)
   - Top products by volume
   - Top products by value

2. **Forecasting**
   - Forecast vs Actual line chart
   - Forecast accuracy gauge
   - Error metrics (MAE, RMSE, MAPE)
   - Method performance comparison

3. **ABC Analysis**
   - Scatter plot (Product value vs frequency)
   - Category breakdown (pie chart)
   - High-value products list
   - Management recommendations by category

4. **Inventory Health**
   - Current stock levels
   - Stock age distribution
   - Days of inventory on hand
   - Reorder alerts

**Key Visuals:**
```
Top Left: EOQ Gauge (target = 548 units)
Top Right: ROP Gauge (target = 205 units)
Middle: Forecast Accuracy Line Chart
Bottom: ABC Scatter Matrix
```

**Data Sources:**
- Sales transactions (daily)
- Inventory levels (weekly)
- Supplier data (lead times)
- Product master (costs, categories)

---

### **2. inventory-health.pbix** (Operations Dashboard)
**Purpose:** Day-to-day inventory monitoring and alerts

**Pages:**
1. **Stock Levels**
   - Current stock by product
   - Color-coded status (green/yellow/red)
   - Stock vs Target comparison
   - Trend (5-year or last 3 years)

2. **Alerts & Exceptions**
   - 🔴 Critical: Stock below ROP (red)
   - 🟡 Warning: Stock below 1.5 × ROP (yellow)
   - 🟢 OK: Stock adequate (green)
   - Actions needed (table view)

3. **Turnover Metrics**
   - Inventory turnover ratio
   - Days of inventory on hand
   - Inventory age distribution
   - Slow-moving product alerts

4. **Receiving & Reordering**
   - Open purchase orders
   - Expected deliveries
   - Recommended orders (from EOQ model)
   - Supplier performance

**Real-time Features:**
- Auto-refresh every 4 hours (configurable)
- Mobile-friendly responsive design
- Drilldown to product detail
- Email alerts for exceptions

**Alert Rules:**
```
RED (Urgent):
- Stock < ROP
- Lead time remaining < 3 days

YELLOW (Attention):
- Stock < 1.5 × ROP
- Lead time remaining < 7 days

GREEN (OK):
- Stock >= 1.5 × ROP
- Lead time remaining > 7 days
```

---

### **3. forecast-vs-actual.pbix** (Analytics Dashboard)
**Purpose:** Track forecast accuracy and identify patterns

**Pages:**
1. **Forecast Accuracy**
   - Actual vs Forecast line chart (overlapping)
   - Error bands (±10%, ±20%)
   - Color-coded accuracy zones
   - Accuracy trend over time

2. **Detailed Metrics**
   - MAE (Mean Absolute Error)
   - RMSE (Root Mean Squared Error)
   - MAPE (Mean Absolute Percentage Error)
   - Accuracy by product/category

3. **Trend Analysis**
   - Trend decomposition (if available)
   - Seasonality visualization
   - Anomaly detection
   - Forecast method performance

4. **Drill-Down Analysis**
   - Select time period
   - View product-level detail
   - Compare forecasting methods
   - Export for further analysis

**Accuracy Targets:**
```
Excellent: > 90% accuracy
Good: 80-90% accuracy
Acceptable: 70-80% accuracy
Poor: < 70% accuracy (review method)
```

---

## 🚀 Getting Started

### **Step 1: Download Power BI Desktop**
- Free at: [powerbi.microsoft.com](https://powerbi.microsoft.com)
- System requirements: Windows 10+, 4GB RAM minimum
- Download size: ~200MB

### **Step 2: Open Dashboard**
1. Launch Power BI Desktop
2. File → Open
3. Navigate to `power-bi/supply-chain-dashboard.pbix`
4. Click "Open"

### **Step 3: Connect Data**
```
Option A: Excel File
1. Click "Transform data"
2. Select "New Source" → Excel
3. Browse to your file
4. Select worksheet

Option B: CSV File
1. Click "Transform data"
2. Select "New Source" → CSV
3. Browse to your file
4. Confirm format

Option C: SQL Server/Database
1. Click "Transform data"
2. Select "New Source" → SQL Server
3. Enter server name
4. Enter connection details
```

### **Step 4: Load & Refresh**
1. Click "Close & Apply"
2. Dashboard loads with your data
3. Wait 10-30 seconds for full load
4. Click "Refresh" to update

### **Step 5: Publish (Optional)**
```
To share with team:
1. File → Publish
2. Sign in to Power BI Service
3. Select workspace
4. Click "Select"
5. Share URL with team
```

---

## 📊 Key Metrics Explained

### **EOQ (Economic Order Quantity) - Card**
```
Display: 548 units

Meaning:
- Order exactly 548 units per cycle
- Minimizes total inventory cost
- Balances ordering cost vs holding cost

What to Do:
- If EOQ increases: Demand or costs changed
- Update supplier order quantities
- Adjust delivery schedules
```

### **ROP (Reorder Point) - Card**
```
Display: 205 units

Meaning:
- When stock reaches 205 units, place new order
- Accounts for lead time + demand uncertainty
- Prevents stockouts

What to Do:
- Set system alerts at this level
- Communicate to warehouse team
- Review if lead times change
```

### **Forecast Accuracy - Gauge**
```
Display: 87% (Green if > 85%)

Meaning:
- Forecast predictions match actual demand 87% of the time
- Average error: ±5 units per prediction

What to Do:
- > 90%: Excellent, trust forecast
- 80-90%: Good, use with caution
- < 80%: Review data quality and assumptions
```

### **Inventory Days - Number**
```
Display: 45 days

Meaning:
- Current inventory covers 45 days of average sales
- Formula: Current Stock / (Annual Sales / 365)

Targets by Industry:
- Retail: 30-60 days
- Pharmacy: 45-90 days
- Manufacturing: 30-90 days
```

---

## 🎨 Customization Guide

### **Change Colors**
1. Select visual
2. Format section (paint bucket icon)
3. Data colors → Change as needed
4. Common: Red (alert), Yellow (warning), Green (OK)

### **Add New Measures**
1. Home → New Measure
2. Enter DAX formula: `=SUM(Sales[Amount])`
3. Name it descriptively
4. Add to visual

### **Create New Visual**
1. Insert → Choose visual type
2. Drag fields from data model
3. Add filters/formatting
4. Save report

### **Example DAX Formulas**
```
Inventory Turnover:
=DIVIDE(SUM(Sales[Cost]), AVERAGE(Inventory[Value]))

Days of Inventory:
=DIVIDE(SUM(Inventory[Qty]), DIVIDE(SUM(Sales[Qty]),365))

Forecast Accuracy %:
=1 - ABS(DIVIDE(SUM(Forecast)-SUM(Actual), SUM(Actual)))
```

---

## 🔄 Refresh Schedule

### **Recommended Updates**
```
Real-time (Every 4 hours):
- Current inventory levels
- New sales/demand

Daily:
- Forecast calculations
- Accuracy metrics

Weekly:
- Supplier performance
- Exception reviews

Monthly:
- Full recalculation
- Model review
- Business review
```

### **Set Auto-Refresh (Power BI Service)**
1. Power BI Service → Your App
2. Settings (gear icon)
3. Scheduled refresh
4. Set frequency (4-8 hours typical)
5. Set time window

---

## 💡 Pro Tips

### **Tip 1: Drill-Down**
Hold Ctrl and click on chart element to drill-down to product level
```
Click on "Feb 2024" → Shows daily breakdown
Click on "SKU-A" → Shows supplier breakdown
Click on "A-Category" → Shows all A products
```

### **Tip 2: Bookmarks**
Save views as bookmarks for quick access:
1. View tab → Bookmarks
2. Create bookmark after filtering
3. Name it (e.g., "Top 20 Products")
4. Click to return instantly

### **Tip 3: Mobile View**
- File → View → Mobile Layout
- Rearrange for phone screens
- Touch-friendly filters
- Publish to Power BI Mobile app

### **Tip 4: Report Comments**
Share findings with team:
1. Click visual
2. Comments icon (speech bubble)
3. Add note/question
4. Team can respond

---

## 🆘 Troubleshooting

### **Problem: "Authentication Failed"**
**Solution:**
1. Click "Edit Queries"
2. Select data source
3. Click "Credentials"
4. Re-enter authentication info
5. Click "Connect"

### **Problem: Data Not Refreshing**
**Solution:**
1. Click "Refresh" button manually
2. Check data source is accessible
3. Verify file path/connection string
4. Check Power Query for errors

### **Problem: Visuals Look Blank**
**Solution:**
1. Check if filters are active
2. Verify data source has data
3. Check column names match
4. Restart Power BI Desktop

### **Problem: Performance Slow**
**Solution:**
1. Reduce date range in filters
2. Close other applications
3. Upgrade Power BI version
4. Simplify calculations
5. Consider Power BI Pro for better performance

---

## 📚 Learning Path

**Week 1:**
- [ ] Install Power BI Desktop
- [ ] Download dashboards
- [ ] Open example dashboard
- [ ] Explore visuals and pages

**Week 2:**
- [ ] Connect to your data
- [ ] Verify data loads correctly
- [ ] Review all dashboard pages
- [ ] Identify key insights

**Week 3:**
- [ ] Present to team
- [ ] Customize as needed
- [ ] Publish to Power BI Service (if desired)
- [ ] Set up automated refresh

**Week 4+:**
- [ ] Monitor daily
- [ ] Update data monthly
- [ ] Review metrics quarterly
- [ ] Continuous improvement

---

## 🎯 Next Steps

1. **Read:** [Getting Started](../documentation/GETTING_STARTED.md)
2. **Understand:** [Methodology](../documentation/METHODOLOGY.md)
3. **Ask:** [FAQ](../documentation/FAQ.md)
4. **Contribute:** [Contributing Guide](../CONTRIBUTING.md)

---

**Ready to build your dashboard?** Start with `supply-chain-dashboard.pbix` today! 🚀

