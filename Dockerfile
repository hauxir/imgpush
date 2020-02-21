FROM python:3.6-slim

RUN apt-get update && \
    apt-get install -y \
    libmagickwand-dev curl

COPY requirements.txt .
COPY ImageMagick-6/policy.xml /etc/ImageMagick-6/policy.xml

RUN pip install -r requirements.txt

RUN mkdir /images
RUN mkdir /cache

EXPOSE 5000

COPY app /app

WORKDIR /app

HEALTHCHECK CMD curl http://localhost:5000/liveness -s -f -o /dev/null || exit 1
CMD gunicorn --bind 0.0.0.0:5000 wsgi:app --access-logfile -
