FROM python:3.6-slim

RUN apt-get update && \
    apt-get install -y \
    libmagickwand-dev curl

COPY requirements.txt .

RUN pip install -r requirements.txt

RUN mkdir /images
RUN mkdir /cache

EXPOSE 5000

COPY kubernetes/policy.xml /etc/ImageMagick-6/policy.xml
COPY app /app

WORKDIR /app

CMD gunicorn --bind 0.0.0.0:5000 wsgi:app --access-logfile -
