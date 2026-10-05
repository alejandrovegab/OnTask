"""The floating always-on-top check-in window, built with AppKit.

Using an NSPanel from inside the rumps process avoids the run-loop clash that
Tkinter would cause. The panel joins all Spaces and sits above full-screen apps
so a check-in cannot be silently missed.

Three details matter more than they look:

* The panel is laid out from measured text, not fixed rectangles, so a long
  question about a long domain name wraps instead of being clipped.
* Showing it has to steal focus for the Y and N keys to work, so the app that
  was in front is remembered and put back afterwards. Answering a check-in
  should not cost you your place.
* An answer is acknowledged before the panel goes away: the chosen button
  highlights for a moment, optionally with a sound. Hiding instantly reads as a
  glitch rather than as confirmation.
"""

from __future__ import annotations

import math

import objc
from AppKit import (
    NSApp,
    NSBackingStoreBuffered,
    NSBeep,
    NSButton,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSLineBreakByWordWrapping,
    NSMakeRect,
    NSPanel,
    NSScreen,
    NSSound,
    NSStatusWindowLevel,
    NSStringDrawingUsesLineFragmentOrigin,
    NSTextField,
    NSTimer,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowStyleMaskTitled,
    NSWindowStyleMaskUtilityWindow,
    NSWorkspace,
)
from Foundation import NSAttributedString, NSObject

WIDTH = 460.0
PAD = 22.0
BUTTON_HEIGHT = 32.0
BUTTON_WIDTH = 112.0
BUTTON_GAP = 10.0
TITLE_GAP = 8.0
BODY_GAP = 18.0
MIN_HEIGHT = 150.0

# Long enough to register as confirmation, short enough to still feel instant.
FEEDBACK_SECONDS = 0.18

ANSWER_SOUNDS = {True: "Tink", False: "Pop"}


class _Target(NSObject):
    """Button and timer target. PyObjC needs a real ObjC object for these."""

    def initWithWindow_(self, window):
        self = objc.super(_Target, self).init()
        if self is None:
            return None
        self._window = window
        return self

    def yes_(self, sender):
        self._window.button_pressed(True)

    def no_(self, sender):
        self._window.button_pressed(False)

    def hideNow_(self, timer):
        self._window.hide_now()


class PromptWindow:
    def __init__(self, on_answer):
        self._on_answer = on_answer
        self._target = _Target.alloc().initWithWindow_(self)
        self._panel = None
        self._title = None
        self._subtitle = None
        self._yes = None
        self._no = None
        # The app to hand focus back to once the check-in is done with.
        self._previous_app = None
        # True between an answer and the panel actually going away.
        self._hide_pending = False

    # -- construction -----------------------------------------------------

    def _build(self) -> None:
        if self._panel is not None:
            return
        # Deliberately not a HUD window: that style forces a dark vibrant
        # material regardless of the system theme, which is what made the panel
        # look washed out. A plain utility panel follows light and dark
        # properly and gives the labels real contrast.
        style = NSWindowStyleMaskTitled | NSWindowStyleMaskUtilityWindow
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, WIDTH, MIN_HEIGHT), style, NSBackingStoreBuffered, False
        )
        panel.setTitle_("OnTask")
        panel.setLevel_(NSStatusWindowLevel)
        panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        panel.setHidesOnDeactivate_(False)
        panel.setBecomesKeyOnlyIfNeeded_(False)
        content = panel.contentView()

        title = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, WIDTH - PAD * 2, 22))
        _style_label(title, NSFont.boldSystemFontOfSize_(16))
        title.setTextColor_(NSColor.labelColor())
        content.addSubview_(title)

        subtitle = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, WIDTH - PAD * 2, 18))
        _style_label(subtitle, NSFont.systemFontOfSize_(12))
        subtitle.setTextColor_(NSColor.secondaryLabelColor())
        content.addSubview_(subtitle)

        no = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, BUTTON_WIDTH, BUTTON_HEIGHT))
        no.setTitle_("No (N)")
        no.setBezelStyle_(1)
        no.setKeyEquivalent_("n")
        no.setTarget_(self._target)
        no.setAction_("no:")
        content.addSubview_(no)

        yes = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, BUTTON_WIDTH, BUTTON_HEIGHT))
        yes.setTitle_("Yes (Y)")
        yes.setBezelStyle_(1)
        yes.setKeyEquivalent_("y")
        yes.setTarget_(self._target)
        yes.setAction_("yes:")
        content.addSubview_(yes)

        self._panel = panel
        self._title = title
        self._subtitle = subtitle
        self._yes = yes
        self._no = no

    # -- layout -----------------------------------------------------------

    def _layout(self, question: str, subtitle: str) -> None:
        """Size the panel around the text instead of clipping the text to it."""
        text_width = WIDTH - PAD * 2
        title_height = max(
            22.0, _text_height(question, NSFont.boldSystemFontOfSize_(16), text_width)
        )
        subtitle_height = max(
            18.0, _text_height(subtitle, NSFont.systemFontOfSize_(12), text_width)
        )
        height = max(
            MIN_HEIGHT,
            PAD + BUTTON_HEIGHT + BODY_GAP + subtitle_height + TITLE_GAP + title_height + PAD,
        )

        self._panel.setContentSize_((WIDTH, height))
        subtitle_y = PAD + BUTTON_HEIGHT + BODY_GAP
        title_y = subtitle_y + subtitle_height + TITLE_GAP
        self._title.setFrame_(NSMakeRect(PAD, title_y, text_width, title_height))
        self._subtitle.setFrame_(NSMakeRect(PAD, subtitle_y, text_width, subtitle_height))
        self._no.setFrame_(
            NSMakeRect(
                WIDTH - PAD - BUTTON_WIDTH * 2 - BUTTON_GAP, PAD, BUTTON_WIDTH, BUTTON_HEIGHT
            )
        )
        self._yes.setFrame_(
            NSMakeRect(WIDTH - PAD - BUTTON_WIDTH, PAD, BUTTON_WIDTH, BUTTON_HEIGHT)
        )

    def _position(self, placement: str) -> None:
        screen = NSScreen.mainScreen()
        if screen is None:
            return
        frame = screen.visibleFrame()
        size = self._panel.frame().size
        margin = 24.0
        left = frame.origin.x + margin
        right = frame.origin.x + frame.size.width - size.width - margin
        middle_x = frame.origin.x + (frame.size.width - size.width) / 2.0
        bottom = frame.origin.y + margin
        top = frame.origin.y + frame.size.height - size.height - margin
        middle_y = frame.origin.y + (frame.size.height - size.height) / 2.0
        spots = {
            "center": (middle_x, middle_y),
            "top_left": (left, top),
            "top_center": (middle_x, top),
            "top_right": (right, top),
            "bottom_left": (left, bottom),
            "bottom_center": (middle_x, bottom),
            "bottom_right": (right, bottom),
        }
        self._panel.setFrameOrigin_(spots.get(placement, spots["center"]))

    # -- showing and hiding -----------------------------------------------

    def show(
        self,
        question: str,
        subtitle: str,
        play_sound: bool = True,
        position: str = "center",
    ) -> None:
        self._build()
        self._hide_pending = False
        self._clear_feedback()
        self._remember_front_app()
        self._title.setStringValue_(question)
        self._subtitle.setStringValue_(subtitle)
        self._layout(question, subtitle)
        self._position(position)
        NSApp.activateIgnoringOtherApps_(True)
        self._panel.makeKeyAndOrderFront_(None)
        if play_sound:
            NSBeep()

    def realert(
        self,
        subtitle: str | None = None,
        play_sound: bool = True,
        position: str = "center",
    ) -> None:
        if self._panel is None:
            return
        if subtitle is not None:
            self._subtitle.setStringValue_(subtitle)
            self._layout(str(self._title.stringValue()), subtitle)
        self._position(position)
        NSApp.activateIgnoringOtherApps_(True)
        self._panel.makeKeyAndOrderFront_(None)
        if play_sound:
            NSBeep()

    def close(self) -> None:
        """Hide, unless an answer is still being acknowledged."""
        if self._hide_pending:
            return
        self.hide_now()

    def hide_now(self) -> None:
        self._hide_pending = False
        self._clear_feedback()
        if self._panel is not None:
            self._panel.orderOut_(None)
        self._restore_front_app()

    @property
    def visible(self) -> bool:
        return self._panel is not None and bool(self._panel.isVisible())

    # -- answering --------------------------------------------------------

    def button_pressed(self, yes: bool) -> None:
        self._on_answer(yes)

    def flash_answer(self, yes: bool, play_sound: bool = False) -> None:
        """Acknowledge an answer, then hide shortly after.

        Called for every route into an answer - button, hotkey, notification -
        so a keyboard answer confirms itself as clearly as a click does.
        """
        if self._panel is None or not self.visible:
            return
        button = self._yes if yes else self._no
        if button is not None:
            button.setHighlighted_(True)
        if play_sound:
            _play(ANSWER_SOUNDS.get(bool(yes), "Pop"))
        self._hide_pending = True
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            FEEDBACK_SECONDS, self._target, "hideNow:", None, False
        )

    def _clear_feedback(self) -> None:
        for button in (self._yes, self._no):
            if button is not None:
                button.setHighlighted_(False)

    # -- focus handover ---------------------------------------------------

    def _remember_front_app(self) -> None:
        if self.visible:
            # Already showing: whatever is in front now is us, not the app the
            # user was working in.
            return
        try:
            app = NSWorkspace.sharedWorkspace().frontmostApplication()
        except Exception:
            return
        try:
            if app is not None and int(app.processIdentifier()) == _own_pid():
                return
        except Exception:
            pass
        self._previous_app = app

    def _restore_front_app(self) -> None:
        app, self._previous_app = self._previous_app, None
        if app is None:
            return
        try:
            # Only hand focus back if it is still ours to give. If the user has
            # since moved to another app - and then answered with a hotkey, or
            # the session stopped - pulling them back would cost them their
            # place instead of saving it.
            if not NSApp.isActive():
                return
        except Exception:
            return
        try:
            if not app.isTerminated():
                app.activateWithOptions_(0)
        except Exception:
            pass


def _own_pid() -> int:
    import os

    return os.getpid()


def _play(name: str) -> None:
    try:
        sound = NSSound.soundNamed_(name)
        if sound is not None:
            sound.play()
        else:
            NSBeep()
    except Exception:
        pass


def _text_height(text: str, font, width: float) -> float:
    """Height the text needs when wrapped to `width`."""
    if not text:
        return 0.0
    try:
        attributed = NSAttributedString.alloc().initWithString_attributes_(
            str(text), {NSFontAttributeName: font}
        )
        rect = attributed.boundingRectWithSize_options_(
            (width, 10000.0), NSStringDrawingUsesLineFragmentOrigin
        )
        return math.ceil(rect.size.height) + 2.0
    except Exception:
        return 0.0


def _style_label(field, font) -> None:
    field.setBezeled_(False)
    field.setDrawsBackground_(False)
    field.setEditable_(False)
    field.setSelectable_(False)
    field.setFont_(font)
    field.setUsesSingleLineMode_(False)
    field.setLineBreakMode_(NSLineBreakByWordWrapping)
    cell = field.cell()
    if cell is not None:
        cell.setWraps_(True)
        cell.setScrollable_(False)
