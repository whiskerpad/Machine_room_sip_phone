# schematic.py - docs/schematic.png / .svg を生成する（pip install schemdraw、フォント Noto Sans CJK JP が必要）
import schemdraw, schemdraw.elements as elm
from matplotlib import font_manager
for f in font_manager.findSystemFonts():
    if 'NotoSansCJK-Regular' in f: font_manager.fontManager.addfont(f)
schemdraw.config(font='Noto Sans CJK JP', fontsize=10.5, lw=1.2)
S=1.2
L=[('1','RESET'),('2','PD0 RXD'),('3','PD1 TXD'),('4','PD2'),('5','PD3'),('6','PD4'),('7','VCC'),('8','GND'),('9','XTAL1'),('10','XTAL2'),('11','PD5'),('12','PD6'),('13','PD7'),('14','PB0')]
R=[('28','PC5'),('27','PC4'),('26','PC3'),('25','PC2'),('24','PC1'),('23','PC0'),('22','GND'),('21','AREF'),('20','AVCC'),('19','PB5 SCK'),('18','PB4 MISO'),('17','PB3 MOSI'),('16','PB2'),('15','PB1')]
pins=[elm.IcPin(name=n,pin=p,side='left',anchorname='p'+p) for p,n in reversed(L)]+[elm.IcPin(name=n,pin=p,side='right',anchorname='p'+p) for p,n in reversed(R)]
d=schemdraw.Drawing(show=False)
ic=elm.Ic(pins=pins,pinspacing=S,edgepadW=2.2,edgepadH=0.6,leadlen=0.7)
d+=ic.at((0,0))
P=lambda n: ic.absanchors['p'+str(n)]
cx=(P(1)[0]+P(28)[0])/2
d+=elm.Label().at((cx+1.5,P(1)[1]+2.3)).label('ATmega328P-PU（DIP-28）',fontsize=13)
d+=elm.Label().at((cx+1.5,P(1)[1]+1.75)).label('UB-ARD03 上に実装 / 5V・16MHz（MiniCore: External 16MHz）',fontsize=9,color='#555')

def net(pt,dx,text,color='#1a4fa0'):
    l=elm.Line().at(pt).right(dx) if dx>0 else elm.Line().at(pt).left(-dx)
    d.add(l)
    d.add(elm.Label().at(l.end).label(text,loc='right' if dx>0 else 'left',color=color,fontsize=10,halign='left' if dx>0 else 'right'))
    return l

# ---------- 左側 ----------
# 1 RESET: 10kΩ プルアップ
l=elm.Line().at(P(1)).left(1.2); d+=l
d+=elm.Dot().at(l.end)
r=elm.Resistor().at(l.end).up(2.0).label('10kΩ',loc='top'); d+=r
d+=elm.Vdd().at(r.end).label('5V')
l2=elm.Line().at(l.end).left(1.2); d+=l2
d+=elm.Label().at(l2.end).label('ISP: RESET',loc='left',halign='right',color='#1a4fa0')

# Pi Zero W
pi=elm.Ic(pins=[elm.IcPin(name='TXD GPIO14',pin='8',side='right',anchorname='TX'),
                elm.IcPin(name='RXD GPIO15',pin='10',side='right',anchorname='RX'),
                elm.IcPin(name='5V',pin='2',side='right',anchorname='V5'),
                elm.IcPin(name='GND',pin='6',side='right',anchorname='G')][::-1],
          pinspacing=S,edgepadW=1.6,edgepadH=0.5,leadlen=0.7)
d+=pi.right().anchor('TX').at((P(2)[0]-13.5,P(2)[1]))
d+=elm.Label().at((pi.absanchors['TX'][0]-1.9,pi.absanchors['TX'][1]+1.4)).label('Raspberry Pi Zero W\n（40ピン ヘッダ）',fontsize=11)
# Pi TXD -> 328P RXD 直結
d+=elm.Line().at(pi.absanchors['TX']).to(P(2))
d+=elm.Label().at(((pi.absanchors['TX'][0]+P(2)[0])/2,P(2)[1]+0.25)).label('直結（3.3V で 5V 側は High 判定）',fontsize=8.5,color='#555')
# 328P TXD -> 2.2k -> node -> Pi RXD ; node -> 3.3k -> GND
xn=P(3)[0]-7.0
l=elm.Line().at(P(3)).tox(P(3)[0]-1.5); d+=l
r1=elm.Resistor().at(l.end).tox(xn).label('R1 2.2kΩ'); d+=r1
d+=elm.Dot().at(r1.end)
d+=elm.Line().at(r1.end).to(pi.absanchors['RX'])
r2=elm.Resistor().at(r1.end).down(2.4).label('R2 3.3kΩ',loc='bottom'); d+=r2
d+=elm.Ground().at(r2.end)
d+=elm.Label().at((xn-1.6,P(3)[1]+0.25)).label('約3.0V',fontsize=8.5,color='#555')
# Pi 5V, GND
l=elm.Line().at(pi.absanchors['V5']).right(1.2); d+=l
d+=elm.Vdd().at(l.end).label('5V')
l=elm.Line().at(pi.absanchors['G']).right(1.2); d+=l
d+=elm.Ground().at(l.end)
d+=elm.Label().at((pi.absanchors['G'][0]-1.9,pi.absanchors['G'][1]-1.4)).label('Pi の電源: micro-USB 5V 2A 以上\n基板の 5V はここから供給（UNO の Vin は使わない）',fontsize=8.5,color='#555')

# PD2..PD4 -> D2..D4
for p,t in [(4,'D2  LCD D2'),(5,'D3  LCD D3'),(6,'D4  LCD D4')]: net(P(p),-1.2,t)
# 7 VCC
l=elm.Line().at(P(7)).left(1.2); d+=l
d+=elm.Dot().at(l.end)
d+=elm.Vdd().at(l.end).label('5V')
l2=elm.Line().at(l.end).left(2.6); d+=l2
c=elm.Capacitor().at(l2.end).down(S*0.95).label('100nF',loc='bottom'); d+=c
d+=elm.Ground().at(c.end)
# 8 GND
l=elm.Line().at(P(8)).left(0.9); d+=l
d+=elm.Ground().at(l.end)
# 9,10 crystal
xa=P(9)[0]-2.4
a=elm.Line().at(P(9)).tox(xa); d+=a
b=elm.Line().at(P(10)).tox(xa); d+=b
d+=elm.Dot().at(a.end); d+=elm.Dot().at(b.end)
x=elm.Crystal().at(a.end).to(b.end); d+=x
d+=elm.Label().at((xa+0.45,(P(9)[1]+P(10)[1])/2)).label('16MHz',halign='left',fontsize=9.5)
for nd,t in [(a.end,'22pF'),(b.end,'22pF')]:
    ll=elm.Line().at(nd).left(1.6); d+=ll
    cc=elm.Capacitor().at(ll.end).left(1.6).label(t); d+=cc
    d+=elm.Ground().at(cc.end).theta(-90)
for p,t in [(11,'D5  LCD D5'),(12,'D6  LCD D6 / タッチ XP'),(13,'D7  LCD D7 / タッチ YM'),(14,'D8  LCD D0')]: net(P(p),-1.2,t)

# ---------- 右側 ----------
for p,t in [(28,'A5  （未使用）'),(27,'A4  LCD RST'),(26,'A3  LCD CS'),(25,'A2  LCD RS / タッチ XM'),(24,'A1  LCD WR / タッチ YP'),(23,'A0  LCD RD')]: net(P(p),1.2,t)
l=elm.Line().at(P(22)).right(0.9); d+=l
d+=elm.Ground().at(l.end)
for p in (21,20):
    l=elm.Line().at(P(p)).right(1.0); d+=l
    d+=elm.Dot().at(l.end)
    c=elm.Capacitor().at(l.end).right(1.4).label('100nF'); d+=c
    d+=elm.Ground().at(c.end).theta(90)
d+=elm.Label().at((P(20)[0]+1.0,P(20)[1]-0.35)).label('5V',fontsize=10,color='#1a4fa0')
for p,t in [(19,'D13  SD SCK（未使用） / ISP: SCK'),(18,'D12  SD MISO（未使用） / ISP: MISO'),(17,'D11  SD MOSI（未使用） / ISP: MOSI'),(16,'D10  SD CS（未使用）'),(15,'D9  LCD D1')]: net(P(p),1.2,t)

# 凡例
yb=P(14)[1]-2.2
d+=elm.Label().at((P(1)[0]-14,yb)).label(
 '青字 = UB-ARD03 の UNO 互換ヘッダのピン名。TFT シールド（3.95" ILI9488）はこのヘッダに重ねて差す。\n'
 'タッチパネル（4線式抵抗膜）は LCD と D6 / D7 / A1 / A2 を共用。  ISP: 書き込み用（USBasp や Arduino as ISP の MOSI / MISO / SCK / RESET / 5V / GND）。\n'
 '0.1µF（100nF）は 328P のピンのすぐ近くに付ける。シールドが 3.3V ピンを使う場合は、別途 3.3V レギュレーターが必要（テスターで要確認）。',
 halign='left',valign='top',fontsize=9,color='#333')
d.save('schematic.svg'); d.save('schematic.png',dpi=130)  # カレントフォルダに出力
