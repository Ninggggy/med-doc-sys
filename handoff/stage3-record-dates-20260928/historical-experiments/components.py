# HISTORICAL ONLY: local paths removed; use ../verify_evidence.py for offline replay.
import cv2,json,numpy as np
from pathlib import Path
from PIL import Image,ImageDraw
r=Path('[LOCAL_PATH_OMITTED]');a=np.asarray(Image.open(r/'doc2-date-native-composite.png').convert('RGB'),dtype=float)
# Proposal mask only; OCR will receive unchanged original pixels.
mask=(a.max(2)<160)&((a.max(2)-a.min(2))<70)
n,l,stats,cent=cv2.connectedComponentsWithStats(mask.astype('uint8'),8)
cc=[dict(bbox=[int(x),int(y),int(x+w),int(y+h)],area=int(area)) for x,y,w,h,area in stats[1:] if area>=20 and h>=10]
out=Image.fromarray(a.astype('uint8'));draw=ImageDraw.Draw(out)
for i,c in enumerate(cc):draw.rectangle(c['bbox'],outline='blue',width=1);draw.text(c['bbox'][:2],str(i),fill='blue')
out.save(r/'components.png');(r/'components.json').write_text(json.dumps(cc,indent=2));print(cc)
