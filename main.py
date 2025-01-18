from functools import lru_cache
from typing import Dict, Any

from fastapi import FastAPI, Request, Form, status, BackgroundTasks
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr
import os
import smtplib
from email.message import EmailMessage
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

class EmailConfig(BaseModel):
    sender_email: str = os.getenv("HOST_EMAIL")
    receiver_email: str = os.getenv("HOST_EMAIL") 
    password: str = os.getenv("HOST_PASSWORD")
    smtp_server: str = 'mail.privateemail.com'
    port: int = 465

@lru_cache()
def get_email_config() -> EmailConfig:
    return EmailConfig()

def create_html_content(data: Dict[str, Any]) -> str:
    return f"""
    <html>
    <body>
        <h2>Form Submission</h2>
        {''.join(f'<p><b>{k.title()}:</b> {v}</p>' for k, v in data.items())}
    </body>
    </html>
    """

def send_email(config: EmailConfig, subject: str, content: str):
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"Broadway Lounge <{config.sender_email}>"
    message["To"] = config.receiver_email
    message.set_content(content, subtype='html')

    with smtplib.SMTP_SSL(config.smtp_server, config.port) as server:
        server.login(config.sender_email, config.password)
        server.send_message(message)

@app.get("/")
@app.get("/reservations")
@app.get("/about")
@app.get("/bar")
@app.get("/contact")
@app.get("/gallery")
@app.get("/philex-index")
async def render_page(request: Request):
    template = request.url.path.strip("/") or "index"
    return templates.TemplateResponse(f"{template}.html", {"request": request})

@app.post("/contact-us")
async def contact_form(
    background_tasks: BackgroundTasks,
    name: str = Form(...),
    email: EmailStr = Form(...),
    message: str = Form(...)
):
    
    print(f"name: {name}, email: {email}, message: {message}")
    """Handle contact form submissions from contact.html"""
    config = get_email_config()
    subject = f"Contact Form: {name}"
    content = create_html_content({
        "name": name,
        "email": email, 
        "message": message
    })
    background_tasks.add_task(send_email, config, subject, content)
    return RedirectResponse(url="/contact", status_code=status.HTTP_302_FOUND)

@app.post("/reserve-table") 
async def reserve_table(
    background_tasks: BackgroundTasks,
    partysize: int = Form(...),
    date: str = Form(...),
    time: str = Form(...),
    datetime: str = Form(None)
):
    
    print(f"partysize: {partysize}, date: {date}, time: {time}, datetime: {datetime}")
    """Handle table reservation form submissions from bar.html"""
    config = get_email_config()
    
    # Format date string if needed
    try:
        date_obj = datetime.strptime(date, "%d/%m/%Y")
        formatted_date = date_obj.strftime("%B %d, %Y")
    except:
        formatted_date = date

    subject = f"Table Reservation Request"
    content = create_html_content({
        "Party Size": partysize,
        "Date": formatted_date,
        "Time": time
    })
    background_tasks.add_task(send_email, config, subject, content)
    return RedirectResponse(url="/bar", status_code=status.HTTP_302_FOUND)

@app.middleware("http")
async def add_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.update({
        "X-XSS-Protection": "1; mode=block",
        "X-Content-Type-Options": "nosniff", 
        "Cache-Control": "public, max-age=1200" if response.status_code == 200 else "no-store"
    })
    return response

@app.middleware("http")
async def fix_mime_type(request: Request, call_next):
    response = await call_next(request)
    content_types = {".ttf": "font/ttf", ".woff": "font/woff", ".woff2": "font/woff2"}
    ext = os.path.splitext(request.url.path)[1]
    if ext in content_types:
        response.headers["Content-Type"] = content_types[ext]
    return response