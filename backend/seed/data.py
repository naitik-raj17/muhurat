"""Vocabulary for synthetic seed data. Every name produced from this is fictional."""

from __future__ import annotations

CITIES: dict[str, list[str]] = {
    # city -> languages vendors there commonly speak (besides English/Hindi)
    "Delhi": ["pa"],
    "Mumbai": ["mr", "gu"],
    "Jaipur": ["rj"],
    "Patna": ["bh", "mai"],
    "Bengaluru": ["kn", "ta"],
    "Hyderabad": ["te", "ur"],
    "Kolkata": ["bn"],
    "Udaipur": ["rj"],
}

# category -> (min price low, min price high, spread factor) in INR, per event
PRICE_BANDS: dict[str, tuple[int, int, float]] = {
    "venue": (300_000, 1_800_000, 1.8),
    "caterer": (200_000, 1_200_000, 1.7),
    "decorator": (100_000, 900_000, 1.9),
    "photographer": (50_000, 350_000, 1.8),
    "makeup": (15_000, 120_000, 1.6),
    "mehendi_artist": (8_000, 60_000, 1.6),
    "dj": (25_000, 200_000, 1.7),
    "pandit": (11_000, 75_000, 1.5),
}

STYLES: dict[str, list[str]] = {
    "venue": ["royal", "heritage", "palace", "garden", "beach", "modern", "destination", "luxury"],
    "caterer": ["rajasthani", "punjabi", "south-indian", "bengali", "mughlai", "jain", "vegan"],
    "decorator": [
        "royal",
        "floral",
        "pastel",
        "boho",
        "minimalist",
        "rajasthani",
        "traditional",
        "luxury",
    ],
    "photographer": ["candid", "cinematic", "traditional", "documentary", "drone", "pre-wedding"],
    "makeup": ["bridal", "hd", "airbrush", "natural", "glam", "south-indian", "bengali"],
    "mehendi_artist": ["arabic", "rajasthani", "bridal", "minimal", "portrait"],
    "dj": ["bollywood", "punjabi", "edm", "sufi", "live-band", "dhol"],
    "pandit": ["vedic", "north-indian", "south-indian", "bengali", "arya-samaj"],
}

NAME_PARTS: dict[str, tuple[list[str], list[str]]] = {
    "venue": (
        ["Rajmahal", "Sheesh", "Neel", "Chandra", "Aangan", "Haveli", "Suryagarh", "Kesar"],
        ["Palace", "Gardens", "Banquets", "Resort", "Courtyard", "Lawns", "Mahal"],
    ),
    "caterer": (
        ["Annapurna", "Swaad", "Rasoi", "Zaika", "Thali", "Dawat", "Bhoj", "Masala"],
        ["Caterers", "Kitchens", "Food Co.", "Hospitality", "Feasts"],
    ),
    "decorator": (
        ["Gulmohar", "Phoolwari", "Rangoli", "Toran", "Kalash", "Marigold", "Saaj"],
        ["Decor", "Designs", "Events", "Studio", "Creations"],
    ),
    "photographer": (
        ["Lens", "Frame", "Kahani", "Pixel", "Lumiere", "Yaadein", "Click"],
        ["Stories", "Studio", "Films", "Photography", "Frames"],
    ),
    "makeup": (
        ["Shringar", "Roop", "Glow", "Kajal", "Noor", "Sundari"],
        ["Makeovers", "Beauty Studio", "Artistry", "Salon"],
    ),
    "mehendi_artist": (
        ["Heena", "Mehendi", "Rachna", "Lali", "Bel", "Kesari"],
        ["Arts", "by Design", "Studio", "Creations"],
    ),
    "dj": (
        ["Beat", "Dhol", "Bass", "Groove", "Sur", "Taal"],
        ["Nation", "Entertainment", "Sounds", "Live", "Collective"],
    ),
    "pandit": (
        ["Shri Vedanta", "Acharya Ved", "Pandit Shastri", "Gyan", "Sanskriti"],
        ["Ji", "Purohit Seva", "Vedic Services", "Karmkand"],
    ),
}

EN_DESCRIPTIONS: dict[str, list[str]] = {
    "venue": [
        "A {style} venue in {city} hosting up to {n} guests, with lawns for mehendi and a "
        "banquet hall for the reception.",
        "{style} property in {city} with in-house rooms, a mandap lawn and valet parking.",
    ],
    "caterer": [
        "Specialists in {style} cuisine with live counters, serving weddings of {n}+ guests in "
        "{city}.",
        "{style} menus, pure-veg and non-veg kitchens, tasting sessions included.",
    ],
    "decorator": [
        "{style} decor for mehendi, sangeet and mandap setups across {city}.",
        "Full-service {style} decorators: stage, entrance, florals and lighting.",
    ],
    "photographer": [
        "{style} wedding photography and films; teams of {k} shooters for multi-day weddings.",
        "Award-winning {style} storytellers covering weddings in {city} and destinations.",
    ],
    "makeup": [
        "{style} bridal makeup and hairstyling, trial session included, travels across {city}.",
        "Celebrity-trained artists offering {style} looks for the bride and family.",
    ],
    "mehendi_artist": [
        "{style} mehendi designs for brides and guests; team of {k} artists for large events.",
        "Organic henna, {style} patterns, quick application for sangeet-night guests.",
    ],
    "dj": [
        "{style} DJ sets with sound, lights and LED dance floor for sangeet and baraat.",
        "High-energy {style} music for baraat, sangeet and after-parties in {city}.",
    ],
    "pandit": [
        "{style} rituals performed with explanations in Hindi and English for the family.",
        "Experienced purohit for {style} ceremonies, havan samagri arranged.",
    ],
}

HI_DESCRIPTIONS: dict[str, list[str]] = {
    "venue": ["{city} में शाही विवाह स्थल, {n} मेहमानों तक की व्यवस्था, बगीचा और बैंक्वेट हॉल।"],
    "caterer": ["शुद्ध शाकाहारी और पारंपरिक {style} व्यंजन, लाइव काउंटर के साथ।"],
    "decorator": ["मेहंदी, संगीत और मंडप के लिए {style} सजावट, फूलों और रोशनी के साथ।"],
    "photographer": ["शादी की कैंडिड फोटोग्राफी और सिनेमैटिक फिल्में, {city} और बाहर।"],
    "makeup": ["दुल्हन का {style} मेकअप और हेयरस्टाइल, ट्रायल सहित।"],
    "mehendi_artist": ["दुल्हन और मेहमानों के लिए सुंदर {style} मेहंदी डिज़ाइन।"],
    "dj": ["संगीत और बारात के लिए बॉलीवुड और पंजाबी डीजे, साउंड और लाइट्स के साथ।"],
    "pandit": ["वैदिक विधि से विवाह संस्कार, हिंदी और संस्कृत में मंत्रोच्चार।"],
}

FAMILY_SURNAMES = [
    "Sharma",
    "Verma",
    "Gupta",
    "Agarwal",
    "Singh",
    "Mehta",
    "Iyer",
    "Reddy",
    "Rao",
    "Nair",
    "Das",
    "Banerjee",
    "Chatterjee",
    "Mukherjee",
    "Joshi",
    "Kapoor",
    "Malhotra",
    "Bansal",
    "Jain",
    "Pillai",
    "Kumar",
    "Mishra",
    "Pandey",
    "Yadav",
    "Bose",
    "Ghosh",
]
RELATIONS = [
    "uncle",
    "aunt",
    "cousin",
    "friend",
    "colleague",
    "neighbour",
    "grandparent",
    "sibling",
]

FIRST_NAMES = [
    "Aarav",
    "Aditi",
    "Akash",
    "Ananya",
    "Anil",
    "Anjali",
    "Arjun",
    "Asha",
    "Deepak",
    "Divya",
    "Gaurav",
    "Geeta",
    "Harsh",
    "Isha",
    "Kavita",
    "Kiran",
    "Lakshmi",
    "Manoj",
    "Meera",
    "Mohit",
    "Neha",
    "Nikhil",
    "Pooja",
    "Pradeep",
    "Priya",
    "Rahul",
    "Rajesh",
    "Ravi",
    "Rekha",
    "Rohan",
    "Sanjay",
    "Sarita",
    "Shreya",
    "Sneha",
    "Sunil",
    "Sunita",
    "Suresh",
    "Tanvi",
    "Usha",
    "Varun",
    "Vikram",
    "Vinita",
    "Yash",
    "Zoya",
    "Farhan",
    "Imran",
    "Simran",
    "Gurpreet",
    "Lalit",
    "Pallavi",
]
# Neighbourhoods used to tell households with the same surname apart.
LOCALITIES = [
    "Malviya Nagar",
    "Lajpat Nagar",
    "Andheri",
    "Kothrud",
    "Salt Lake",
    "Jayanagar",
    "Banjara Hills",
    "Vaishali Nagar",
    "Boring Road",
    "Gomti Nagar",
    "Sector 15",
    "Civil Lines",
    "Model Town",
]
