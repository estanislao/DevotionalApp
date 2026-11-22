"""Gera os ícones do PWA a partir da mesma geometria de app/static/logo.svg (viewBox 540x270).

Traço e folhas são mais grossos que no logo para o ícone ler bem em tamanho pequeno.
Uso (precisa de Pillow, que não é dependência do app):  python3 tools/gera_icones.py app/static/icons
"""
import math, sys
from PIL import Image, ImageDraw

LARANJA, INDIGO, FUNDO = (227, 155, 102), (46, 49, 146), (255, 255, 255)
VB_W, VB_H, TRACO = 540, 270, 32

def bezier(p0, p1, p2, p3, n=220):
    for i in range(n + 1):
        t = i / n; u = 1 - t
        yield (u**3*p0[0] + 3*u*u*t*p1[0] + 3*u*t*t*p2[0] + t**3*p3[0],
               u**3*p0[1] + 3*u*u*t*p1[1] + 3*u*t*t*p2[1] + t**3*p3[1])

def linha(a, b, n=40):
    for i in range(n + 1):
        t = i / n
        yield (a[0] + (b[0]-a[0])*t, a[1] + (b[1]-a[1])*t)

def elipse(cx, cy, rx, ry, graus, n=160):
    r = math.radians(graus)
    return [(cx + rx*math.cos(a)*math.cos(r) - ry*math.sin(a)*math.sin(r),
             cy + rx*math.cos(a)*math.sin(r) + ry*math.sin(a)*math.cos(r))
            for a in (2*math.pi*i/n for i in range(n))]

# (cor, pontos do traço, folha) — mesma ordem de pintura do SVG: laranja atrás, índigo na frente
CAMADAS = [
    (LARANJA,
     list(bezier((18,158),(105,62),(268,58),(348,162))) + list(bezier((348,162),(388,214),(432,190),(462,178))) + list(linha((452,180),(478,170))),
     elipse(505, 157, 44, 24, -14)),
    (INDIGO,
     list(bezier((18,158),(105,254),(268,254),(348,148))) + list(bezier((348,148),(392,92),(420,78),(452,60))) + list(linha((446,64),(470,48))),
     elipse(492, 34, 42, 24, -40)),
]

def gerar(tamanho, destino, ocupacao=0.68, ss=4):
    T = tamanho * ss
    img = Image.new("RGB", (T, T), FUNDO)
    d = ImageDraw.Draw(img)
    escala = T * ocupacao / VB_W
    # caixa real do desenho no viewBox (x 7..541, y ~14..232) para centralizar de verdade
    minx, maxx, miny, maxy = 2, 549, 4, 238
    ox = (T - (maxx - minx) * escala) / 2 - minx * escala
    oy = (T - (maxy - miny) * escala) / 2 - miny * escala
    P = lambda p: (ox + p[0]*escala, oy + p[1]*escala)
    r = TRACO * escala / 2
    for cor, pontos, folha in CAMADAS:
        for p in pontos:
            x, y = P(p)
            d.ellipse((x-r, y-r, x+r, y+r), fill=cor)
        d.polygon([P(p) for p in folha], fill=cor)
    img.resize((tamanho, tamanho), Image.LANCZOS).save(destino, optimize=True)

saida = sys.argv[1]
gerar(192, f"{saida}/icon-192.png")
gerar(512, f"{saida}/icon-512.png")
gerar(180, f"{saida}/apple-touch-icon.png", ocupacao=0.74)
gerar(32, f"{saida}/favicon-32.png", ocupacao=0.9, ss=8)
print("ok")
