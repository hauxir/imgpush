#!/bin/bash
nginx
gunicorn --bind unix:imgpush.sock wsgi:app --access-logfile -
