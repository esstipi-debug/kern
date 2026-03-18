# 🤝 Contributing Guide

Thank you for considering contributing! This document provides guidelines and instructions for contributing.

---

## 📋 Code of Conduct

- Be respectful and inclusive
- Welcome all experience levels
- Focus on constructive feedback
- No harassment, discrimination, or trolling

---

## 🐛 Reporting Bugs

### **Before Submitting**
1. Check [FAQ](documentation/FAQ.md)
2. Search [Issues](https://github.com/yourusername/supply-chain-optimization/issues)
3. Test with latest version

### **Submit Bug Report**
```markdown
**Title:** [BUG] Brief description

**Environment:**
- Excel/Power BI version
- Operating system
- Python version (if applicable)

**Steps to Reproduce:**
1. Step 1
2. Step 2
3. Step 3

**Expected behavior:**
[What should happen]

**Actual behavior:**
[What actually happens]

**Screenshots:**
[If applicable]

**Sample data:**
[Optional CSV or file]
```

---

## 💡 Feature Requests

### **Before Suggesting**
1. Check if feature exists
2. Search existing issues
3. Read [Roadmap](README.md#-roadmap)

### **Submit Feature Request**
```markdown
**Title:** [FEATURE] Brief description

**Problem:**
[What problem does this solve?]

**Solution:**
[How would this work?]

**Benefits:**
- Benefit 1
- Benefit 2

**Alternative approaches:**
[Other solutions considered]

**Impact:**
- How many users affected?
- How important?
```

---

## 🔧 Development Setup

### **Requirements**
- Python 3.8+
- Git
- Excel 2016+ or Power BI Desktop
- Basic familiarity with supply chain concepts

### **Local Setup**
```bash
# Clone repository
git clone https://github.com/yourusername/supply-chain-optimization.git
cd supply-chain-optimization

# Create virtual environment (optional but recommended)
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies (if any)
pip install -r requirements.txt

# Install pre-commit hooks
pre-commit install
```

---

## 📝 Development Workflow

### **1. Create Feature Branch**
```bash
git checkout -b feature/your-feature-name
# Or: git checkout -b fix/bug-name
```

### **2. Make Changes**
- Keep commits small and focused
- Write clear commit messages
- Follow existing code style
- Update documentation

### **3. Test Changes**
- Test on Excel 2016 and latest version
- Test on Windows and Mac (if possible)
- Test with sample data

### **4. Submit Pull Request**
```markdown
**Title:** [TYPE] Brief description

**Description:**
Explain what this PR does and why.

**Type of Change:**
- [ ] Bug fix
- [ ] New feature
- [ ] Documentation update
- [ ] Performance improvement

**Testing:**
- [ ] Tested on Excel 2016
- [ ] Tested on latest Excel
- [ ] Tested on Windows
- [ ] Tested on Mac

**Checklist:**
- [ ] Code follows style guidelines
- [ ] Documentation updated
- [ ] No breaking changes
- [ ] Self-reviewed the code

**Related Issues:**
Fixes #123
```

---

## 📁 Ways to Contribute

### **Code Contributions**
- Bug fixes
- New Excel functions
- New Power BI visuals
- Performance improvements
- Python enhancements

### **Documentation**
- Improve guides
- Add examples
- Fix typos
- Translate to other languages

### **Case Studies**
- Submit your success story
- Industry-specific examples
- Lessons learned
- ROI tracking

### **Community**
- Answer questions in Issues
- Share on social media
- Write blog posts
- Conduct webinars

---

## 📊 Excel Template Contributions

### **Adding New Formula**
1. Place in new sheet: `Formulas_New`
2. Document with:
   - Purpose
   - Formula explanation
   - Example
   - Assumptions
3. Test with various data
4. Update USER_GUIDE.md

### **Example Template Structure**
```
Sheet: "EOQ Variations"
│
├─ Section: Formula explanation
├─ Section: Input parameters
├─ Section: Calculation
├─ Section: Results
└─ Section: Interpretation
```

---

## 📈 Power BI Contribution Guidelines

### **New Dashboard/Visual**
1. Create in new `.pbix` file
2. Use standard color scheme
3. Document DAX formulas
4. Include sample data
5. Test with different data sizes

### **Power BI Best Practices**
- Use consistent naming
- Clean data model
- Efficient DAX queries
- Responsive design (mobile-friendly)
- Clear visual hierarchy

---

## 🐍 Python Code Guidelines

If contributing Python code:

### **Style**
- Follow PEP 8
- Use meaningful variable names
- Comment complex logic
- Type hints where applicable

### **Testing**
```bash
# Run tests
pytest tests/

# Check code quality
pylint supply_chain/

# Format code
black supply_chain/
```

---

## 📚 Documentation Standards

### **For .md Files**
- Use clear headings (H1, H2, H3)
- Include examples
- Add code blocks with syntax highlighting
- Use emojis for quick scanning
- Link to related docs

### **For Comments**
- Explain "why", not "what"
- Keep updated with code
- Use professional tone

---

## ✅ PR Review Process

1. **Automated Checks**
   - Code quality scanning
   - Spell checking
   - Format validation

2. **Manual Review**
   - Code review by maintainers
   - Functionality testing
   - Documentation review

3. **Feedback**
   - Constructive comments
   - Requests for changes
   - Approval

4. **Merge**
   - Squash commits (if needed)
   - Merge to main
   - Close related issues

---

## 🎯 Contribution Ideas

### **High Priority**
- [ ] Polish existing documentation
- [ ] Add more case studies
- [ ] Create video tutorials
- [ ] Improve error handling
- [ ] Performance optimization

### **Medium Priority**
- [ ] New forecasting methods
- [ ] Industry-specific templates
- [ ] Integration guides (ERP, CRM)
- [ ] Translated documentation
- [ ] Add-on tools

### **Fun Ideas**
- Create companion tools
- Build industry adapters
- Write comparison articles
- Submit to awards/contests
- Start community discussions

---

## 🏆 Recognition

Contributors are recognized:
- In README.md
- In CHANGELOG.md
- In GitHub contributors page
- Shoutout on social media (if public)
- Potential speaking opportunities

---

## 📞 Questions?

- 📧 Email: support@example.com
- 💬 GitHub Discussions: [Link]
- 🐦 Twitter: [@example]
- 📱 Discord: [Link]

---

**Thank you for contributing!** 🙏

