"""Generate the fixed, fictional weekly ordering fixture with the standard library.

Usage: python generate.py /path/to/new/output-directory
"""
from datetime import date, timedelta
from pathlib import Path
from random import Random
import csv
import sys

out=Path(sys.argv[1]).resolve()
if out.exists():
    raise FileExistsError(f'Refusing to replace existing output: {out}')
out.mkdir(parents=True)
as_of=date(2026,9,29)
start=as_of-timedelta(days=84)
points=[('DEMO-POINT-01',1.15),('DEMO-POINT-02',1.00),('DEMO-POINT-03',0.78)]
skus=[
 ('DEMO-SKU-01','ДЕМО — Шаурма с курицей',30,12,3,1),
 ('DEMO-SKU-02','ДЕМО — Шаурма с говядиной',19,8,3,1),
 ('DEMO-SKU-03','ДЕМО — Шаурма с фалафелем',15,8,5,2),
 ('DEMO-SKU-04','ДЕМО — Шаурма острая',11,6,3,1),
 ('DEMO-SKU-05','ДЕМО — Шаурма сырная',13,8,4,2),
 ('DEMO-SKU-06','ДЕМО — Шаурма мини',10,10,2,1),
]
rng=Random(20260929)
rows=[]
for offset in range(84):
 day=start+timedelta(days=offset)
 week=offset//7
 for pidx,(point,scale) in enumerate(points):
  for sidx,(sku,name,base,*_) in enumerate(skus):
   weekday=day.weekday()
   day_factor=1.25 if weekday in (5,6) else 1.10 if weekday==4 else 0.91 if weekday==0 else 1.0
   if pidx==1:day_factor*=1.16 if weekday<5 else 0.86
   trend=1+week*0.008
   if sidx==2:trend*=1+week*0.006
   jitter=rng.randint(-4,4)
   demand=max(0,round(base*scale*day_factor*trend+jitter))
   lost=0
   if pidx==0 and sidx==0 and day in (date(2026,8,22),date(2026,8,23)):
    lost=round(demand*0.35)
   elif pidx==2 and sidx==3 and day in (date(2026,9,5),date(2026,9,6)):
    lost=round(demand*0.25)
   elif rng.randrange(37)==0:
    lost=rng.randint(1,min(3,demand))
   rows.append((day.isoformat(),point,sku,name,demand-lost,demand))
with (out/'history.csv').open('w',encoding='utf-8',newline='') as f:
 w=csv.writer(f,lineterminator="\n");w.writerow(('date','point_key','sku_key','sku_name','qty_sold','qty_demand'));w.writerows(rows)
latest={(p,s):[] for p,_ in points for s,*_ in skus}
for day,p,s,name,sold,demand in rows[-7*len(points)*len(skus):]:latest[(p,s)].append(demand)
planning=[]
for pidx,(point,scale) in enumerate(points):
 for sidx,(sku,name,base,pack,shelf,lead) in enumerate(skus):
  recent=round(sum(latest[(point,sku)])/7)
  stock=max(pack,round(recent*(0.75+0.15*((pidx+sidx)%3))))
  incoming=pack if (pidx+sidx)%3==0 else 0
  safety=max(pack,round(recent*0.35))
  if pidx==2 and sidx==2: stock,incoming=32,8
  planning.append((point,sku,stock,incoming,safety,pack,pack,shelf,lead))
with (out/'planning.csv').open('w',encoding='utf-8',newline='') as f:
 w=csv.writer(f,lineterminator="\n");w.writerow(('point_key','sku_key','stock_on_hand','incoming_qty','safety_stock','pack_size','min_order','shelf_life_days','lead_time_days'));w.writerows(planning)
print(len(rows),len(planning),start,as_of-timedelta(days=1))
