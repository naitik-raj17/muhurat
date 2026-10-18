"""Hand-labelled guest replies to a WhatsApp invitation (synthetic), labelled the way a person
reads them, not the way the rules work.

`expected` is "accepted", "declined", "maybe", or "review" when a person should look at it
(the reply doesn't answer the invitation, or it is genuinely ambiguous). `plus_ones` is the
number of extra people for accepted replies.
"""

from __future__ import annotations

from typing import NamedTuple


class Reply(NamedTuple):
    text: str
    expected: str
    plus_ones: int
    lang: str  # en, hinglish, hi


REPLIES: list[Reply] = [
    # ---- English, accepted
    Reply("Yes, we'll be there!", "accepted", 0, "en"),
    Reply("Yes coming", "accepted", 0, "en"),
    Reply("Confirmed, see you at the sangeet", "accepted", 0, "en"),
    Reply("Will attend with my wife", "accepted", 1, "en"),
    Reply("Yes, coming +2", "accepted", 2, "en"),
    Reply("Definitely coming, we are 4 people", "accepted", 3, "en"),
    Reply("Sure, I will come", "accepted", 0, "en"),
    Reply("Yes! Wouldn't miss it for the world", "accepted", 0, "en"),
    Reply("Accept. Coming with 2 kids", "accepted", 2, "en"),
    Reply("Count me in", "accepted", 0, "en"),
    Reply("We'll definitely be there, all 3 of us", "accepted", 2, "en"),
    Reply("Happy to attend!", "accepted", 0, "en"),
    Reply("yes yes coming", "accepted", 0, "en"),
    Reply("Attending with family, 5 members", "accepted", 4, "en"),
    # ---- English, declined
    Reply("Sorry, can't make it", "declined", 0, "en"),
    Reply("Unfortunately we won't be able to come", "declined", 0, "en"),
    Reply("No, I'm travelling that week", "declined", 0, "en"),
    Reply("Regret, unable to attend", "declined", 0, "en"),
    Reply("We cannot come, sorry. Best wishes to the couple!", "declined", 0, "en"),
    Reply("I decline with regret", "declined", 0, "en"),
    Reply("Won't be able to join, exams that week", "declined", 0, "en"),
    Reply("Not coming", "declined", 0, "en"),
    Reply("Can't attend, but sending blessings", "declined", 0, "en"),
    # ---- English, maybe
    Reply("Maybe, will confirm next week", "maybe", 0, "en"),
    Reply("Not sure yet", "maybe", 0, "en"),
    Reply("Will try to come", "maybe", 0, "en"),
    Reply("Might be able to make it for the reception", "maybe", 0, "en"),
    Reply("Depends on my leave, will let you know", "maybe", 0, "en"),
    Reply("Unsure, depends on flights", "maybe", 0, "en"),
    # ---- Hinglish, accepted
    Reply("haan ji zaroor aayenge", "accepted", 0, "hinglish"),
    Reply("Haan aa rahe hain", "accepted", 0, "hinglish"),
    Reply("Zarur aayenge, 3 log", "accepted", 2, "hinglish"),
    Reply("haanji, hum 2 log aayenge", "accepted", 1, "hinglish"),
    Reply("Bilkul aayenge bhai", "accepted", 0, "hinglish"),
    Reply("ha ji, family ke saath aa rahe", "accepted", 0, "hinglish"),
    Reply("Pakka aayenge", "accepted", 0, "hinglish"),
    # ---- Hinglish, declined
    Reply("Nahi aa payenge, sorry", "declined", 0, "hinglish"),
    Reply("Maaf kijiye, nahin aa sakte", "declined", 0, "hinglish"),
    Reply("nahi aa paunga bhai", "declined", 0, "hinglish"),
    Reply("Is baar nahi ho payega", "declined", 0, "hinglish"),
    # ---- Hinglish, maybe
    Reply("pata nahi abhi", "maybe", 0, "hinglish"),
    Reply("Shayad aa jaayenge", "maybe", 0, "hinglish"),
    Reply("Koshish karenge aane ki", "maybe", 0, "hinglish"),
    Reply("abhi pakka nahi hai", "maybe", 0, "hinglish"),
    # ---- Hindi (Devanagari)
    Reply("हाँ, हम ज़रूर आएंगे", "accepted", 0, "hi"),
    Reply("हाँ, हम 4 लोग आएंगे", "accepted", 3, "hi"),
    Reply("जी हां, आ रहे हैं", "accepted", 0, "hi"),
    Reply("बिल्कुल आएंगे", "accepted", 0, "hi"),
    Reply("नहीं आ पाएंगे", "declined", 0, "hi"),
    Reply("माफ़ कीजिए, हम नहीं आ सकेंगे", "declined", 0, "hi"),
    Reply("शायद आएंगे", "maybe", 0, "hi"),
    Reply("पता नहीं, बाद में बताएंगे", "maybe", 0, "hi"),
    # ---- needs a person (not an answer, or genuinely ambiguous)
    Reply("What is the venue address?", "review", 0, "en"),
    Reply("Is there parking at the venue?", "review", 0, "en"),
    Reply("Who is this?", "review", 0, "en"),
    Reply("Congratulations to the couple!", "review", 0, "en"),
    Reply("Please send the location pin", "review", 0, "en"),
    Reply("What's the dress code?", "review", 0, "en"),
    Reply("Can we bring our dog?", "review", 0, "en"),
    Reply("👍", "review", 0, "en"),
    Reply("Yes for the mehendi but no for the wedding", "review", 0, "en"),
    Reply("My husband is coming but I can't", "review", 0, "en"),
    Reply("Kya time hai?", "review", 0, "hinglish"),
    Reply("Venue kahan hai?", "review", 0, "hinglish"),
    Reply("स्थान कहाँ है?", "review", 0, "hi"),
    Reply("बधाई हो!", "review", 0, "hi"),
    # ---- tricky phrasings a person reads easily
    Reply("Can't wait to come!", "accepted", 0, "en"),
    Reply("No doubt, we'll be there", "accepted", 0, "en"),
    Reply("Not sure we can make it, sorry", "maybe", 0, "en"),
    Reply("We are not coming, sorry", "declined", 0, "en"),
    Reply("Wouldn't miss it", "accepted", 0, "en"),
    Reply("No way we'd skip this, coming!", "accepted", 0, "en"),
    Reply("I'm afraid we won't make it", "declined", 0, "en"),
    Reply("Yes, I will come with one more person", "accepted", 1, "en"),
]
