"""
title: Create image
author: selvalogesh
version: 1.0.0
description: A toggle in the chat input. When it's on, the Router turns the message into an image with OpenRouter's image models, cheapest first (CR-1 §3.2, R9).
"""

from pydantic import BaseModel

ICON = (
    "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' "
    "stroke='currentColor' stroke-width='2'><rect x='3' y='3' width='18' height='18' rx='2'/>"
    "<circle cx='9' cy='9' r='2'/><path d='M21 15l-5-5L5 21'/></svg>"
)


class Filter:
    class Valves(BaseModel):
        priority: int = 0

    def __init__(self):
        self.valves = self.Valves()
        self.toggle = True
        self.icon = ICON

    async def inlet(self, body: dict) -> dict:
        body["router_image_mode"] = True  # router.py IMAGE_FLAG
        return body
