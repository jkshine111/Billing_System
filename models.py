from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base

class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True)
    product_id = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    available_stock = Column(Integer, default=0)
    price_per_unit = Column(Float, nullable=False)
    tax_percentage = Column(Float, default=0.0)

    def __repr__(self):
        return f"<Product {self.product_id} {self.name}>"

class Purchase(Base):
    __tablename__ = "purchases"
    id = Column(Integer, primary_key=True)
    customer_email = Column(String, index=True, nullable=False)
    purchase_time = Column(DateTime, default=datetime.utcnow)
    paid_amount = Column(Float, nullable=False)
    total_amount = Column(Float, nullable=False)
    balance = Column(Float, nullable=False)
    items = relationship("PurchaseItem", back_populates="purchase", cascade="all,delete-orphan")

class PurchaseItem(Base):
    __tablename__ = "purchase_items"
    id = Column(Integer, primary_key=True)
    purchase_id = Column(Integer, ForeignKey("purchases.id"))
    product_id = Column(Integer, ForeignKey("products.id"))
    quantity = Column(Integer, nullable=False)
    purchase = relationship("Purchase", back_populates="items")
    product = relationship("Product")

class Denomination(Base):
    __tablename__ = "denominations"
    id = Column(Integer, primary_key=True)
    value = Column(Integer, unique=True)
