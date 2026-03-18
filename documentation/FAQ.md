# ❓ Frequently Asked Questions

---

## **General Questions**

### **Q: Do I need Excel expertise to use this?**
A: No! The templates are beginner-friendly with step-by-step instructions. Even if you've never used advanced Excel features, you can follow the guides. All calculations are automated.

### **Q: Can I use on Mac?**
A: Yes! All templates work on Mac. Excel for Mac has VBA support (macros) so everything functions identically.

### **Q: How much does this cost?**
A: It's completely free! The project is open-source under MIT license. You can use, modify, and distribute freely.

### **Q: Can I use commercially?**
A: Yes! MIT license allows commercial use. You can use in your business, resell, or integrate into products.

### **Q: Is my data private?**
A: Completely! All files are local on your computer. No data is sent to any server. Total privacy.

---

## **Excel Questions**

### **Q: Which Excel version do I need?**
A: Excel 2016 or newer (Windows or Mac). Office 365 is recommended for latest features.

### **Q: Why does my macro not work?**
A: Excel has macro security disabled by default. Fix:
1. File → Options → Trust Center
2. Click "Trust Center Settings"
3. Click "Macro Settings"
4. Select "Enable All Macros"
5. Click OK

### **Q: Can I use Google Sheets?**
A: Not directly (Google Sheets doesn't support VBA macros). But you can:
1. Download template
2. Convert to Google Sheets (some features may not work)
3. Or: Use the formulas manually in Google Sheets

### **Q: What if I get #REF! error?**
A: Usually means data format issue. Fix:
1. Check all dates are in YYYY-MM-DD format
2. Remove blank rows
3. Delete the error cell and re-enter formula
4. Click "Refresh" button

### **Q: Can I edit the formulas?**
A: Yes! All formulas are visible and editable. Change parameters like:
- Service level (95% vs 90%)
- Lead time
- Cost factors

### **Q: How do I add more products?**
A: Simply paste more data rows. The formulas automatically adapt to new rows.

---

## **Power BI Questions**

### **Q: Do I need Power BI Pro subscription?**
A: Power BI Desktop (free) is enough for creating/editing dashboards. Pro subscription ($10/month) needed only for sharing online.

### **Q: Can I use Power BI Online?**
A: Yes! Open Power BI Service, upload dashboard, and it syncs automatically. Your data stays in your data source.

### **Q: How often does data refresh?**
A: 
- Desktop: Manual or scheduled (import mode)
- Online: Every 8 hours (free tier) or hourly (Pro)
- Real-time: Update as often as your source allows

### **Q: Can I customize the visuals?**
A: Yes! Click Edit and modify any visual, color, or metric. All DAX formulas are editable.

### **Q: How do I share with my team?**
A: 
1. Publish to Power BI Service
2. Share the app/report link
3. Grant access to team members
4. They can view dashboards (no editing without Pro)

---

## **Data Questions**

### **Q: How much historical data do I need?**
A: Minimum 12 months (1 year). More is better:
- 12 months: Captures seasonality
- 24 months: Better accuracy
- 36+ months: Most accurate

### **Q: What if I have less than 12 months?**
A: You can start with whatever you have:
- 3-6 months: Use Moving Average or Exponential Smoothing
- 6-12 months: Good for most models
- 12+ months: Optimal

### **Q: Can I use forecast data?**
A: Not recommended for training. Historical actual sales are better. Forecasts introduce bias.

### **Q: What if my data has gaps?**
A: Try:
1. Interpolate missing values
2. Average surrounding months
3. Use trend if caused by known event
4. Remove and note in analysis

### **Q: How do I handle Black Friday/holiday spikes?**
A: Options:
1. Remove outlier months, then forecast
2. Use Prophet (handles holidays automatically)
3. Manually adjust forecast +X%
4. Create separate model for peak season

### **Q: What about seasonal products?**
A: All methods handle seasonality:
- Prophet: Automatic (best option)
- ARIMA: With seasonal terms
- Exponential Smoothing: With seasonal component
- Moving Average: Adequate but less accurate

---

## **Results Interpretation**

### **Q: My EOQ is 0. What does this mean?**
A: Error in input data. Check:
1. Annual demand > 0
2. Order cost > 0
3. Holding cost > 0
4. No blank cells

### **Q: EOQ seems very high/low. Is it wrong?**
A: Maybe! EOQ is sensitive to inputs:
```
Low EOQ (100-200 units):
- Low demand OR high order cost OR low holding cost
- Consider: More frequent small orders

High EOQ (1000-5000 units):
- High demand OR low order cost OR high holding cost
- Consider: Less frequent large orders
```

### **Q: My forecast is 0% accurate. Why?**
A: Possible causes:
1. Data is too noisy/random
2. Trend just changed (new market)
3. Seasonality changed
4. Data quality issues

Fix:
- Increase training period
- Adjust parameters
- Review for anomalies

### **Q: Forecast accuracy 85%. Is that good?**
A: It depends:
```
>90%: Excellent (rare consumer goods)
80-90%: Very good (most companies)
70-80%: Acceptable (volatile markets)
<70%: Needs improvement (check data/method)
```

---

## **Technical Questions**

### **Q: Can I automate updates?**
A: Yes!
- Excel: Use Macro scheduler or Task Scheduler
- Power BI: Schedule refresh in Power BI Service

### **Q: Can I integrate with my ERP?**
A: Yes! If your ERP exports to CSV/Excel/SQL:
1. Export data from ERP
2. Load into template
3. Template calculates recommendations

### **Q: Can I use with Salesforce/NetSuite/SAP?**
A: Power BI connects to these systems. Follow:
1. Get API credentials from system
2. Power BI → Get Data → [System]
3. Connect and authenticate
4. Dashboard auto-refreshes

### **Q: Can I export results?**
A: Yes!
- Excel: Save as CSV/PDF
- Power BI: Export data or visual images

---

## **Support Questions**

### **Q: Where do I get help?**
A: Resources:
1. [Getting Started Guide](GETTING_STARTED.md)
2. [User Guide](USER_GUIDE.md)
3. [GitHub Issues](https://github.com/yourusername/supply-chain-optimization/issues)
4. [Email Support](mailto:support@example.com)

### **Q: How do I report a bug?**
A: Open issue on GitHub:
1. Go to Issues tab
2. Click "New Issue"
3. Describe bug with:
   - Excel/Power BI version
   - Data sample
   - Steps to reproduce
   - Screenshot

### **Q: How do I request a feature?**
A: Open GitHub issue with:
- Feature description
- Why it's useful
- How to implement (if you know)

### **Q: Can I contribute?**
A: Yes! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

---

## **Business Questions**

### **Q: What ROI can I expect?**
A: Typical results (see case studies):
- **Retail:** $50K-500K/year
- **Pharmacy:** $30K-200K/year
- **Manufacturing:** $100K-1M+/year
- **E-Commerce:** $20K-300K/year

### **Q: How long until I see results?**
A: 
- Immediate: Recommendations generated
- Week 1-2: First orders using new strategy
- Month 1: Early results visible
- Month 3: Clear ROI patterns

### **Q: Can I use for client consulting?**
A: Yes! You can:
- Use templates for clients
- Customize for their business
- Charge consulting fees
- Resell (with proper attribution)

### **Q: Can I build a business around this?**
A: Yes! Many options:
1. Consulting/Implementation services
2. Customized templates
3. Training/Workshops
4. Add-on services (API, Dashboard hosting)
5. Industry-specific versions

---

## **Troubleshooting**

### **Problem: "Excel cannot complete this task with available resources"**
**Solution:**
1. Close other programs
2. Save and close file
3. Reopen file
4. Try calculation again
5. Reduce data size if needed

### **Problem: Power BI takes 5+ minutes to load**
**Solution:**
1. Check internet connection
2. Try "Refresh" button
3. Reduce date range
4. Upgrade to Power BI Pro

### **Problem: "This file is in an older Excel format"**
**Solution:**
1. Right-click file
2. Select "Open with" → Excel 2016+
3. Click "Update" to convert format
4. Save as .xlsm

### **Problem: Macros are slow**
**Solution:**
1. Close other applications
2. Disable automatic calculations: Ctrl+Shift+F9
3. Manual refresh after data entry
4. Consider Power BI for large datasets

---

## **Still Can't Find Answer?**

- 📧 **Email:** support@supplychain-optimization.com
- 💬 **GitHub Issues:** [Create issue](https://github.com/yourusername/supply-chain-optimization/issues)
- 📱 **Community Discord:** [Join server](https://discord.gg/example)
- 🐦 **Twitter:** [@example](https://twitter.com/example)

---

