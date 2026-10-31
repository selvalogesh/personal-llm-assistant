"""
title: Web search
author: selvalogesh
version: 1.0.0
description: A toggle in the chat input. When it's on, the Router adds OpenRouter's web plugin to the turn (CR-1 §3.1, R8).
"""

from pydantic import BaseModel

ICON = (
    "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' "
    "stroke='currentColor' stroke-width='2'><circle cx='12' cy='12' r='9'/><path d='M3 12h18M12 3a14 14 0 0 1 0 18"
    "M12 3a14 14 0 0 0 0 18'/></svg>"
)


class Filter:
    class Valves(BaseModel):
        priority: int = 0

    def __init__(self):
        self.valves = self.Valves()
        self.toggle = True
        self.icon = ICON

    async def inlet(self, body: dict) -> dict:
        body["router_web_search"] = True  # router.py WEB_FLAG
        return body
