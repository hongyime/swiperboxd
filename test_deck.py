import sys
import os

# Ensure src is in PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "src")))

try:
    from api.app import list_deck
    deck = list_deck("official-top-250-films-with-the-most-fans", "hongyime")
    print(deck)
except Exception as e:
    import traceback
    traceback.print_exc()
