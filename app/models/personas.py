# app/models/personas.py
from typing import List, Optional
from pydantic import BaseModel


class Trait(BaseModel):
    name: str


class Persona(BaseModel):
    name: str
    traits: List[str]


class Behavior(BaseModel):
    name: str
    characteristics: List[str]


class PersonaCollection(BaseModel):
    personas: List[Persona]
    behaviors: List[Behavior]
