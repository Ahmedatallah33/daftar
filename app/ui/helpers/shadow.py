from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsDropShadowEffect


def add_shadow(widget, blur: int = 24, y_offset: int = 4, opacity: int = 28):
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setXOffset(0)
    effect.setYOffset(y_offset)
    color = QColor(15, 23, 42, opacity)
    effect.setColor(color)
    widget.setGraphicsEffect(effect)
    return effect


def add_soft_shadow(widget):
    return add_shadow(widget, blur=16, y_offset=2, opacity=18)


def add_strong_shadow(widget):
    return add_shadow(widget, blur=36, y_offset=10, opacity=45)
