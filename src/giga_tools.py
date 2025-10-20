from langchain_gigachat.chat_models import GigaChat
from langgraph.prebuilt import create_react_agent
from langchain.tools import tool
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError, validator, field_validator, Field
from typing import List, Optional, Dict, Any
import json
import os

class ElementDTO(BaseModel):
    ID: int
    Name: str
    Symbol: str
    Atomic_number: int
    Standard_atomic_weight: float
    Density: float
    stoichiometric_coefficient: int
    Displacement_energy: int
    Lattice: int
    Surface: int
    Compound: int
    Gas: int
    Percentage: float

    @field_validator('ID', 'Atomic_number', 'stoichiometric_coefficient', 
                    'Displacement_energy', 'Lattice', 'Surface', 'Compound', 'Gas', mode='before')
    @classmethod
    def convert_str_to_int(cls, v):
        if isinstance(v, str):
            return int(v) if v.strip() else 0
        return v

    @field_validator('Standard_atomic_weight', 'Density', 'Percentage', mode='before')
    @classmethod
    def convert_str_to_float(cls, v):
        if isinstance(v, str):
            return float(v) if v.strip() else 0.0
        return v

class MaterialDTO(BaseModel):
    ID: int
    Name: str
    Description: str
    Width: int
    Elements: List[ElementDTO]

    @field_validator('ID', 'Width', mode='before')
    @classmethod
    def convert_str_to_int(cls, v):
        if isinstance(v, str):
            return int(v) if v.strip() else 0
        return v

class IonDTO(BaseModel):
    pass

class ScreenDTO(BaseModel):
    ID: int
    Name: str
    Description: str
    Count_of_layers: int
    Total_Ions_calculated: int
    Ion_Average_Range: float
    Ion_Average_Range_Straggling: float
    Ion_Lateral_Range: float
    Ion_Lateral_Range_Straggling: float
    Ion_Radial_Range: float
    Ion_Radial_Range_Straggling: float
    Range_Skewne: float
    Range_Kurtosis: float
    Ions_in_layers: List[int]
    Materials: List[MaterialDTO]

    @field_validator('ID', 'Count_of_layers', 'Total_Ions_calculated', mode='before')
    @classmethod
    def convert_str_to_int(cls, v):
        if isinstance(v, str):
            return int(v) if v.strip() else 0
        return v

    @field_validator('Ion_Average_Range', 'Ion_Average_Range_Straggling', 
                    'Ion_Lateral_Range', 'Ion_Lateral_Range_Straggling',
                    'Ion_Radial_Range', 'Ion_Radial_Range_Straggling',
                    'Range_Skewne', 'Range_Kurtosis', mode='before')
    @classmethod
    def convert_str_to_float(cls, v):
        if isinstance(v, str):
            return float(v) if v.strip() else 0.0
        return v

    @field_validator('Ions_in_layers', mode='before')
    @classmethod
    def parse_ions_in_layers(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return []
        return v

class ScreenDataDTO(BaseModel):
    """
    Предоставляет подробную информацию об экране. Экран может состоять из нескольких слоев.
    Для каждого слоя присвоен свой материал. Материал может состоять из чистых элементов или
    из смешанного.
    Для каждого материала указана толщина и информация об элементах, из которых состоит материал.
    Материал может состоять из одного или нескольких элементов. Для каждого материала
    необходимо указать толщину. Для каждого элемента необходимо
    указать правильный химический символ, название, стандартный атомную массу, плотность.
    Если материал состоит из нескольких элементов, то необходимо указать процентное соотношение
    каждого элемента в материале.
    """
    Screen: ScreenDTO
    Ion: List[IonDTO]

class ElementInfo(BaseModel):
    name: str = Field(description="Название элемента")
    symbol: str = Field(description="Химический символ элемента")
    atomic_number: int = Field(description="Атомный номер элемента")
    standard_atomic_weight: float = Field(description="Стандартный атомный вес")
    density: float = Field(description="Плотность элемента")
    percentage: float = Field(description="Процентное содержание конкретного элемента во всем материале")

    def to_dict(self) -> Dict[str, Any]:
        """
        Конвертирует ElementInfo в словарь с переименованными полями
        """
        return {
            "Name": self.name,
            "Symbol": self.symbol,
            "Atomic_number": self.atomic_number,
            "Standard_atomic_weight": self.standard_atomic_weight,
            "Density": self.density,
            "Percentage": self.percentage
        }

class MaterialInfo(BaseModel):
    name: str = Field(description="Название материала")
    description: str = Field(description="Описание материала")
    width: float = Field(description="Толщина материала (слоя) в мкм")
    elements: List[ElementInfo] = Field(description="Список элементов")

    def to_dict(self) -> Dict[str, Any]:
        """
        Конвертирует MaterialInfo в словарь с переименованными полями
        """
        return {
            "Name": self.name,
            "Description": self.description,
            "Width": self.width,
            "Elements": [element.to_dict() for element in self.elements]
        }

class ScreenInfo(BaseModel):
    """
    Краткая информация об экране. Экран может состоять из одного или более слоев. Каждый слой представляет собой материал.
    Необходимо указать материал, название, толщину материала (в мкм), а также из каких элементов он состоит. 
    Для каждого элемента необходимо указать название, химический символ, атомный номер, стандартный атомный вес. 
    Указать плотность элемента. Если в материале несколько элементов, то необходимо указать для каждого элемента его 
    процентное соотношение во всем материале. 
    Внимательно проанализируй, чтобы процентное соотношение (содержание) элемента соответствовало содержанию элемента 
    во всем материале. Необходимо, чтобы суммарное содержание элементов в материале не превышало 100 %. 
    """
    name: str = Field(description="Название экрана")
    description: str = Field(description="Описание экрана")
    materials: List[MaterialInfo] = Field(description="Список материалов слоев экрана")

    def to_dict(self) -> Dict[str, Any]:
        """
        Конвертирует объект ScreenInfo в словарь с переименованными полями
        """
        return {
            "Name": self.name,
            "Description": self.description,
            "Materials": [material.to_dict() for material in self.materials]
        }

class ScreenDataInfo(BaseModel):
    
    screen: ScreenInfo = Field(description="Информация о целом экране")

    def to_dict(self) -> Dict[str, Any]:
        """
        Конвертирует объект ScreenData в словарь с переименованными полями
        """
        return {
            "Screen": self.screen
        }


