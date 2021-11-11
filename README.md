<img width="246" alt="Screenshot 2019-06-19 at 17 56 29" src="https://user-images.githubusercontent.com/2439255/59781204-a23da780-92bb-11e9-99c5-490feecca557.png">
Minimalist Self-hosted Image Service for user submitted images in your app (e.g. avatars).

## Features
- One simple API endpoint for uploading images
- Automatic Conversion to an image format of your choice
- Automatic resizing to any size of your liking
- Built-in Rate limiting
- Built-in Allowed Origin whitelisting
- Liveness API 

## Usage
Uploading an image:
```bash
> curl -F 'file=@/some/file.jpg' http://some.host
{"filename":"somename.png"}
```
Uploading an image by URL:
```bash
 curl -X POST -H "Content-Type: application/json" -d '{"url": "<SOME_URL>"}'  http://some.host
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

### Kubernetes

> This is fully optional and is only needed if you want to run imgpush in Kubernetes.

If you want to deploy imgpush in Kubernetes, there is an example deployment available in the Kubernetes directory.
In case you do not have a running Kubernetes cluster yet, you can use [Minikube](https://kubernetes.io/docs/setup/) to setup a local single-node Kubernetes cluster.
Otherwise you can just use your existing cluster.

1. Verify that your cluster works:
```
$ kubectl get pods
# Should return without an error, maybe prints information about some deployed pods.
```

2. Apply the `kubernetes/deployment-example.yaml` file:
```
$ kubectl apply -f kubernetes/deployment-example.yaml
namespace/imgpush created
deployment.apps/imgpush created
persistentvolumeclaim/imgpush created
service/imgpush created
```

3. It will take a moment while your Kubernetes downloads the current imgpush image.
4. Verify that the deployment was successful:
```
$ kubectl -n imgpush get deployments.
NAME      READY   UP-TO-DATE   AVAILABLE   AGE
imgpush   1/1     1            1           3m41s

$ kubectl -n imgpush get svc
NAME      TYPE        CLUSTER-IP    EXTERNAL-IP   PORT(S)    AGE
imgpush   ClusterIP   10.10.10.41   <none>        5000/TCP   3m57s
```

5. When the deployment is finished, the READY column should be `1/1`.
6. Afterwards you can forward the port to your local machine and upload an image via your webbrowser (visit http://127.0.0.1:5000/).
```
$ kubectl -n imgpush port-forward service/imgpush 5000
Forwarding from 127.0.0.1:5000 -> 5000
Handling connection for 5000
Handling connection for 5000
Handling connection for 5000
Handling connection for 5000
```

7. To expose imgpush to the internet you need to configure an [Ingress](https://kubernetes.io/docs/concepts/services-networking/ingress/). The exact configuration depends on you cluster but you can find an example in the `kubernetes/deployment-example.yaml` file that you can adapt to your setup.


### Liveness

imgpush provides the `/liveness` endpoint that always returns `200 OK` that you can use for docker Healthcheck and kubernetes liveness probe. 

For Docker, as `curl` is install in the image : 

```yaml
healthcheck:
    start_period: 0s
    test: ['CMD-SHELL', 'curl localhost:5000/liveness -s -f -o /dev/null || exit 1']
    interval: 30s
```

For Kubernetes
```yaml
livenessProbe:
    httpGet:
    path: /liveness
    port: 5000            
    initialDelaySeconds: 5
    periodSeconds: 30
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
| NAME_STRATEGY  | "randomstr"  | `randomstr` for random 5 chars, `uuidv4` for UUIDv4 |
| NUDE_FILTER_MAX_THRESHOLD  | None  | max unsafe value returned from nudenet library(https://github.com/notAI-tech/NudeNet), range is from 0-0.99. Blocks nudity from being uploaded. |

Setting configuration variables is all set through env variables that get passed to the docker container.
### Example:
```
docker run -e ALLOWED_ORIGINS="['https://a.com', 'https://b.com']" -s -v <PATH TO STORE IMAGES>:/images -p 5000:5000 hauxir/imgpush:latest
```
or to quickly deploy it locally, run
```
docker-compose up -d
```
