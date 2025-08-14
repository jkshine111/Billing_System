import math
import asyncio
from pathlib import Path
from typing import Optional
from datetime import datetime
from typing import Dict, List
from utils import normalize_email
from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from database import SessionLocal, engine, Base
from mail_notification import send_invoice_email
from fastapi.responses import HTMLResponse, RedirectResponse
from models import Product, Purchase, PurchaseItem, Denomination
from fastapi import FastAPI, Depends, Request, Form, HTTPException, status, BackgroundTasks, Query


BASE_DIR = Path(__file__).resolve().parent

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Mini Billing (FastAPI)")
# templates = Jinja2Templates(directory="templates")

# Use absolute paths
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# -------------------------------
# Simple Admin UI (Products only)
# -------------------------------

@app.get("/admin", include_in_schema=False)
def admin_root():
    return RedirectResponse(url="/admin/products")

@app.get("/admin/products", response_class=HTMLResponse)
def admin_products_list(request: Request, db: Session = Depends(get_db)):
    products = db.query(Product).order_by(Product.name.asc()).all()
    return templates.TemplateResponse(
        "admin_products.html",
        {"request": request, "products": products}
    )

@app.get("/admin/products/new", response_class=HTMLResponse)
def admin_products_new_form(request: Request):
    return templates.TemplateResponse(
        "admin_product_form.html",
        {"request": request, "mode": "create", "product": None}
    )

@app.post("/admin/products/new")
async def admin_products_create(
    request: Request,
    product_id: str = Form(...),
    name: str = Form(...),
    available_stock: int = Form(...),
    price_per_unit: float = Form(...),
    tax_percentage: float = Form(...),
    db: Session = Depends(get_db),
):
    # Basic validations / uniqueness
    product_id = product_id.strip()
    name = name.strip()
    if not product_id or not name:
        return HTMLResponse("product_id and name are required", status_code=400)

    if db.query(Product).filter(Product.product_id == product_id).first():
        return HTMLResponse("product_id already exists", status_code=409)

    obj = Product(
        product_id=product_id,
        name=name,
        available_stock=max(0, available_stock),
        price_per_unit=max(0.0, price_per_unit),
        tax_percentage=max(0.0, tax_percentage),
    )
    db.add(obj)
    db.commit()
    return RedirectResponse(url="/admin/products", status_code=status.HTTP_303_SEE_OTHER)

@app.get("/admin/products/{id}/edit", response_class=HTMLResponse)
def admin_products_edit_form(id: int, request: Request, db: Session = Depends(get_db)):
    product = db.query(Product).get(id)
    if not product:
        return HTMLResponse("Product not found", status_code=404)
    return templates.TemplateResponse(
        "admin_product_form.html",
        {"request": request, "mode": "edit", "product": product}
    )

@app.post("/admin/products/{id}/edit")
async def admin_products_update(
    id: int,
    request: Request,
    product_id: str = Form(...),
    name: str = Form(...),
    available_stock: int = Form(...),
    price_per_unit: float = Form(...),
    tax_percentage: float = Form(...),
    db: Session = Depends(get_db),
):
    product = db.query(Product).get(id)
    if not product:
        return HTMLResponse("Product not found", status_code=404)

    product_id = product_id.strip()
    name = name.strip()
    if not product_id or not name:
        return HTMLResponse("product_id and name are required", status_code=400)

    # keep product_id unique
    exists = db.query(Product).filter(Product.product_id == product_id, Product.id != id).first()
    if exists:
        return HTMLResponse("product_id already exists", status_code=409)

    product.product_id = product_id
    product.name = name
    product.available_stock = max(0, available_stock)
    product.price_per_unit = max(0.0, price_per_unit)
    product.tax_percentage = max(0.0, tax_percentage)

    db.commit()
    return RedirectResponse(url="/admin/products", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/admin/products/{id}/delete")
def admin_products_delete(id: int, db: Session = Depends(get_db)):
    product = db.query(Product).get(id)
    if not product:
        return HTMLResponse("Product not found", status_code=404)
    db.delete(product)
    db.commit()
    return RedirectResponse(url="/admin/products", status_code=status.HTTP_303_SEE_OTHER)


@app.on_event("startup")
def startup_event():
    db = SessionLocal()
    try:
        # Seed products once
        if db.query(Product).count() == 0:
            db.add_all([
                Product(product_id="P1001", name="Pen",      available_stock=100, price_per_unit=10.0, tax_percentage=5.0),
                Product(product_id="P1002", name="Notebook", available_stock=50,  price_per_unit=50.0, tax_percentage=12.0),
                Product(product_id="P1003", name="Eraser",   available_stock=200, price_per_unit=5.0,  tax_percentage=0.0),
            ])
            db.commit()
        # Seed denominations once
        if db.query(Denomination).count() == 0:
            for v in [2000, 500, 200, 100, 50, 20, 10, 5, 2, 1]:
                db.add(Denomination(value=v))
            db.commit()
    finally:
        db.close()

@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/billing")

# Billing page
@app.get("/billing", response_class=HTMLResponse)
async def billing_form(request: Request, db: Session = Depends(get_db)):
    products = db.query(Product).order_by(Product.name.asc()).all()
    return templates.TemplateResponse("billing_form.html", {"request": request, "products": products})

# Generate bill (HTML form POST)
@app.post("/generate_bill", response_class=HTMLResponse)
async def generate_bill(request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    form = await request.form()

    customer_email = (form.get("customer_email") or "").strip()
    if not customer_email:
        return HTMLResponse("Customer email is required", status_code=400)

    try:
        paid_amount = float((form.get("paid_amount") or "0").strip())
    except Exception:
        return HTMLResponse("Invalid paid amount", status_code=400)

    # Collect dynamic items
    items_raw: List[Dict] = []
    i = 1
    while True:
        pid_key, qty_key = f"product_id_{i}", f"quantity_{i}"
        if pid_key not in form:
            break
        pid = (form.get(pid_key) or "").strip()
        try:
            qty = int(form.get(qty_key, "0"))
        except Exception:
            return HTMLResponse(f"Invalid quantity for row {i}", status_code=400)
        if not pid or qty <= 0:
            return HTMLResponse(f"Invalid product/quantity at row {i}", status_code=400)
        items_raw.append({"product_id": pid, "quantity": qty})
        i += 1

    if not items_raw:
        return HTMLResponse("No items provided", status_code=400)

    # Validate, compute totals, check stock
    total = 0.0
    details = []
    for row in items_raw:
        product = db.query(Product).filter(Product.product_id == row["product_id"]).first()
        if not product:
            return HTMLResponse(f"Product {row['product_id']} not found", status_code=400)
        if product.available_stock < row["quantity"]:
            return HTMLResponse(
                f"Insufficient stock for {product.name} (have {product.available_stock}, need {row['quantity']})",
                status_code=400,
            )
        amount = product.price_per_unit * row["quantity"]
        tax_amount = amount * (product.tax_percentage / 100.0)
        line_total = amount + tax_amount
        total += line_total
        details.append({
            "product_obj": product,
            "name": product.name,
            "quantity": row["quantity"],
            "unit_price": product.price_per_unit,
            "tax_percent": product.tax_percentage,
            "amount": amount,
            "tax_amount": tax_amount,
            "total": line_total,
        })

    total = round(total, 2)
    if paid_amount < total:
        return HTMLResponse(f"Paid amount (₹{paid_amount:.2f}) is less than total (₹{total:.2f})", status_code=400)

    balance = round(paid_amount - total, 2)

    # Change denominations (integer rupees)
    denoms = db.query(Denomination).order_by(Denomination.value.desc()).all()
    balance_denoms: Dict[int, int] = {}
    change_int = int(math.floor(balance))
    for d in denoms:
        c = change_int // d.value
        if c > 0:
            balance_denoms[d.value] = c
            change_int %= d.value

    # Persist purchase and items; decrement stock
    purchase = Purchase(
        customer_email=customer_email,
        total_amount=total,
        paid_amount=paid_amount,
        balance=balance,
        purchase_time=datetime.now(),
    )
    db.add(purchase)
    db.commit()
    db.refresh(purchase)

    for d in details:
        db.add(PurchaseItem(
            purchase_id=purchase.id,
            product_id=d["product_obj"].id,
            quantity=d["quantity"],
        ))
        d["product_obj"].available_stock -= d["quantity"]
    db.commit()

    # --- SEND EMAIL and build notice ---
    email_notice = None
    email_status = "success"
    try:
        # call your existing mailer; raise on error
        send_invoice_email(customer_email, purchase.id)
        email_notice = f"Invoice emailed to {customer_email}"
        email_status = "success"
    except Exception as e:
        email_notice = f"Email failed: {e}"
        email_status = "error"

    subtotal_before_tax = round(sum(d["amount"] for d in details), 2)
    total_tax = round(sum(d["tax_amount"] for d in details), 2)
    net_price = round(subtotal_before_tax + total_tax, 2)
    rounded_down = float(int(net_price))  # whole-rupee floor

    return templates.TemplateResponse("bill_display.html", {
        "request": request,
        "customer_email": customer_email,
        "details": details,
        "total": net_price,
        "paid_amount": paid_amount,
        "balance": balance,
        "balance_denoms": balance_denoms,
        "purchase_id": purchase.id,
        "purchase_time": purchase.purchase_time,
        "subtotal_before_tax": subtotal_before_tax,
        "total_tax": total_tax,
        "rounded_down": rounded_down,
        "email_notice": email_notice,       # <<< pass message
        "email_status": email_status,       # <<< 'success' | 'error'
    })


@app.get("/purchases", response_class=HTMLResponse)
async def view_purchases(
    request: Request,
    db: Session = Depends(get_db),
    customer: Optional[str] = Query(default=None)
    ):
    # All purchases (for the top table)
    purchases = db.query(Purchase).order_by(Purchase.purchase_time.desc()).all()
    total_revenue = round(sum(p.total_amount for p in purchases), 2)

    # Unique customers count (normalized)
    unique_customers = db.query(
        func.count(func.distinct(func.lower(func.trim(Purchase.customer_email))))
    ).scalar()

    # Unique customer list + order counts (normalized), for the clickable list
    customers = db.query(
        func.lower(func.trim(Purchase.customer_email)).label("email"),
        func.count(Purchase.id).label("orders")
    ).group_by("email").order_by(func.count(Purchase.id).desc()).all()

    # If a customer is selected, fetch that customer's purchases
    selected_customer = None
    customer_purchases = []
    customer_total_revenue = 0.0

    if customer:
        norm = customer.strip().lower()
        selected_customer = norm
        customer_purchases = (
            db.query(Purchase)
            .filter(func.lower(func.trim(Purchase.customer_email)) == norm)
            .order_by(Purchase.purchase_time.desc())
            .all()
        )
        customer_total_revenue = round(sum(p.total_amount for p in customer_purchases), 2)

    # Rows for main table
    purchase_rows = [{
        "id": p.id,
        "customer_email": p.customer_email,
        "purchase_time": p.purchase_time,
        "total_amount": p.total_amount,
        "paid_amount": p.paid_amount,
        "balance": p.balance,
        "items_count": len(p.items),
    } for p in purchases]

    # Rows for selected customer's table
    customer_rows = [{
        "id": p.id,
        "customer_email": p.customer_email,
        "purchase_time": p.purchase_time,
        "total_amount": p.total_amount,
        "paid_amount": p.paid_amount,
        "balance": p.balance,
        "items_count": len(p.items),
    } for p in customer_purchases]

    return templates.TemplateResponse("purchases.html", {
        "request": request,
        "purchases": purchases,
        "purchase_rows": purchase_rows,
        "total_revenue": total_revenue,
        "unique_customers": unique_customers,
        "customers": customers,                          # list of (email, orders)
        "selected_customer": selected_customer,          # normalized email or None
        "customer_rows": customer_rows,                  # table for selected customer
        "customer_total_revenue": customer_total_revenue # total for selected customer
    })


@app.get("/customers", response_class=HTMLResponse)
async def view_customers(
    request: Request,
    db: Session = Depends(get_db),
    customer: Optional[str] = Query(default=None)
):
    # Get all unique customers with order counts
    customers = db.query(
        func.lower(func.trim(Purchase.customer_email)).label("email"),
        func.count(Purchase.id).label("orders")
    ).group_by("email").order_by(func.count(Purchase.id).desc()).all()

    # If a customer is selected, get their purchase details
    selected_customer = None
    customer_rows = []
    customer_total_revenue = 0.0

    if customer:
        norm = customer.strip().lower()
        selected_customer = norm
        purchases = (
            db.query(Purchase)
            .filter(func.lower(func.trim(Purchase.customer_email)) == norm)
            .order_by(Purchase.purchase_time.desc())
            .all()
        )
        customer_rows = [{
            "id": p.id,
            "purchase_time": p.purchase_time,
            "total_amount": p.total_amount,
            "paid_amount": p.paid_amount,
            "balance": p.balance,
            "items_count": len(p.items),
        } for p in purchases]
        customer_total_revenue = round(sum(p.total_amount for p in purchases), 2)

    return templates.TemplateResponse("customers.html", {
        "request": request,
        "customers": customers,
        "selected_customer": selected_customer,
        "customer_rows": customer_rows,
        "customer_total_revenue": customer_total_revenue
    })

@app.get("/products", response_class=HTMLResponse)
async def view_products(
    request: Request,
    db: Session = Depends(get_db),
    product: Optional[str] = Query(default=None),
):
    """
    /products
      - No query: list unique products with Orders count and Total Qty
      - ?product=eraser : show purchases that contain that product + totals
    """

    # --- Product list (unique, with aggregates) ---
    prod_norm = func.lower(func.trim(PurchaseItem.product_name)).label("product")
    products = (
        db.query(
            prod_norm,
            func.count(func.distinct(PurchaseItem.purchase_id)).label("orders"),
            func.coalesce(func.sum(PurchaseItem.quantity), 0).label("total_qty"),
        )
        .select_from(PurchaseItem)
        .join(Purchase, Purchase.id == PurchaseItem.purchase_id)
        .group_by(prod_norm)
        .order_by(func.count(func.distinct(PurchaseItem.purchase_id)).desc(), prod_norm.asc())
        .all()
    )

    # --- If a product is selected, fetch detailed purchases containing it ---
    selected_product = None
    product_rows = []
    product_total_qty = 0
    product_total_revenue = 0.0

    if product:
        selected_product = product.strip().lower()

        # All purchases that contain the selected product
        matching_purchases = (
            db.query(Purchase)
            .join(Purchase.items)  # Purchase -> PurchaseItem relationship
            .filter(func.lower(func.trim(PurchaseItem.product_name)) == selected_product)
            .order_by(Purchase.purchase_time.desc())
            .all()
        )

        # Build per-purchase rows, but only for the selected product lines
        for pur in matching_purchases:
            focus_items = [
                it for it in pur.items
                if (it.product_name or "").strip().lower() == selected_product
            ]

            qty_sum_pur = sum(int(it.quantity or 0) for it in focus_items)

            rev_sum_pur = 0.0
            for it in focus_items:
                q = float(it.quantity or 0)
                unit = float(it.price_per_unit or 0.0)
                tax = float(it.tax_percentage or 0.0)
                rev_sum_pur += q * unit * (1.0 + tax / 100.0)

            product_rows.append({
                "id": pur.id,
                "purchase_time": pur.purchase_time,
                "qty_for_product": qty_sum_pur,
                "revenue_for_product": round(rev_sum_pur, 2),
                "total_amount_bill": pur.total_amount,  # full bill amount
                "paid_amount": pur.paid_amount,
                "balance": pur.balance,
                "items_count": len(pur.items),
            })

            product_total_qty += qty_sum_pur
            product_total_revenue += rev_sum_pur

        product_total_revenue = round(product_total_revenue, 2)

    return templates.TemplateResponse("products.html", {
        "request": request,
        "products": products,                          # for top table
        "selected_product": selected_product,          # None or normalized name
        "product_rows": product_rows,                  # detail table rows
        "product_total_qty": product_total_qty,        # totals for selected product
        "product_total_revenue": product_total_revenue
    })


@app.get("/purchase/{purchase_id}", response_class=HTMLResponse)
async def purchase_detail(purchase_id: int, request: Request, db: Session = Depends(get_db)):
    purchase = db.query(Purchase).filter(Purchase.id == purchase_id).first()
    if not purchase:
        return HTMLResponse("Purchase not found", status_code=404)

    items = purchase.items
    line_items = []
    subtotal_before_tax = 0.0
    total_tax = 0.0
    for i in items:
        unit = i.product.price_per_unit
        qty = i.quantity
        tax_pct = i.product.tax_percentage
        line_sub = unit * qty
        line_tax = line_sub * (tax_pct / 100.0)
        subtotal_before_tax += line_sub
        total_tax += line_tax
        line_items.append({
            "product_name": i.product.name,
            "product_code": i.product.product_id,
            "quantity": qty,
            "unit_price": unit,
            "tax_percent": tax_pct,
            "subtotal": round(line_sub, 2),
            "tax_amount": round(line_tax, 2),
            "line_total": round(line_sub + line_tax, 2),
        })

    return templates.TemplateResponse("purchase_detail.html", {
        "request": request,
        "purchase": purchase,
        "items": items,
        "line_items": line_items,
        "subtotal_before_tax": round(subtotal_before_tax, 2),
        "total_tax": round(total_tax, 2),
    })

############# Application Run Command #############
# uvicorn app:app --reload --host 0.0.0.0 --port 8000
