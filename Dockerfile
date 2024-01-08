FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y \
    libmagickwand-dev curl \
    nginx

COPY requirements.txt .
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY ImageMagick-6/policy.xml /etc/ImageMagick-6/policy.xml

RUN pip install -r requirements.txt

COPY classifier_model.onnx /root/.NudeNet/classifier_model.onnx

RUN mkdir /images
RUN mkdir /cache

EXPOSE 5000

COPY app /app

WORKDIR /app

CMD bash entrypoint.sh
