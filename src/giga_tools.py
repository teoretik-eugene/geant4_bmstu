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
    density: Optional[float] = Field(
        default=None,
        description=(
            "Плотность элемента в г/см³. "
            "Обязательна для обычных материалов и сплавов (isCompound=false). "
            "Для соединений (isCompound=true) не требуется — плотность задаётся "
            "на уровне материала, а доли вычисляются из NAtoms."
        )
    )
    percentage: Optional[float] = Field(
        default=None,
        description=(
            "Массовая доля элемента в материале в процентах (0–100). "
            "Используется для обычных материалов и сплавов (isCompound=false). "
            "Для соединений (isCompound=true) не нужна — вычисляется автоматически из NAtoms."
        )
    )
    n_atoms: Optional[int] = Field(
        default=None,
        description=(
            "Количество атомов данного элемента в молекуле соединения. "
            "Используется только при isCompound=true. "
            "Например, для H₂O: H → n_atoms=2, O → n_atoms=1."
        )
    )

    def to_dict(self) -> Dict[str, Any]:
        """
        Конвертирует ElementInfo в словарь с переименованными полями.
        Включает только непустые поля, чтобы не нарушать обратную совместимость.
        """
        result: Dict[str, Any] = {
            "Name": self.name,
            "Symbol": self.symbol,
            "Atomic_number": self.atomic_number,
            "Standard_atomic_weight": self.standard_atomic_weight,
        }
        if self.density is not None:
            result["Density"] = self.density
        if self.percentage is not None:
            result["Percentage"] = self.percentage
        if self.n_atoms is not None:
            result["NAtoms"] = self.n_atoms
        return result

class MaterialInfo(BaseModel):
    name: str = Field(description="Название материала")
    description: str = Field(description="Описание материала")
    width: float = Field(description="Толщина материала (слоя) в мкм")
    elements: Optional[List[ElementInfo]] = Field(
        default=None,
        description=(
            "Список элементов, составляющих материал. "
            "Не требуется если задано поле nist_name."
        )
    )
    density: Optional[float] = Field(
        default=None,
        description=(
            "Плотность материала в г/см³. "
            "Если не указана для обычного материала — вычисляется как средневзвешенная "
            "по долям элементов (поведение по умолчанию). "
            "Обязательна для соединений (is_compound=true)."
        )
    )
    is_compound: Optional[bool] = Field(
        default=False,
        description=(
            "Признак химического соединения. "
            "Если true — массовые доли элементов вычисляются автоматически "
            "из числа атомов (NAtoms) и атомных масс. "
            "Требует обязательного указания density и NAtoms для каждого элемента. "
            "Если false или не указано — используется стандартный расчёт через Percentage."
        )
    )
    nist_name: Optional[str] = Field(
        default=None,
        description=(
            "Имя материала из базы NIST Geant4 (например 'G4_POLYETHYLENE', 'G4_WATER', "
            "'G4_KAPTON', 'G4_Al', 'G4_Fe'). "
            "Если указано — материал берётся напрямую из базы NIST; "
            "поля elements, density и is_compound игнорируются."
        )
    )

    def to_dict(self) -> Dict[str, Any]:
        """
        Конвертирует MaterialInfo в словарь с переименованными полями.
        Включает только непустые поля, чтобы не нарушать обратную совместимость.
        """
        result: Dict[str, Any] = {
            "Name": self.name,
            "Description": self.description,
            "Width": self.width,
        }
        if self.nist_name is not None:
            result["NistName"] = self.nist_name
        if self.density is not None:
            result["Density"] = self.density
        if self.is_compound:
            result["isCompound"] = self.is_compound
        if self.elements:
            result["Elements"] = [element.to_dict() for element in self.elements]
        return result

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


