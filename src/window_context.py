"""Read text from the active window using Windows UI Automation API.

Uses the COM IUIAutomation interface to read text near the cursor
from the currently focused element. Never moves cursor, never sends
keystrokes, never touches the clipboard.

Falls back to empty string on any failure.
"""

import logging

logger = logging.getLogger(__name__)

# Maximum characters to return (text before caret)
MAX_CONTEXT_CHARS = 500


def get_window_context() -> str:
    """Read up to 500 chars of text before the cursor in the active window.

    Uses IUIAutomation COM interface:
      1. Get the focused element
      2. Try TextPattern → get text near caret
      3. Fall back to ValuePattern → read the Value property
      4. On any failure, return ""

    Returns:
        Text preceding the cursor position, or "" on failure.
    """
    try:
        return _read_via_uia()
    except Exception as e:
        logger.debug("Window context unavailable: %s", e)
        return ""


def _read_via_uia() -> str:
    """Internal: read text using comtypes + UIAutomation."""
    import ctypes
    import comtypes
    import comtypes.client

    # COM must be initialized on the calling thread
    ctypes.windll.ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED
    try:
        return _read_via_uia_inner(comtypes)
    finally:
        ctypes.windll.ole32.CoUninitialize()


def _read_via_uia_inner(comtypes) -> str:
    """Actual UIA reading with COM already initialized."""
    import comtypes.client

    # Create IUIAutomation instance
    uia = comtypes.CoCreateInstance(
        comtypes.GUID("{FF48DBA4-60EF-4201-AA87-54103EEF594E}"),  # CUIAutomation
        interface=None,
        clsctx=comtypes.CLSCTX_INPROC_SERVER,
    )

    # Get IUIAutomation interface
    IUIAutomation = comtypes.client.GetModule("UIAutomationCore.dll")
    uia = uia.QueryInterface(IUIAutomation.IUIAutomation)

    # Get focused element
    focused = uia.GetFocusedElement()
    if focused is None:
        return ""

    # Strategy 1: TextPattern — best for rich text editors, browsers, Word, etc.
    text = _try_text_pattern(focused, IUIAutomation)
    if text:
        return text

    # Strategy 2: ValuePattern — works for simple text fields, address bars, etc.
    text = _try_value_pattern(focused, IUIAutomation)
    if text:
        return text

    return ""


def _try_text_pattern(focused, uia_module) -> str:
    """Try to read text via IUIAutomationTextPattern."""
    try:
        UIA_TextPatternId = 10014
        pattern = focused.GetCurrentPattern(UIA_TextPatternId)
        if pattern is None:
            return ""

        text_pattern = pattern.QueryInterface(uia_module.IUIAutomationTextPattern)

        # Try to get caret/selection range first
        ranges = text_pattern.GetSelection()
        if ranges is not None and ranges.Length > 0:
            caret_range = ranges.GetElement(0)

            # Clone the range, then expand/move to get preceding text
            context_range = caret_range.Clone()

            # Move start backwards up to MAX_CONTEXT_CHARS characters
            # TextUnit_Character = 0
            context_range.MoveEndpointByUnit(
                0,  # TextPatternRangeEndpoint_Start
                0,  # TextUnit_Character
                -MAX_CONTEXT_CHARS,
            )

            # Set end to caret start position
            context_range.MoveEndpointByRange(
                1,  # TextPatternRangeEndpoint_End
                caret_range,
                0,  # TextPatternRangeEndpoint_Start (of caret range)
            )

            text = context_range.GetText(MAX_CONTEXT_CHARS)
            if text:
                return text.strip()

        # Fallback: get entire document text (trimmed)
        doc_range = text_pattern.DocumentRange
        if doc_range:
            full_text = doc_range.GetText(MAX_CONTEXT_CHARS * 2)
            if full_text:
                # Return last MAX_CONTEXT_CHARS chars as approximate context
                return full_text[-MAX_CONTEXT_CHARS:].strip()

    except Exception as e:
        logger.debug("TextPattern failed: %s", e)

    return ""


def _try_value_pattern(focused, uia_module) -> str:
    """Try to read text via IUIAutomationValuePattern."""
    try:
        UIA_ValuePatternId = 10002
        pattern = focused.GetCurrentPattern(UIA_ValuePatternId)
        if pattern is None:
            return ""

        value_pattern = pattern.QueryInterface(uia_module.IUIAutomationValuePattern)
        value = value_pattern.CurrentValue
        if value:
            # Return last MAX_CONTEXT_CHARS chars
            return value[-MAX_CONTEXT_CHARS:].strip()

    except Exception as e:
        logger.debug("ValuePattern failed: %s", e)

    return ""
