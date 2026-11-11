import csv
import os

compact_path = os.path.join(os.path.dirname(__file__), "data", "precios_compacto.csv")

first_date = None
last_date = None
dates_set = set()
count = 0

with open(compact_path, "r", encoding="utf-8", errors="ignore") as f:
    reader = csv.DictReader(f)
    for row in reader:
        count += 1
        d = row.get("fecha")
        if d:
            dates_set.add(d)
        if count % 200000 == 0:
            print(f"Read {count} rows... Unique dates so far: {len(dates_set)}")

sorted_dates = sorted(list(dates_set))
print(f"Total rows in compact CSV: {count}")
print(f"Total unique dates: {len(sorted_dates)}")
print(f"First date: {sorted_dates[0] if sorted_dates else 'none'}")
print(f"Last date: {sorted_dates[-1] if sorted_dates else 'none'}")
print(f"Sample dates: {sorted_dates[:5]} ... {sorted_dates[-5:]}")
