import re

def check_spam(text):

    bad_words = [
        "t.me",
        "@",
        "instagram",
        "whatsapp",
        "kaspi"
    ]

    text = text.lower()

    for word in bad_words:
        if word in text:
            return True

    return False
