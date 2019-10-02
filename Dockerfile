FROM python:3.6-slim

RUN apt-get update && \
    apt-get install -y \
    libmagickwand-dev

COPY requirements.txt .

RUN pip install -r requirements.txt

RUN mkdir /images
RUN mkdir /cache

EXPOSE 5000

COPY app /app

WORKDIR /app

CMD gunicorn --bind 0.0.0.0:5000 wsgi:app --access-logfile -
