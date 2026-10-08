"""Versioned-in-code starting templates; each project may create many instances."""

TEMPLATES = {
    'supporting': {'title':'Supporting material','sections':[]},
    'recipe-card': {'title': 'Recipe card', 'sections': [
        {'key': 'yield', 'label': 'Yield and preparation', 'guidance': 'Servings, time, equipment, and accessibility notes.'},
        {'key': 'ingredients', 'label': 'Ingredients', 'guidance': 'Amounts and substitutions; do not infer missing quantities.'},
        {'key': 'method', 'label': 'Method', 'guidance': 'Steps in the order participants will follow.'},
        {'key': 'safety', 'label': 'Food safety and allergens', 'guidance': 'Record reviewed local guidance and known allergens.'},
        {'key': 'notes', 'label': 'Pantry and facilitator notes', 'guidance': 'Supplies, sourcing, adaptations, and unresolved details.'},
    ]},
    'pantry-poster': {'title': 'Pantry wall poster', 'sections': [
        {'key': 'heading', 'label': 'Poster heading', 'guidance': 'A short, readable wall heading.'},
        {'key': 'supplies', 'label': 'Available supplies', 'guidance': 'List categories or items clearly.'},
        {'key': 'use', 'label': 'How to use the pantry', 'guidance': 'Short instructions visible at a distance.'},
        {'key': 'storage', 'label': 'Storage and safety', 'guidance': 'Location and safe storage notes.'},
        {'key': 'replenishment', 'label': 'Replenishment and contact', 'guidance': 'Who to tell when supplies run low.'},
    ]},
    'program-media': {'title': 'Other program media', 'sections': [
        {'key': 'heading', 'label': 'Heading', 'guidance': 'A concise title for a handout or wall display.'},
        {'key': 'audience', 'label': 'Audience and purpose', 'guidance': 'Who will use this item and why.'},
        {'key': 'message', 'label': 'Main content', 'guidance': 'Write the material in reading order.'},
        {'key': 'instructions', 'label': 'Instructions and notes', 'guidance': 'Practical directions and unresolved details.'},
    ]},
}
