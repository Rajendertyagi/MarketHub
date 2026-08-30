#!/usr/bin/env python3
"""Fix the stress test canonical IDs."""
import re

path = r'D:\Temp\MarketHub\test\test_condition_alert_stress.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# Replace all NSE:EQ: variants with proper canonical IDs
content = content.replace('NSE:EQ:RELIANCE', 'NSE:EQUITY:INE002A01018')
content = content.replace('NSE:EQ:INFY', 'NSE:EQUITY:INE009A01021')

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print(f'Fixed {content.count("NSE:EQUITY")} canonical IDs')