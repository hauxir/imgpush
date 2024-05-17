FROM python:3.12.3-slim

RUN rm -f /app/imgpush.sock && \
    apt-get update && \
    apt-get install -y \
    libmagickwand-dev curl \
    nginx && \
    mkdir /files /cache

COPY requirements.txt .
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY ImageMagick-6/policy.xml /etc/ImageMagick-6/policy.xml

RUN pip install -r requirements.txt

EXPOSE 5000

COPY app /app

WORKDIR /app

CMD bash entrypoint.sh
