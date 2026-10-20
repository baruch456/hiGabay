FROM python:3.11-slim

WORKDIR /app
COPY . /app

RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Render routes public traffic to the port in PORT. Keep the API internal.
CMD ["bash", "-c", "uvicorn app:app --host 127.0.0.1 --port=8001 & streamlit run streamlit_app.py --server.address=0.0.0.0 --server.port=${PORT:-10000} --server.enableCORS=false --server.enableXsrfProtection=false"]
