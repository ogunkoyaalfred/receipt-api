FROM python:3.11-slim

# Install Tesseract and English language data
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-eng \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "app:app"]
```

And your `requirements.txt` should have:
```
flask
flask-cors
pillow
pytesseract
gunicorn
```

And your `Procfile`:
```
web: gunicorn --bind 0.0.0.0:8080 app:app