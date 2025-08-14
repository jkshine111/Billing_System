# Pydantic schemas

from pydantic import BaseModel
from typing import List, Optional

class ProductBase(BaseModel):
    product_id: str
    name: str
    available_stock: int
    price_per_unit: float
    tax_percentage: float

class ProductCreate(ProductBase):
    pass

class PurchaseItemCreate(BaseModel):
    product_id: str
    quantity: int

class PurchaseCreate(BaseModel):
    customer_email: str
    items: List[PurchaseItemCreate]
    paid_amount: float
