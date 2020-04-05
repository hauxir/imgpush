FROM python:3.6-slim

RUN apt-get update && \
    apt-get install -y \
    libmagickwand-dev curl \
    nginx

COPY requirements.txt .
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY ImageMagick-6/policy.xml /etc/ImageMagick-6/policy.xml

RUN pip install -r requirements.txt

RUN mkdir /images
RUN mkdir /cache

EXPOSE 5000

COPY app /app

WORKDIR /app

CMD bash entrypoint.sh
