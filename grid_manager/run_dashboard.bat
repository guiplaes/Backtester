@echo off
cd /d "C:\Users\Administrator\Desktop\MT4 Claude"
"C:\Program Files\Python312\pythonw.exe" -m streamlit run grid_manager\dashboard.py --server.port 8502 --server.address 127.0.0.1 --server.headless true --browser.gatherUsageStats false >> grid_manager\logs\streamlit_8502.log 2>&1
