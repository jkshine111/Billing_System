# mail_notification.py
import os
import ssl
import smtplib
from email.message import EmailMessage
from sqlalchemy.orm import Session
from database import SessionLocal
from models import Purchase

# ---- Config Email ----
SMTP_HOST="smtp.gmail.com"
SMTP_PORT="465" #SSL
SMTP_PASS="enter your google app password"
SMTP_FROM="yourmail@gmail.com"
USE_SSL="true"

def _build_line_items(purchase):
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
    return line_items, round(subtotal_before_tax, 2), round(total_tax, 2)

def _render_invoice_html(purchase, line_items, subtotal_before_tax, total_tax):
    net = subtotal_before_tax + total_tax
    rows = "".join(
        f"""
        <tr>
          <td>{r['product_code']}</td>
          <td>{r['unit_price']:.2f}</td>
          <td>{r['quantity']}</td>
          <td>{r['subtotal']:.2f}</td>
          <td>{r['tax_percent']:.2f}</td>
          <td>{r['tax_amount']:.2f}</td>
          <td>{r['line_total']:.2f}</td>
        </tr>
        """ for r in line_items
    )
    return f"""
    <h2>Invoice #{purchase.id}</h2>
    <p>Customer: {purchase.customer_email}<br>
       Date: {purchase.purchase_time.strftime("%Y-%m-%d %H:%M")}</p>
    <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;">
      <thead>
        <tr>
          <th>Product ID</th><th>Unit Price</th><th>Qty</th>
          <th>Purchase Price</th><th>Tax %</th><th>Tax Amount</th><th>Total</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
    <p>Total (no tax): {subtotal_before_tax:.2f}<br>
       Tax: {total_tax:.2f}<br>
       Net: {net:.2f}<br>
       Paid: {purchase.paid_amount:.2f}<br>
       Balance: {purchase.balance:.2f}</p>
    <p>Thank you for your purchase!</p>
    """

def _send_smtp_email(to_email: str, subject: str, html_body: str):
    msg = EmailMessage()
    msg["From"] = SMTP_FROM
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content("Please view the invoice in HTML format.")
    msg.add_alternative(html_body, subtype="html")

    if USE_SSL:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context, timeout=30) as server:
            if SMTP_FROM and SMTP_PASS:
                server.login(SMTP_FROM, SMTP_PASS)
            server.send_message(msg)
    else:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
            if SMTP_FROM and SMTP_PASS:
                server.login(SMTP_FROM, SMTP_PASS)
            server.send_message(msg)

def send_invoice_email(to_email: str, purchase_id: int):
    """Public function to send invoice email by purchase_id"""
    db: Session = SessionLocal()
    try:
        purchase = db.query(Purchase).filter(Purchase.id == purchase_id).first()
        if not purchase:
            print(f"[EMAIL] Purchase #{purchase_id} not found.")
            return
        line_items, sub, tax = _build_line_items(purchase)
        html = _render_invoice_html(purchase, line_items, sub, tax)
        _send_smtp_email(to_email, f"Invoice #{purchase.id}", html)
        print(f"[EMAIL] Invoice sent to {to_email} for purchase #{purchase_id}")
    except Exception as e:
        print(f"[EMAIL][ERROR]: {e}")
    finally:
        db.close()
