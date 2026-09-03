"""The floating always-on-top check-in window, built with AppKit.

Using an NSPanel from inside the rumps process avoids the run-loop clash that
Tkinter would cause. The panel joins all Spaces and sits above full-screen apps
so a check-in cannot be silently missed.
"""

from __future__ import annotations

import objc
from AppKit import (
    NSApp,
    NSBackingStoreBuffered,
    NSBeep,
    NSButton,
    NSColor,
    NSFont,
    NSMakeRect,
    NSPanel,
    NSScreen,
    NSStatusWindowLevel,
    NSTextField,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowStyleMaskHUDWindow,
    NSWindowStyleMaskTitled,
    NSWindowStyleMaskUtilityWindow,
)
from Foundation import NSObject

WIDTH = 420.0
HEIGHT = 168.0


class _Target(NSObject):
    """Button action target. PyObjC needs a real ObjC object for this."""

    def initWithCallback_(self, callback):
        self = objc.super(_Target, self).init()
        if self is None:
            return None
        self._callback = callback
        return self

    def yes_(self, sender):
        self._callback(True)

    def no_(self, sender):
        self._callback(False)


class PromptWindow:
    def __init__(self, on_answer):
        self._target = _Target.alloc().initWithCallback_(on_answer)
        self._panel = None
        self._title = None
        self._subtitle = None

    def _build(self) -> None:
        if self._panel is not None:
            return
        style = NSWindowStyleMaskTitled | NSWindowStyleMaskUtilityWindow | NSWindowStyleMaskHUDWindow
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, WIDTH, HEIGHT), style, NSBackingStoreBuffered, False
        )
        panel.setTitle_("OnTask")
        panel.setLevel_(NSStatusWindowLevel)
        panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        panel.setHidesOnDeactivate_(False)
        panel.setBecomesKeyOnlyIfNeeded_(False)
        content = panel.contentView()

        title = NSTextField.alloc().initWithFrame_(NSMakeRect(24, HEIGHT - 66, WIDTH - 48, 26))
        _style_label(title, NSFont.boldSystemFontOfSize_(17))
        content.addSubview_(title)

        subtitle = NSTextField.alloc().initWithFrame_(NSMakeRect(24, HEIGHT - 94, WIDTH - 48, 22))
        _style_label(subtitle, NSFont.systemFontOfSize_(12))
        subtitle.setTextColor_(NSColor.secondaryLabelColor())
        content.addSubview_(subtitle)

        no = NSButton.alloc().initWithFrame_(NSMakeRect(WIDTH - 250, 24, 105, 32))
        no.setTitle_("No (N)")
        no.setBezelStyle_(1)
        no.setKeyEquivalent_("n")
        no.setTarget_(self._target)
        no.setAction_("no:")
        content.addSubview_(no)

        yes = NSButton.alloc().initWithFrame_(NSMakeRect(WIDTH - 135, 24, 111, 32))
        yes.setTitle_("Yes (Y)")
        yes.setBezelStyle_(1)
        yes.setKeyEquivalent_("y")
        yes.setTarget_(self._target)
        yes.setAction_("yes:")
        content.addSubview_(yes)

        self._panel = panel
        self._title = title
        self._subtitle = subtitle

    def show(self, question: str, subtitle: str, play_sound: bool = True) -> None:
        self._build()
        self._title.setStringValue_(question)
        self._subtitle.setStringValue_(subtitle)
        self._center()
        NSApp.activateIgnoringOtherApps_(True)
        self._panel.makeKeyAndOrderFront_(None)
        if play_sound:
            NSBeep()

    def realert(self, subtitle: str | None = None, play_sound: bool = True) -> None:
        if self._panel is None:
            return
        if subtitle is not None:
            self._subtitle.setStringValue_(subtitle)
        self._center()
        NSApp.activateIgnoringOtherApps_(True)
        self._panel.makeKeyAndOrderFront_(None)
        if play_sound:
            NSBeep()

    def close(self) -> None:
        if self._panel is not None:
            self._panel.orderOut_(None)

    @property
    def visible(self) -> bool:
        return self._panel is not None and bool(self._panel.isVisible())

    def _center(self) -> None:
        screen = NSScreen.mainScreen()
        if screen is None:
            return
        frame = screen.visibleFrame()
        x = frame.origin.x + (frame.size.width - WIDTH) / 2.0
        # Slightly above centre reads better than dead centre.
        y = frame.origin.y + (frame.size.height - HEIGHT) * 0.62
        self._panel.setFrameOrigin_((x, y))


def _style_label(field, font) -> None:
    field.setBezeled_(False)
    field.setDrawsBackground_(False)
    field.setEditable_(False)
    field.setSelectable_(False)
    field.setFont_(font)
