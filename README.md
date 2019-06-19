# Img Push
Minimalist Self-hosted Image Service.

## Features
- One simple API endpoint for uploading images
- Automatic Conversion to an image format of your choice
- Automatic resizing to any size of your liking
- Built-in Rate limiting
- Built-in Allowed Origin whitelisting

## Usage:
Uploading an image:
```bash
> curl -F 'file=@/some/file.jpg' http://some.host
{"filename":"somename.png"}
```
Fetching a file in a specific size(e.g. 320x240):
```
http://some.host/somename.png?w=320&h=240
```
returns the image cropped to the desired size

## Running
imgpush requires docker

```bash
docker run -v <PATH TO STORE IMAGES>:/images -p 5000:5000 hauxir/imgpush:latest
```

## Configuration
| Setting  | Default value | Type |
| ------------- | ------------- |------------- |
| OUTPUT_TYPE  | "png"  | An image type supported by imagemagick, e.g. png or jpg |
| MAX_UPLOADS_PER_DAY  | "1000"  | Integer |
| MAX_UPLOADS_PER_HOUR  | "100"  | Integer |
| MAX_UPLOADS_PER_MINUTE  | "20"  | Integer |
| ALLOWED_ORIGINS  | "['*']"  | array of domains, e.g ['https://a.com'] |
| VALID_SIZES  | Any size  | array of integers allowed in the h= and w= parameters, e.g "[100,200,300]". You should set this to protect against being bombarded with requests! |

Setting configuration variables is all set through env variables that get passed to the docker container.
### Example:
```
docker run -e ALLOWED_ORIGINS="['https://a.com', 'https://b.com']" -s -v <PATH TO STORE IMAGES>:/images -p 5000:5000 hauxir/imgpush:latest
```
