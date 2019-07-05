FROM python:3.6-slim

RUN apt-get update && \
    apt-get install -y \
    libmagickwand-dev

RUN pip install flask
RUN pip install flask-cors
RUN pip install Flask-Limiter
RUN pip install Wand
RUN pip install filetype
RUN pip install gunicorn

RUN mkdir /images
RUN mkdir /cache

EXPOSE 5000

COPY app /app

WORKDIR /app

CMD gunicorn --bind 0.0.0.0:5000 wsgi:app --access-logfile -
