"""Application icons as PNG bytes, for the browser picker.

Tk cannot read an .icns, and an app's icon is not always at a predictable path
inside the bundle, so the icon is asked for through NSWorkspace and redrawn into
a bitmap of exactly the size wanted. Drawing into an explicitly sized bitmap
rather than resizing the NSImage keeps Retina backing scale out of it: the PNG
comes back the number of pixels asked for, which is what Tk will render it as.

Every failure returns None; an icon is decoration, and the picker still works
as a plain list without one.
"""

from __future__ import annotations

import base64

DEFAULT_SIZE = 20


def icon_png(app_path: str, size: int = DEFAULT_SIZE) -> bytes | None:
    """PNG bytes for an app bundle's icon, or None."""
    if not app_path:
        return None
    try:
        from AppKit import (
            NSBitmapImageRep,
            NSCompositingOperationSourceOver,
            NSDeviceRGBColorSpace,
            NSGraphicsContext,
            NSWorkspace,
        )
    except Exception:
        return None
    try:
        image = NSWorkspace.sharedWorkspace().iconForFile_(app_path)
        if image is None:
            return None
        rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
            None, size, size, 8, 4, True, False, NSDeviceRGBColorSpace, 0, 0
        )
        if rep is None:
            return None
        context = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
        if context is None:
            return None
        NSGraphicsContext.saveGraphicsState()
        try:
            NSGraphicsContext.setCurrentContext_(context)
            image.drawInRect_fromRect_operation_fraction_(
                ((0.0, 0.0), (float(size), float(size))),
                ((0.0, 0.0), (0.0, 0.0)),
                NSCompositingOperationSourceOver,
                1.0,
            )
        finally:
            NSGraphicsContext.restoreGraphicsState()
        # NSBitmapImageFileTypePNG
        data = rep.representationUsingType_properties_(4, {})
        return bytes(data) if data is not None else None
    except Exception:
        return None


def icon_base64(app_path: str, size: int = DEFAULT_SIZE) -> str | None:
    """Icon as base64 PNG, the form tkinter.PhotoImage(data=...) accepts."""
    png = icon_png(app_path, size)
    return base64.b64encode(png).decode("ascii") if png else None
