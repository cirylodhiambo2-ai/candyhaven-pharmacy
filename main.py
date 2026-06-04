from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
from typing import List, Optional
import requests
import base64
import os
import jwt
from datetime import datetime, timedelta
from passlib.context import CryptContext
from dotenv import load_dotenv
from database import get_db_connection, init_db

# 1. LOAD CONFIGURATIONS FROM YOUR .ENV FILE FIRST
load_dotenv()

# 2. INITIALIZE THE CORE FASTAPI APP INTERFACE
app = FastAPI(title="Candyhaven Pharmacy Management API")

# 3. CONFIGURE SECURITY CORS HEADERS FOR BROWSER ACCESS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 4. ENCRYPTION & SECURITY TOKEN CONSTANTS
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "fallback-secret-string-123456")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 480  # Keeps the terminal unlocked for an 8-hour shift

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

# 5. MPESA POCHI LA BIASHARA CORNERSTONE VARIABLES
POCHI_PHONE = "254727327268"  # Formatted properly for Safaricom's network infrastructure
MPESA_CONSUMER_KEY = os.getenv("MPESA_CONSUMER_KEY")
MPESA_CONSUMER_SECRET = os.getenv("MPESA_CONSUMER_SECRET")
MPESA_SHORTCODE = "174379"    # Standard Daraja Sandbox shortcode for runtime validation
MPESA_PASSKEY = "bfb279f9aa9bdbcf158e97dd71a467cd2e0c893059b10f78e6b72ada1ed2c919"

# 6. SECURED OPERATOR CREDENTIALS (Stored as plain text for simple mock comparison)
USER_DB = {
    "admin": "candyhaven2026"
}

# 7. AUTHENTICATION & SECURITY HELPER FUNCTIONS
def verify_token(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session token.")
        return username
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate active session.")

def get_mpesa_password():
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    data_to_encode = MPESA_SHORTCODE + MPESA_PASSKEY + timestamp
    encoded_string = base64.b64encode(data_to_encode.encode())
    return encoded_string.decode('utf-8'), timestamp

# 8. DATA TRANSFER MODELS (PYDANTIC STRUCTS)
class MedicineCreate(BaseModel):
    name: str
    category: str
    stock_quantity: int
    buying_price: float
    selling_price: float
    expiry_date: str
    batch_number: Optional[str] = "N/A"

class CartItem(BaseModel):
    medicine_id: int
    quantity: int

class SaleCreate(BaseModel):
    items: List[CartItem]

# 9. ENGINE STARTUP INITIALIZATION HOOK
@app.on_event("startup")
def startup_event():
    init_db()

# 10. AUTHENTICATION ROUTE (GENERATES SECURE OPERATOR ACCESS TOKENS)
@app.post("/token")
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    correct_password = USER_DB.get(form_data.username)
    # Direct matching avoids the 72-byte bcrypt error
    if not correct_password or form_data.password != correct_password:
        raise HTTPException(status_code=400, detail="Incorrect username or secure password.")
    
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = jwt.encode(
        {"sub": form_data.username, "exp": datetime.utcnow() + access_token_expires},
        SECRET_KEY, algorithm=ALGORITHM
    )
    return {"access_token": access_token, "token_type": "bearer"}
    
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = jwt.encode(
        {"sub": form_data.username, "exp": datetime.utcnow() + access_token_expires},
        SECRET_KEY, algorithm=ALGORITHM
    )
    return {"access_token": access_token, "token_type": "bearer"}

# 11. SECURED INVENTORY MANAGEMENT ENDPOINTS
@app.get("/medicines")
def get_medicines(current_user: str = Depends(verify_token)):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM medicines")
    medicines = cursor.fetchall()
    conn.close()
    return [dict(row) for row in medicines]

@app.post("/medicines")
def add_medicine(medicine: MedicineCreate, current_user: str = Depends(verify_token)):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT INTO medicines (name, category, stock_quantity, buying_price, selling_price, expiry_date, batch_number)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (medicine.name, medicine.category, medicine.stock_quantity, medicine.buying_price, medicine.selling_price, medicine.expiry_date, medicine.batch_number))
        conn.commit()
        return {"status": "success", "message": f"{medicine.name} safely logged to stock."}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()

# 12. SECURED SALES TRANSACTION PROCESSOR
@app.post("/sales")
def process_sale(sale: SaleCreate, current_user: str = Depends(verify_token)):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        total_amount = 0.0
        for item in sale.items:
            cursor.execute("SELECT stock_quantity, selling_price FROM medicines WHERE medicine_id = ?", (item.medicine_id,))
            med = cursor.fetchone()
            if not med:
                raise HTTPException(status_code=404, detail=f"Item ID {item.medicine_id} not found.")
            if med["stock_quantity"] < item.quantity:
                raise HTTPException(status_code=400, detail=f"Insufficient stock balance.")
            total_amount += med["selling_price"] * item.quantity

        cursor.execute("INSERT INTO sales (total_amount) VALUES (?)", (total_amount,))
        sale_id = cursor.lastrowid

        for item in sale.items:
            cursor.execute("SELECT selling_price FROM medicines WHERE medicine_id = ?", (item.medicine_id,))
            med = cursor.fetchone()
            
            cursor.execute('''
                INSERT INTO sale_items (sale_id, medicine_id, quantity, price_per_unit)
                VALUES (?, ?, ?, ?)
            ''', (sale_id, item.medicine_id, item.quantity, med["selling_price"]))
            
            cursor.execute('''
                UPDATE medicines 
                SET stock_quantity = stock_quantity - ? 
                WHERE medicine_id = ?
            ''', (item.quantity, item.medicine_id))

        conn.commit()
        return {"status": "success", "total_charged": total_amount}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

# 13. SECURED BUSINESS INTELLIGENCE & AUDIT HUB
@app.get("/sales/history")
def get_sales_history(current_user: str = Depends(verify_token)):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('''
            SELECT si.sale_id, m.name, si.quantity, si.price_per_unit, (si.quantity * si.price_per_unit) as total_item_price, s.sale_date
            FROM sale_items si
            JOIN medicines m ON si.medicine_id = m.medicine_id
            JOIN sales s ON si.sale_id = s.sale_id
            ORDER BY s.sale_id DESC
        ''')
        history_rows = cursor.fetchall()
        
        cursor.execute('''
            SELECT SUM((m.selling_price - m.buying_price) * si.quantity) as profit, SUM(s.total_amount) as revenue
            FROM sale_items si JOIN medicines m ON si.medicine_id = m.medicine_id JOIN sales s ON si.sale_id = s.sale_id
        ''')
        lifetime = cursor.fetchone()

        cursor.execute('''
            SELECT SUM((m.selling_price - m.buying_price) * si.quantity) as profit, SUM(s.total_amount) as revenue
            FROM sale_items si JOIN medicines m ON si.medicine_id = m.medicine_id JOIN sales s ON si.sale_id = s.sale_id
            WHERE date(s.sale_date) = date('now', 'localtime')
        ''')
        daily = cursor.fetchone()

        cursor.execute('''
            SELECT SUM((m.selling_price - m.buying_price) * si.quantity) as profit, SUM(s.total_amount) as revenue
            FROM sale_items si JOIN medicines m ON si.medicine_id = m.medicine_id JOIN sales s ON si.sale_id = s.sale_id
            WHERE date(s.sale_date) >= date('now', '-7 days', 'localtime')
        ''')
        weekly = cursor.fetchone()
        
        return {
            "history": [dict(row) for row in history_rows],
            "lifetime_revenue": lifetime["revenue"] or 0.0,
            "lifetime_profit": lifetime["profit"] or 0.0,
            "daily_revenue": daily["revenue"] or 0.0,
            "daily_profit": daily["profit"] or 0.0,
            "weekly_revenue": weekly["revenue"] or 0.0,
            "weekly_profit": weekly["profit"] or 0.0
        }
    finally:
        conn.close()

# 14. SECURED SAFARICOM MPESA GATEWAY GATE
@app.post("/mpesa/stkpush")
def initiate_stk_push(phone: str, amount: int, current_user: str = Depends(verify_token)):
    # Request secure OAuth credential tokens from Safaricom endpoints
    api_url = "https://sandbox.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials"
    token_res = requests.get(api_url, auth=(MPESA_CONSUMER_KEY, MPESA_CONSUMER_SECRET))
    token = token_res.json().get("access_token")
    
    stk_url = "https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    password, timestamp = get_mpesa_password()
    
    if phone.startswith("0"):
        phone = "254" + phone[1:]
    elif phone.startswith("+254"):
        phone = phone[1:]

    payload = {
        "BusinessShortCode": MPESA_SHORTCODE,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": "CustomerBuyGoodsOnline",  # Maps processing parameters for Pochi accounts
        "Amount": amount,
        "PartyA": phone,
        "PartyB": POCHI_PHONE,                         # Routes execution target safely to your mom's number
        "PhoneNumber": phone,
        "CallBackURL": "https://your-public-domain.com/mpesa/callback",
        "AccountReference": "Candyhaven",
        "TransactionDesc": "Pharmacy Pochi Checkout"
    }
    response = requests.post(stk_url, json=payload, headers=headers)
    return response.json()

# 15. STATIC PANEL UI ROUTE MOUNT (LEAVE THIS AT THE VERY BOTTOM OF THE SCRIPT)
app.mount("/", StaticFiles(directory="static", html=True), name="static")