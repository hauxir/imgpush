<img width="246" alt="Screenshot 2019-06-19 at 17 56 29" src="https://user-images.githubusercontent.com/2439255/59781204-a23da780-92bb-11e9-99c5-490feecca557.png">
Minimalist Self-hosted Image Service for user submitted images in your app (e.g. avatars).

## Features
- One simple API endpoint for uploading images
- Automatic Conversion to an image format of your choice
- Automatic resizing to any size of your liking
- Built-in Rate limiting
- Built-in Allowed Origin whitelisting

## Usage
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
| Setting  | Default value | Description |
| ------------- | ------------- |------------- |
| OUTPUT_TYPE  | Same as Input file | An image type supported by imagemagick, e.g. png or jpg |
| MAX_SIZE_MB  | "16"  | Integer, Max size per uploaded file in megabytes |
| MAX_UPLOADS_PER_DAY  | "1000"  | Integer, max per IP address |
| MAX_UPLOADS_PER_HOUR  | "100"  | Integer, max per IP address |
| MAX_UPLOADS_PER_MINUTE  | "20"  | Integer, max per IP address |
| ALLOWED_ORIGINS  | "['*']"  | array of domains, e.g ['https://a.com'] |
| VALID_SIZES  | Any size  | array of integers allowed in the h= and w= parameters, e.g "[100,200,300]". You should set this to protect against being bombarded with requests! |

Setting configuration variables is all set through env variables that get passed to the docker container.
### Example:
```
docker run -e ALLOWED_ORIGINS="['https://a.com', 'https://b.com']" -s -v <PATH TO STORE IMAGES>:/images -p 5000:5000 hauxir/imgpush:latest
```
or to quickly deploy it locally, run
```
docker-compose up -d
```
