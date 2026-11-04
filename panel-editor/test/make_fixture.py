"""Generates test/fixtures/sample.ai: a small PDF-compatible file exercising paths, bezier curves,
dashes, linear + radial gradients, clipping, an image, text and a stroked+filled shape.
(Made with reportlab, i.e. independently of Panel Editor's own exporter.)"""
from reportlab.pdfgen import canvas
from reportlab.lib.colors import Color, HexColor
from reportlab.lib.utils import ImageReader
from PIL import Image
import io, sys

out = sys.argv[1] if len(sys.argv) > 1 else 'test/fixtures/sample.ai'
c = canvas.Canvas(out, pagesize=(400, 300))
c.setFillColor(HexColor('#2255cc')); c.rect(20, 200, 100, 60, stroke=0, fill=1)
c.setStrokeColor(HexColor('#cc2222')); c.setLineWidth(4); c.setDash(6, 3); c.circle(200, 230, 30, stroke=1, fill=0)
c.setDash(); c.setFillColor(HexColor('#22aa44')); c.setStrokeColor(HexColor('#000000')); c.setLineWidth(2)
p = c.beginPath(); p.moveTo(250, 200); p.curveTo(280, 280, 340, 280, 370, 200); p.close(); c.drawPath(p, stroke=1, fill=1)
c.saveState(); p = c.beginPath(); p.rect(20, 120, 120, 60); c.clipPath(p, stroke=0, fill=0)
c.linearGradient(20, 120, 140, 120, (HexColor('#ff0000'), HexColor('#0000ff')), extend=True); c.restoreState()
c.saveState(); p = c.beginPath(); p.rect(160, 100, 100, 60); c.clipPath(p, stroke=0, fill=0)
c.radialGradient(210, 130, 60, (HexColor('#ffee00'), HexColor('#aa00aa')), extend=True); c.restoreState()
im = Image.new('RGB', (4, 4)); 
for x in range(4):
    for y in range(4): im.putpixel((x, y), (x * 60, y * 60, 128))
c.drawImage(ImageReader(im), 290, 100, 80, 60, mask=None)
c.setFillColor(HexColor('#000000')); c.setFont('Helvetica-Bold', 18); c.drawString(20, 60, 'Panel A: Test label')
c.setFont('Times-Italic', 12); c.drawString(20, 40, 'Italic serif text, 12 pt')
c.save()
print('wrote', out)
