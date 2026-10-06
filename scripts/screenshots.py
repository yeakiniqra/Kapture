#!/usr/bin/env python3
"""Render the README / website screenshots from the real UI, offscreen, at 2x.

    venv/bin/python scripts/screenshots.py      # writes assets/screenshots/*.png

The "screen" being captured is a mock web page painted here, so the shots are
reproducible and never contain anyone's real desktop.
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "assets", "screenshots")
W, H, DPR = 1440, 900, 2                     # scene size (logical) and export scale

# A HiDPI virtual screen the size of the scene (the overlay goes full-screen on it).
# Qt splits the platform spec on ':' so the path must be relative (no drive letter).
os.chdir(ROOT)
os.makedirs("build", exist_ok=True)
_cfg = os.path.join("build", "shots-screen.json")
with open(_cfg, "w") as f:
    json.dump({"screens": [{"name": "S", "x": 0, "y": 0, "width": W, "height": H,
                            "logicalDpi": 96, "logicalBaseDpi": 96, "dpr": DPR}]}, f)
os.environ["QT_QPA_PLATFORM"] = f"offscreen:configfile={_cfg}"
os.environ["QT_API"] = "pyside6"
sys.path.insert(0, ROOT)

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QSystemTrayIcon  # noqa: E402

app = QApplication(sys.argv)

import kapture.config as config  # noqa: E402
import kapture.theme as th  # noqa: E402
import kapture.ui.editor as editor  # noqa: E402
from kapture import icons  # noqa: E402
from kapture.capture import CaptureResult  # noqa: E402
from kapture.tray import TrayApp  # noqa: E402
from kapture.ui.dialogs import SettingsDialog  # noqa: E402
from kapture.ui.overlay import OverlayWindow  # noqa: E402

th.load_fonts()
app.setFont(th.sans(14))
# Never touch the real user config; show neutral defaults.
editor.save_config = config.save_config = lambda cfg: None
config.CONFIG.update({"color": "#E63232", "stroke": 1, "save_dir": "~/Pictures",
                      "auto_save": False, "capture_binding": "Print"})


def use_theme(mode: str):
    th.is_dark = lambda: mode == "dark"


# ── mock web page (the thing being screenshotted) ────────────────────────────

PAGE_W, PAGE_H = 1200, 720
INK, MUTED, LINE, BLUE = QColor("#101828"), QColor("#667085"), QColor("#E4E7EC"), QColor("#2E6BE6")
CARD_A = QRect(260, 160, 420, 270)              # payment card
CARD_B = QRect(700, 160, 440, 270)              # usage card
REGION = QRect(248, 148, 904, 294)              # what the user selects (page coords)
ROWS = [("Email", "alex.morgan@example.com"), ("Card", "Visa  ••••  4242"),
        ("Address", "221B Baker Street, London"), ("Plan", "Pro  ·  $12 / month")]


def _text(p, x, y, s, px=14, color=INK, medium=False):
    p.setFont(th.sans(px, medium))
    p.setPen(color)
    p.drawText(QPointF(x, y), s)


def paint_page(p: QPainter):
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.fillRect(0, 0, PAGE_W, PAGE_H, QColor("#FFFFFF"))
    # window title bar (Yaru-ish) with address pill
    p.fillRect(0, 0, PAGE_W, 44, QColor("#EBEBED"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#FFFFFF"))
    p.drawRoundedRect(QRectF(PAGE_W / 2 - 220, 9, 440, 26), 13, 13)
    _text(p, PAGE_W / 2 - 200, 27, "app.example.com/settings/billing", 12, MUTED)
    for i, c in enumerate(("#C9C9CE", "#C9C9CE", "#E95420")):        # Ubuntu window buttons
        p.setBrush(QColor(c))
        p.drawEllipse(QPointF(PAGE_W - 76 + i * 24, 22), 7, 7)
    # sidebar
    p.fillRect(0, 44, 220, PAGE_H - 44, QColor("#F7F8FA"))
    _text(p, 28, 92, "Northwind", 18, INK, True)
    for i, item in enumerate(("Overview", "Projects", "Team", "Billing", "Settings")):
        y = 132 + i * 40
        if item == "Billing":
            p.setBrush(QColor("#E8EEFC"))
            p.drawRoundedRect(QRectF(16, y - 24, 188, 34), 8, 8)
        _text(p, 32, y, item, 14, BLUE if item == "Billing" else MUTED, item == "Billing")
    # heading
    _text(p, 260, 104, "Billing", 28, INK, True)
    _text(p, 260, 132, "Manage your plan, payment details and invoices.", 14, MUTED)

    def card(r: QRect, title: str):
        p.setPen(QPen(LINE, 1))
        p.setBrush(QColor("#FFFFFF"))
        p.drawRoundedRect(QRectF(r), 12, 12)
        _text(p, r.x() + 24, r.y() + 38, title, 16, INK, True)

    card(CARD_A, "Payment method")
    for i, (k, v) in enumerate(ROWS):
        y = CARD_A.y() + 78 + i * 36
        _text(p, CARD_A.x() + 24, y, k, 13, MUTED)
        _text(p, CARD_A.x() + 120, y, v, 14)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(BLUE)
    p.drawRoundedRect(QRectF(update_btn()), 18, 18)
    _text(p, update_btn().x() + 22, update_btn().y() + 23, "Update plan", 14, QColor("#FFFFFF"), True)

    card(CARD_B, "Usage this week")
    _text(p, CARD_B.x() + 24, CARD_B.y() + 92, "18.4 GB", 34, INK, True)
    _text(p, CARD_B.x() + 160, CARD_B.y() + 92, "of 50 GB", 14, MUTED)
    for i, (d, h) in enumerate(zip("MTWTFSS", (46, 70, 58, 96, 80, 34, 52))):
        x = CARD_B.x() + 28 + i * 56
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(BLUE if i == 3 else QColor("#C9D7F8"))
        p.drawRoundedRect(QRectF(x, CARD_B.bottom() - 44 - h, 32, h), 5, 5)
        _text(p, x + 11, CARD_B.bottom() - 18, d, 12, MUTED)

    # invoices
    inv = QRect(260, 450, 880, 230)
    card(inv, "Invoices")
    for i, (date, amt) in enumerate((("Oct 1, 2026", "$12.00"), ("Sep 1, 2026", "$12.00"),
                                     ("Aug 1, 2026", "$12.00"))):
        y = inv.y() + 86 + i * 44
        p.fillRect(inv.x() + 24, y - 28, inv.width() - 48, 1, LINE)
        _text(p, inv.x() + 24, y, date, 14)
        _text(p, inv.x() + 360, y, amt, 14)
        p.setBrush(QColor("#E3F4E8"))
        p.drawRoundedRect(QRectF(inv.x() + 640, y - 18, 52, 24), 12, 12)
        _text(p, inv.x() + 652, y - 1, "Paid", 12, QColor("#1E7B3A"), True)


def update_btn() -> QRect:
    return QRect(CARD_A.x() + 24, CARD_A.bottom() - 52, 132, 36)


def page_pixmap() -> QPixmap:
    img = QImage(PAGE_W * DPR, PAGE_H * DPR, QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(DPR)
    p = QPainter(img)
    paint_page(p)
    p.end()
    return QPixmap.fromImage(img)


# ── scene helpers ────────────────────────────────────────────────────────────

def canvas(w=W, h=H, bg=th.DARK["bg"]) -> tuple:
    """A desktop backdrop: flat colour + Nothing-style dot grid."""
    img = QImage(w * DPR, h * DPR, QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(DPR)
    img.fill(QColor(bg))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    dot = QColor(th.DARK["border"] if bg == th.DARK["bg"] else th.LIGHT["border"])
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(dot)
    for x in range(12, w, 24):
        for y in range(12, h, 24):
            p.drawEllipse(QPointF(x, y), 1, 1)
    return img, p


def top_bar(p: QPainter, w=W):
    """GNOME-style top bar with the Kapture tray glyph."""
    p.fillRect(0, 0, w, 32, QColor("#0B0A10"))
    p.setFont(th.sans(13, True))
    p.setPen(QColor("#FFFFFF"))
    p.drawText(QRect(0, 0, w, 32), Qt.AlignmentFlag.AlignCenter, "Mon 5 Oct  14:32")
    p.drawPixmap(QRectF(w - 116, 7, 18, 18), icons.pixmap("kapture-symbolic", "#FFFFFF", 18, 2), QRectF())
    for i, name in enumerate(("monitor", "power")):
        p.drawPixmap(QRectF(w - 80 + i * 32, 8, 16, 16), icons.pixmap(name, "#FFFFFF", 16), QRectF())


PAGE_AT, PAGE_SCALE = QPoint(64, 72), 0.86


def draw_page_window(p: QPainter, page: QPixmap):
    r = QRectF(PAGE_AT.x(), PAGE_AT.y(), PAGE_W * PAGE_SCALE, PAGE_H * PAGE_SCALE)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.drawPixmap(r, page, QRectF(page.rect()))
    p.setPen(QPen(QColor("#00000040"), 1))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRect(r)


def grab(widget) -> QPixmap:
    app.processEvents()
    return widget.grab()


def save(img: QImage, name: str):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name)
    img.save(path, "PNG")
    print(f"{path}  {img.width()}x{img.height()}  {os.path.getsize(path) // 1024} KB")


# ── the annotated editor ─────────────────────────────────────────────────────

def annotated_editor(page: QPixmap) -> 'editor.AnnotationWindow':
    crop = page.copy(QRect(REGION.x() * DPR, REGION.y() * DPR, REGION.width() * DPR,
                           REGION.height() * DPR))
    crop.setDevicePixelRatio(DPR)
    ed = editor.AnnotationWindow(crop, REGION)
    o = REGION.topLeft()

    def at(x, y):                                   # page coords → image coords
        return QPoint(x - o.x(), y - o.y())

    def drag(tool, *pts):
        ed.select_tool(tool)
        ed.press(at(*pts[0]))
        for q in pts[1:]:
            ed.drag_to(at(*q))
        ed.release(at(*pts[-1]))

    row_y = lambda i: CARD_A.y() + 78 + i * 36      # noqa: E731 — baseline of ROWS[i]
    vx = CARD_A.x() + 116
    drag("blur", (vx, row_y(0) - 18), (vx + 196, row_y(0) + 8))          # email
    drag("blur", (vx, row_y(2) - 18), (vx + 208, row_y(2) + 8))          # address
    drag("marker", (vx + 2, row_y(3) - 5), (vx + 152, row_y(3) - 5))     # plan
    btn = update_btn()
    drag("arrow", (btn.right() + 150, btn.center().y() + 4), (btn.right() + 12, btn.center().y()))
    drag("rect", (CARD_B.x() + 14, CARD_B.y() + 54), (CARD_B.x() + 268, CARD_B.y() + 106))
    for n, (x, y) in enumerate(((CARD_A.right() - 34, row_y(0) - 6),
                                (CARD_A.right() - 34, row_y(3) - 6),
                                (CARD_B.x() + 282, CARD_B.y() + 64))):
        drag("step", (x, y), (x, y))
    ed.select_tool("text")
    ed.press(at(btn.right() + 158, btn.center().y() + 2))
    ed._text.setText("Upgrade here")
    ed.commit_text()
    ed.select_tool("arrow")
    return ed


# ── shots ────────────────────────────────────────────────────────────────────

def hero(page):
    use_theme("dark")
    ed = annotated_editor(page)
    shot = grab(ed)
    img, p = canvas()
    top_bar(p)
    draw_page_window(p, page)
    p.drawPixmap(W - ed.width() - 56, H - ed.height() - 48, shot)
    p.end()
    save(img, "hero.png")
    ed.close()


def overlay(page):
    img, p = canvas()
    top_bar(p)
    draw_page_window(p, page)
    p.end()
    screen = QPixmap.fromImage(img)
    ov = OverlayWindow(CaptureResult(screen, QRect(0, 0, W, H), DPR))
    ov.resize(W, H)
    sel = QRect(PAGE_AT + REGION.topLeft() * PAGE_SCALE, REGION.size() * PAGE_SCALE)
    ov.is_drawing, ov.selection = True, sel
    shot = QPixmap(W * DPR, H * DPR)         # full-screen grab() comes back at 1x
    shot.setDevicePixelRatio(DPR)
    app.processEvents()
    ov.render(shot)
    save(shot.toImage(), "overlay.png")
    ov.close()


def editors(page):
    for mode in ("dark", "light"):
        use_theme(mode)
        ed = annotated_editor(page)
        shot = grab(ed)
        pad = 48
        img, p = canvas(ed.width() + 2 * pad, ed.height() + 2 * pad,
                        th.DARK["bg"] if mode == "dark" else th.LIGHT["bg"])
        p.drawPixmap(pad, pad, shot)
        p.end()
        save(img, f"editor-{mode}.png")
        ed.close()


def tray_and_settings():
    use_theme("dark")
    tray = TrayApp.__new__(TrayApp)          # menu only: skip hotkeys / GNOME setup
    QSystemTrayIcon.__init__(tray)
    tray.app = app
    tray._setup_menu()
    menu = tray._menu
    menu.setActiveAction(menu.actions()[0])
    menu.show()
    menu_shot = grab(menu)
    dlg = SettingsDialog()
    dlg.show()
    dlg_shot = grab(dlg)

    w = 64 + dlg.width() + 48 + menu.width() + 64
    h = 32 + 56 + max(dlg.height(), menu.height()) + 56
    img, p = canvas(w, h)
    top_bar(p, w)
    p.drawPixmap(64, 88, dlg_shot)
    p.drawPixmap(w - menu.width() - 64, 40, menu_shot)
    p.end()
    save(img, "tray-settings.png")


if __name__ == "__main__":
    page = page_pixmap()
    hero(page)
    overlay(page)
    editors(page)
    tray_and_settings()
