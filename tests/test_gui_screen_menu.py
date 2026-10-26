"""The cat's "What's on my screen?" menu item sends a question the screen tool will act on."""

from ragnarok.gui.app import SCREEN_QUESTION
from ragnarok.gui.bridge import Bridge
from ragnarok.gui.chat import ChatWindow
from ragnarok.skills.screen.tools import asks_about_screen


def test_the_menu_question_is_one_the_screen_tool_accepts():
    # read_screen refuses unless the user's own message asks about the screen
    assert asks_about_screen(SCREEN_QUESTION)


def test_ask_sends_and_shows_the_message_like_typing_it(hidden_tk):
    bridge = Bridge()
    busy: list[bool] = []
    chat = ChatWindow(hidden_tk, bridge, on_busy=busy.append, on_close=lambda: None)
    chat.ask(SCREEN_QUESTION)
    assert bridge.inputs.get_nowait() == SCREEN_QUESTION
    assert chat.busy and busy == [True]
