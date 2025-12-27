#!/bin/bash
nginx
uvicorn app:app --uds imgpush.sock --access-log
