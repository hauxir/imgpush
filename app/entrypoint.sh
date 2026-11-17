#!/bin/bash

WORKERS="${WORKERS:-$(getconf _NPROCESSORS_ONLN)}"

# Force single-threaded ONNX to prevent process spawning
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1
export NUMEXPR_NUM_THREADS=1

# Prevent glibc malloc fragmentation from accumulating over time.
# Forces allocations >64KB through mmap() instead of brk(), so memory
# is returned to the OS immediately on free() rather than fragmenting the heap.
export MALLOC_MMAP_THRESHOLD_=65536
export MALLOC_TRIM_THRESHOLD_=131072

# TRUSTED_PROXIES: addresses or CIDR ranges (comma or space separated) of the
# reverse proxies in front of this container. Only requests from those may say
# who the real client is, via X-Forwarded-For; from anyone else the header is
# ignored, so clients can't pick the address the per-IP limits count against.
mkdir -p /etc/nginx/imgpush.d
: > /etc/nginx/imgpush.d/realip.conf
if [ -n "${TRUSTED_PROXIES:-}" ]; then
    for proxy in ${TRUSTED_PROXIES//,/ }; do
        if ! [[ "$proxy" =~ ^[0-9a-fA-F:.]+(/[0-9]{1,3})?$ ]]; then
            echo "TRUSTED_PROXIES: '$proxy' is not an IP address or CIDR range" >&2
            exit 1
        fi
        echo "set_real_ip_from $proxy;" >> /etc/nginx/imgpush.d/realip.conf
    done
    {
        echo "real_ip_header X-Forwarded-For;"
        echo "real_ip_recursive on;"
    } >> /etc/nginx/imgpush.d/realip.conf
fi

echo "Starting uvicorn with $WORKERS workers"

nginx
# Only nginx can reach the unix socket, and it always sets X-Forwarded-For
# itself, so the header is trusted from every peer on it.
uvicorn app:app --uds imgpush.sock --access-log --workers "$WORKERS" --limit-max-requests 200 --forwarded-allow-ips '*'

